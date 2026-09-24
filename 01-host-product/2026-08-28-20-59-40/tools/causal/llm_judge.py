#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""llm_judge.py — M1-10 LLM 判分层：逐题语义判定三要素(cause/effect/evidence_hint)+可用性。

输入: answers-llm.jsonl + gold.jsonl
输出: <out>/scores-llm-judge.json（与 eval_m1 scores 兼容的核心指标 + 逐题明细）
限速: 题间默认 16s;空响应/非法 JSON 最多额外请求一次;传输重试由 chat() 独立负责。
断点只复用合法成功判分;失败记录保留诊断并在下次运行重判，不改变 gold 分母。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_answer import chat, provider_conf

GAP_SECONDS = int(os.environ.get("LLM_GAP", "16"))


def judge_prompt(question, gold, answer_text):
    relax = os.environ.get("LLM_JUDGE_CAUSE_RELAX") == "1"
    rubric = (
        "你是评测判分器。根据【标准答案】对【被评答案】做三要素语义判定。\n"
        "判定规则：\n"
        "- cause：标准答案可能含多个子原因——被评答案命中其中任何一个实质性原因即判 true（允许措辞不同）。\n"
    )
    if relax:  # 宽松口径诊断: gold cause 若为泛化背景式描述,同链真实前置事件也算命中
        rubric += (
            "- cause(宽松补充)：当标准答案的原因本身是笼统背景（如'来自某群聊/某类调整'、无明显实质动因）时，"
            "被评答案给出的原因若是一个真实存在、时间早于结果事件、且与该结果同属一条事实链的实质前置事件/决策"
            "（即便与标准答案措辞完全不同），也判 true。\n"
        )
    rubric += (
        "- effect：命中核心结果/后续动作即 true（允许措辞不同、粒度略粗，不要求覆盖全部细节）。\n"
        "- evidence_hint：答案引用了与标准答案指向同一事件、或同一事实链中的相关真实事件（时间+主体+对象对得上）即 true；"
        "完全没引用证据、或引用了无关事件才判 false。\n"
        "- 编造事实（标准答案中没有的实质性内容）才算 false；表达保守/信息量少不扣 true。\n"
        "- 无法确认时判 false。\n"
        f"问题：{question}\n"
        f"标准答案-cause：{gold.get('cause', '')}\n"
        f"标准答案-effect：{gold.get('effect', '')}\n"
        f"标准答案-evidence_hint：{gold.get('evidence_hint', '')}\n"
        f"被评答案：\n{answer_text}\n"
        '只输出 JSON（不要其他文字）：{"cause": true/false, "effect": true/false, '
        '"evidence_hint": true/false, "available": true/false}\n'
        "available = 被评答案确实在回答该问题（非空话、非失败信息、非拒答）。"
    )
    return rubric


JUDGE_FIELDS = ("cause", "effect", "evidence_hint", "available")
MAX_PARSE_RETRIES = 1
RETRYABLE_RESPONSE_ERRORS = {"response_empty", "response_invalid_json"}
ERROR_MESSAGES = {
    "response_empty": "判分响应为空",
    "response_invalid_json": "判分响应不是合法 JSON",
    "response_schema_invalid": "判分响应结构或布尔字段类型不合法",
    "api_429": "判分服务限流；底层退避后仍未成功",
    "api_5xx": "判分服务 HTTP 5xx 错误",
    "api_4xx": "判分服务 HTTP 4xx 错误（非限流）",
    "api_timeout/network": "判分请求超时或网络连接失败",
    "api_unknown": "判分请求失败（未分类）",
    "answer_missing": "答案缺失或为空，未请求判分",
    "answer_failure": "答案标记管线失败，未请求判分",
    "answer_invalid": "答案文本不是字符串，未请求判分",
}


class JudgeResponseError(ValueError):
    def __init__(self, error_type):
        self.error_type = error_type
        super().__init__(ERROR_MESSAGES[error_type])


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(text):
    """Accept a complete object or fenced object, with strict boolean fields."""
    if text is None or (isinstance(text, str) and not text.strip()):
        raise JudgeResponseError("response_empty")
    if not isinstance(text, str):
        raise JudgeResponseError("response_schema_invalid")
    raw = text.strip()
    # Unwrap one complete fence, then parse its whole body, never an inner object.
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", raw, re.S | re.I)
    if fence is not None:
        raw = fence.group(1).strip()
        if not raw:
            raise JudgeResponseError("response_empty")
    try:
        d = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError) as exc:
        raise JudgeResponseError("response_invalid_json") from exc
    if not isinstance(d, dict) or any(type(d.get(k)) is not bool for k in JUDGE_FIELDS):
        raise JudgeResponseError("response_schema_invalid")
    return {k: d[k] for k in JUDGE_FIELDS}


def _http_status(exc):
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 400 <= code <= 599:
        return code
    match = re.search(r"\bHTTP(?:Error)?\s*[:(]?\s*([45]\d{2})\b", str(exc), re.I)
    return int(match.group(1)) if match else None


def classify_judge_error(exc):
    """Support typed exceptions and RuntimeError wrappers raised by llm_answer.chat."""
    if isinstance(exc, JudgeResponseError):
        return exc.error_type
    status = _http_status(exc)
    if status is not None:
        return "api_429" if status == 429 else ("api_5xx" if status >= 500 else "api_4xx")
    lower = str(exc).lower()
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)) or any(
        marker in lower for marker in (
            "timeout", "timed out", "urlerror", "connectionerror", "connection reset",
            "connection refused", "network is unreachable", "name or service not known",
            "getaddrinfo failed", "remotedisconnected", "incompleteread",
        )
    ):
        return "api_timeout/network"
    if "empty judge response" in lower or lower.strip() == "no json in:":
        return "response_empty"
    if isinstance(exc, json.JSONDecodeError) or "no json in:" in lower or "jsondecodeerror" in lower:
        return "response_invalid_json"
    if isinstance(exc, (KeyError, IndexError, AttributeError)) or any(
        marker in lower for marker in (
            "judge fields must be json booleans", "judge result must be a json object",
            "keyerror(", "indexerror(", "attributeerror(",
        )
    ):
        return "response_schema_invalid"
    return "api_unknown"


def _safe_error_text(exc):
    """Persist only fixed messages and HTTP status, never API bodies or answer text."""
    message = ERROR_MESSAGES[classify_judge_error(exc)]
    status = _http_status(exc)
    return f"HTTP {status}: {message}" if status is not None else message


def _judge_failure(error_type, attempts=0, attempt_errors=None, exc=None):
    return {
        **dict.fromkeys(JUDGE_FIELDS, False),
        "judge_ok": False,
        "judge_err": _safe_error_text(exc) if exc is not None else ERROR_MESSAGES[error_type],
        "error_type": error_type,
        "judge_attempts": attempts,
        "parse_retry_count": max(0, attempts - 1),
        "attempt_errors": list(attempt_errors or []),
        "unavailable_reason": "判分未完成：" + ERROR_MESSAGES[error_type],
    }


def judge_answer(prompt, max_tokens, parse_retries=MAX_PARSE_RETRIES):
    """Retry only judge-content parsing, never multiply transport-layer retries."""
    if type(parse_retries) is not int or not 0 <= parse_retries <= MAX_PARSE_RETRIES:
        raise ValueError("parse_retries must be 0 or 1")
    errors = []
    for attempt in range(1, parse_retries + 2):
        try:
            raw = chat([{"role": "user", "content": prompt}],
                       max_tokens=max_tokens, temperature=0.0)
        except Exception as exc:  # Transport already owns its retry policy.
            error_type = classify_judge_error(exc)
            errors.append(error_type)
            return _judge_failure(error_type, attempt, errors, exc)
        try:
            result = parse_json(raw)
        except JudgeResponseError as exc:
            errors.append(exc.error_type)
            if exc.error_type in RETRYABLE_RESPONSE_ERRORS and attempt <= parse_retries:
                time.sleep(GAP_SECONDS)
                continue
            return _judge_failure(exc.error_type, attempt, errors, exc)
        result.update({
            "judge_ok": True, "judge_err": "", "error_type": "",
            "judge_attempts": attempt, "parse_retry_count": attempt - 1,
            "attempt_errors": errors,
            "unavailable_reason": "" if result["available"] else "语义不可用：答案未有效回答问题",
        })
        return result


def _answer_error(answer):
    if answer is None:
        return "answer_missing"
    if "pipeline_ok" in answer and answer["pipeline_ok"] is not True:
        return "answer_failure"
    text = answer.get("answer_text")
    if text is None or (isinstance(text, str) and not text.strip()):
        return "answer_missing"
    return "" if isinstance(text, str) else "answer_invalid"


def _stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_fingerprint(qid, gold, answer, config):
    payload = {"qid": qid, "gold": gold, "answer": answer, "config": config}
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _load_rows(path, keys=()):
    """Read JSONL or a complete JSON result without guessing from the first byte."""
    with io.open(path, encoding="utf-8-sig") as f:
        raw = f.read().strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        rows = []
        for line in raw.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in keys:
            rows = obj.get(key)
            if isinstance(rows, list):
                return rows
        return [obj] if obj.get("qid") is not None else []
    raise ValueError("输入必须是 JSONL、JSON 数组或带条目数组的 JSON 对象")


def _load_checkpoint(path, expected):
    """Reuse only valid successful judgments matching the current input fingerprint."""
    if not os.path.exists(path):
        return {}
    done = {}
    try:
        for row in _load_rows(path, keys=("items", "results")):
            if not isinstance(row, dict) or row.get("qid") is None:
                continue
            qid = str(row["qid"])
            if (qid not in expected or row.get("input_fingerprint") != expected[qid]
                    or row.get("judge_ok") is not True
                    or any(type(row.get(k)) is not bool for k in JUDGE_FIELDS)
                    or row.get("error_type")):
                continue
            done[qid] = {**row, "qid": qid, "judge_err": "", "error_type": "",
                         "unavailable_reason": "" if row["available"]
                         else "语义不可用：答案未有效回答问题"}
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"[llm-judge] 忽略无效断点文件 ({type(exc).__name__})", file=sys.stderr, flush=True)
    return done


def _write_checkpoint(path, rows):
    """Atomically replace the JSONL checkpoint after each completed item."""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parse-retries", type=int, choices=(0, 1), default=MAX_PARSE_RETRIES,
                    help="空响应/非法 JSON 的额外尝试次数（默认 1，可设 0 控制配额）")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    answer_rows = _load_rows(args.answers, keys=("items", "answers", "results"))
    gold_rows = _load_rows(args.gold, keys=("items", "answers", "results"))
    answers = {}
    for a in answer_rows:
        if not isinstance(a, dict) or a.get("qid") is None:
            raise ValueError("answers 输入含无 qid 条目")
        qid = str(a["qid"])
        if qid in answers:
            raise ValueError("answers 输入存在重复 qid: " + qid)
        answers[qid] = a
    golds = {}
    for g in gold_rows:
        if not isinstance(g, dict) or g.get("qid") is None:
            raise ValueError("gold 输入含无 qid 条目")
        qid = str(g["qid"])
        if qid in golds:
            raise ValueError("gold 输入存在重复 qid: " + qid)
        golds[qid] = g
    conf = provider_conf()  # 提前校验凭证；只记录非敏感的有效配置。
    out_path = args.out
    max_tokens = int(os.environ.get("LLM_JUDGE_MAX_TOKENS", "4096"))
    judge_config = {
        "max_tokens": max_tokens,
        "provider": conf.get("provider", os.environ.get("LLM_PROVIDER", "doubao")),
        "model": conf.get("model", os.environ.get("LLM_MODEL", "")),
        "endpoint": conf.get("url", ""),
        "cause_relax": os.environ.get("LLM_JUDGE_CAUSE_RELAX", "0"),
    }
    input_errors = {qid: _answer_error(answers.get(qid)) for qid in golds}
    expected_fingerprints = {
        qid: input_fingerprint(
            qid, gold, answers.get(qid, {}).get("answer_text", ""),
            {**judge_config, "answer_state": input_errors[qid], "prompt": judge_prompt(
                gold.get("question") or answers.get(qid, {}).get("question", ""),
                gold, answers.get(qid, {}).get("answer_text", ""))},
        )
        for qid, gold in golds.items()
    }
    checkpoint_path = out_path + ".checkpoint.jsonl"
    checkpoint_source = checkpoint_path if os.path.exists(checkpoint_path) else out_path
    done = _load_checkpoint(checkpoint_source, expected_fingerprints)
    print(f"[llm-judge] {len(golds)} 题, 已判 {len(done)}", flush=True)

    items = []
    checkpoint_rows = dict(done)
    for qid in sorted(golds):
        if qid in done:
            # Refresh generation latency even when the semantic judgment can be reused.
            items.append({**done[qid], "latency_ms": answers.get(qid, {}).get("latency_ms", -1)})
            continue
        a = answers.get(qid, {})
        prompt = judge_prompt(golds[qid].get("question") or a.get("question", ""),
                              golds[qid], a.get("answer_text", ""))
        t0 = time.monotonic()
        if input_errors[qid]:
            v = _judge_failure(input_errors[qid])
        else:
            v = judge_answer(prompt, max_tokens, args.parse_retries)
        v.update({"qid": qid, "latency_ms": a.get("latency_ms", -1),
                  "judge_ms": int((time.monotonic() - t0) * 1000),
                  "input_fingerprint": expected_fingerprints[qid]})
        items.append(v)
        checkpoint_rows[qid] = v
        # Retain successful later-qid rows if this run is interrupted during an earlier retry.
        _write_checkpoint(checkpoint_path, [checkpoint_rows[k] for k in sorted(checkpoint_rows)])
        print(f"[{len(items)}/{len(golds)}] {qid}: cause={v['cause']} effect={v['effect']} "
              f"ev={v['evidence_hint']} avail={v['available']} judge_ok={v['judge_ok']} "
              f"error_type={v['error_type']} attempts={v['judge_attempts']}", flush=True)
        if v["judge_attempts"]:
            time.sleep(GAP_SECONDS)

    _write_checkpoint(checkpoint_path, items)
    n = len(items)
    counts = {
        "availability": sum(i.get("judge_ok") is True and i.get("available") is True for i in items),
        "causal_accuracy": sum(i.get("judge_ok") is True
                               and all(i.get(k) is True for k in JUDGE_FIELDS[:3]) for i in items),
        "cause_hit": sum(i.get("judge_ok") is True and i.get("cause") is True for i in items),
        "effect_hit": sum(i.get("judge_ok") is True and i.get("effect") is True for i in items),
        "evidence_hit": sum(i.get("judge_ok") is True and i.get("evidence_hint") is True for i in items),
        "judge_success_rate": sum(i.get("judge_ok") is True for i in items),
        "generation_success_rate": sum(not input_errors[qid] for qid in golds),
    }
    metrics = {key: round(value / n, 4) if n else 0 for key, value in counts.items()}
    metrics.update({
        "n": n,
        "judge_channel": f"llm(provider={judge_config['provider']},model={judge_config['model']}) 逐题三要素语义判定",
    })
    error_counts = {}
    for item in items:
        if item.get("error_type"):
            error_type = item["error_type"]
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
    result = {
        "meta": {
            "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
            "answers": args.answers, "gold": args.gold,
            "judge_config": {k: v for k, v in judge_config.items() if k != "endpoint"},
            "endpoint_sha256": hashlib.sha256(judge_config["endpoint"].encode("utf-8")).hexdigest(),
            "parse_retries": args.parse_retries,
            "judge_attempts_scope": "chat invocations; excludes retries inside chat",
            "score_timeout_ms": None,
            "availability_scope": "semantic availability over all gold qids; judge failures are conservative placeholders",
            "generation_success_scope": "nonempty string and no explicit pipeline failure; absent pipeline_ok remains legacy-compatible",
        },
        "metrics": metrics,
        "metric_counts": {key: {"numerator": value, "denominator": n} for key, value in counts.items()},
        "error_counts": error_counts,
        "items": items,
    }
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1))
    os.replace(tmp, out_path)
    print("[llm-judge] METRICS:", json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
