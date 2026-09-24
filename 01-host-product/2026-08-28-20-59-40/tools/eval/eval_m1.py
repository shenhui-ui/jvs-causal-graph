#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_m1.py —— M1「跨会话因果问答」MVP 判分器接入草案(单文件,纯标准库)

=========================================================================
一、定位
=========================================================================
本文件是 M1-14(判分器接入)的**草案**。它把既有 `memory_eval.py`(位于
`tools/eval/memory_eval.py`)已能做的「规则化/结构化判分」,与 M1 立项书
(v0.6 = M1 立项基线)要求的三个 M1 级指标对齐:

  * **系统可用率 availability**      ≥90%   —— 非空 / 非超时 / pipeline_ok
  * **因果准确率 causal_accuracy**   主验收:≥60%(硬)/ 70%(目标)
  * **端到端合格率 end_to_end_qualified_rate** ≥60%(硬)/ 70%(目标)
  * **证据完整率 evidence_completeness**   —— 三项原始数据之一(随附报告)

它**只做判分**,不调用任何 LLM、不联网、无第三方依赖。真正的「因果三要素」
语义判定走 `llm_check` 接口——**默认实现是规则启发式(关键词 / 子串 / 结构判定)**,
并明确标注「接入真实 LLM 判分时替换」。二者可无缝切换(`judge_channel` 字段)。

=========================================================================
二、与 memory_eval.py 的关系
=========================================================================
  * **复用**:本文件不重复实现 Markdown→JSONL 解析。M1 判断分母(真实题集 ≥30 题)
    用 `memory_eval.py convert --schema causal` 先产出黄金 JSONL,再作为 `--gold`
    喂给本工具。(见 README「与 memory_eval.py 的关系」)
  * **复用 schema**:memory_eval 的 causal schema 里,`answer` = 标准答案(证据链式,
    「因为 X 触发 → 由 Y 提出(决策者)→ 目的是 Z」);本工具把这三段拆成 M1 判分器的
    `cause / evidence_hint(决策者) / effect` 三要素。
  * **新增 M1 指标**:memory_eval 只给「命中率 + 结构通过率 + 反事实人工清单」,
    不含 availability / causal_accuracy / evidence_completeness / qualified。
    本文件把这些 M1 指标补上。
  * 语义正确性本身仍需人工或 LLM 判定;本文件的规则子串/结构判定是**可复现的近似**,
    正式结题建议接入真实 LLM 判分后再人工抽检约 20%。

=========================================================================
三、输入字段约定(answers.jsonl · 系统输出)
=========================================================================
每条记录(JSON Lines,一行一个 dict,坏行不中断):

  qid            题目唯一编号(必填,如 R-C01)
  answer_text    系统答案文本(必填,因果题应为证据链式回答)
  evidence_chain 证据链(list[str]);由系统给出,判分只看其是否含 `[src:` 引用
  latency_ms     系统端到端耗时(毫秒);缺失视为<超时(即不超时)
  pipeline_ok    管线是否成功(必填,bool)
  trace          可选,系统 trace(仅透传/诊断,不参与判分)

黄金三要素读取优先级(合成为 gold):
  1) 记录内嵌字段:记录顶层的 `cause / effect / evidence_hint`
     (或 `gold` dict:{cause, effect, evidence_hint, question, answer, schema});
  2) `--gold <jsonl>`(memory_eval convert 的产物)按 qid 补齐。
        其中 cause = 触发 X;evidence_hint = 决策者 Y(证据/拍板者);effect = 目的 Z。
   黄金三要素缺任一 → 该条不参与 causal_accuracy 分母(报为 n_ungraded)。

=========================================================================
四、命令摘要
=========================================================================
  python eval_m1.py score  answers.jsonl [--gold gold.jsonl] [--out scores.json]
      [--seed N] [--double-check] [--timeout-ms 30000] [--match-threshold 0.45]
      [--require-causal-structure] [--conflict-out conflict_list.jsonl]
  python eval_m1.py report-md scores.json [--out report.md]
      [--baseline 0.60] [--target 0.70] [--availability-baseline 0.90]
  python eval_m1.py finalize scores.json conflict_list.jsonl final.jsonl
      [--out scores_final.json]
  python eval_m1.py --version

判分口径(M1 立项书 §2.4 / §2.5):
  * qualified = causal_all_correct 且 evidence_score >= 1.0 且 format_ok
    —— 分母 = 可用条目(可用基础上),同时输出「全量分母」保守口径。
  * 主验收按题数阈值判(18/30 硬、21/30 目标),本工具同时给 95% CI 便于对照。
"""

import argparse
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# 常量与默认配置
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_MS = 30000          # 逐题耗时阈值，不是实测 P95；专家模式显式传入
DEFAULT_COVERAGE_THRESHOLD = 0.45   # 三要素内容覆盖判分阈值
DEFAULT_STRUCT_PARTIAL = 0.25       # 结构判定时要求的最小覆盖
DEFAULT_BASELINE = 0.60             # 主验收/合格率硬线 60%
DEFAULT_TARGET = 0.70               # 目标线 70%
DEFAULT_AVAIL_BASELINE = 0.90       # 可用率硬线 90%

# 因果三要素字段名(cause/effect/evidence_hint)
ELEMENTS = ("cause", "effect", "evidence_hint")

# 因果连接词 / 决策标记(结构判定的词表)
_CAUSAL_CONNECTIVES = ("因为", "所以", "由于", "因此", "导致", "因而", "→", "从而")
_DECISION_MARKERS = ("决定", "拍板", "提出", "同意", "批准", "确认", "指出", "负责", "确认", "点头")
_EFFECT_MARKERS = ("为了", "目的", "旨在", "以降低", "以提升", "以便", "以减少")

_PUNCT_RE = re.compile(r"[\u3000\s，。！？、；：·…—–（）()《》〈〉【】\[\]「」『』“”\"'"
                       r"\u2018\u2019\u201c\u201d.,;:!?/\\|+*~`^_=<>]{1,}")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_warn(message):
    sys.stderr.write("[eval_m1][warn] {0}\n".format(message))


def ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_jsonl(path):
    """逐行读 JSONL,坏行不中断。返回 (records, errors[(line, err)])。"""
    records, errors = [], []
    if not os.path.exists(path):
        raise ValueError("找不到文件: {0}".format(path))
    # 用 utf-8-sig 容忍 Windows 下常见的 UTF-8 BOM(逐行读,坏行不中断)
    with open(path, "r", encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    errors.append((i, "非对象类型: {0}".format(type(obj).__name__)))
            except json.JSONDecodeError as exc:
                errors.append((i, "JSON 解析失败: {0}".format(exc)))
    return records, errors


def write_jsonl(records, path):
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(obj, path):
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def strip_md(text):
    if not text:
        return ""
    t = re.sub(r"\*\*", "", text)
    t = re.sub(r"`", "", t)
    t = re.sub(r"(?m)^>\s?", "", t)
    return t.strip()


def normalize(text):
    """归一化文本用于相似度/覆盖判定:去标记、去标点与空白、统一小写。"""
    t = strip_md(text)
    t = t.lower()
    t = re.sub(_PUNCT_RE, "", t)
    return t


def _bigrams(s):
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def bigram_sim(a, b):
    A = _bigrams(normalize(a))
    B = _bigrams(normalize(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def coverage(gold, ans):
    """内容覆盖度:gold 的字符二元组被 ans 覆盖的比例,范围 [0,1]。"""
    G = _bigrams(normalize(gold))
    A = _bigrams(normalize(ans))
    if not G:
        return 0.0
    return len(G & A) / len(G)


# ---------------------------------------------------------------------------
# LLM 判分器接口(stub:规则启发式)
# ---------------------------------------------------------------------------

def llm_check(element, answer_text, gold_text, coverage_threshold=DEFAULT_COVERAGE_THRESHOLD,
              struct_partial=DEFAULT_STRUCT_PARTIAL):
    """判分器单要素接口(stub)。

    ……接入真实 LLM 判分时替换本函数……
    接口约定:给定 element(cause/effect/evidence_hint)、系统答案 answer_text、黄金要素
    gold_text,返回 bool(该要素是否正确)。**替换实现**建议返回
    {"correct": bool, "reason": str, "confidence": float} 或直接 bool;
    且遵循 M1「固定 seed + 同题 2 次取一致」口径(见 --seed / --double-check)。

    —— 默认实现:规则启发式(关键词 / 子串 / 结构判定)。
      1) 内容覆盖:gold 的归一化二元组被 answer 覆盖 >= coverage_threshold,或完全相等/子串包含;
      2) 结构判定:命中对应结构词(因为/目的/决策词)且内容覆盖 >= struct_partial。
    """
    if not answer_text or not str(answer_text).strip():
        return False
    answer_text = str(answer_text)
    gold_text = str(gold_text or "").strip()
    if not gold_text:
        return False

    # 1) 内容覆盖 / 子串
    if _content_match(gold_text, answer_text, coverage_threshold):
        return True

    # 2) 结构判定
    if _structure_match(element, answer_text, gold_text, struct_partial):
        return True

    return False


def _content_match(gold_text, answer_text, threshold):
    g = normalize(gold_text)
    a = normalize(answer_text)
    if not g or not a:
        return False
    if g == a or g in a or a in g:
        return True
    G = _bigrams(g)
    A = _bigrams(a)
    if not G:
        return False
    return len(G & A) / len(G) >= threshold


def _structure_match(element, answer_text, gold_text, struct_partial):
    """结构判定:命中该要素对应的结构词,并保证对黄金有一定内容覆盖。"""
    cov = coverage(gold_text, answer_text)
    if cov < struct_partial:
        return False
    if element == "cause":
        return any(m in answer_text for m in ("因为", "由于", "触发", "起因", "导致"))
    if element == "effect":
        return any(m in answer_text for m in _EFFECT_MARKERS + ("使", "令"))
    if element == "evidence_hint":
        return any(m in answer_text for m in _DECISION_MARKERS)
    return False


# ---------------------------------------------------------------------------
# 黄金三要素提取与合并
# ---------------------------------------------------------------------------

def extract_gold(record, gold_map):
    """从 record(内嵌)或 gold_map(--gold)提取黄金三要素 dict。优先级:内嵌 > --gold。"""
    gold = {}
    # 1) record 内嵌的 gold dict
    emb = record.get("gold")
    if isinstance(emb, dict):
        gold.update({k: emb.get(k) for k in ELEMENTS + ("question", "answer", "schema")})
    # 2) record 顶层直接带三要素
    for k in ELEMENTS + ("question", "answer", "schema"):
        if k in record and record.get(k) not in (None, ""):
            gold.setdefault(k, record.get(k))
    # 3) --gold 按 qid 补齐
    qid = record.get("qid")
    qid_key = str(qid) if qid is not None else None
    if qid_key is not None and qid_key in gold_map:
        g = gold_map[qid_key]
        for k in ELEMENTS + ("question", "answer", "schema"):
            if gold.get(k) in (None, "") and g.get(k) not in (None, ""):
                gold[k] = g.get(k)
    return {k: (v if v not in (None, "") else "") for k, v in gold.items()} if any(gold.get(k) for k in ELEMENTS) else gold


def build_gold_map(gold_path):
    gm = {}
    if not gold_path:
        return gm
    recs, errs = read_jsonl(gold_path)
    for ln, err in errs:
        log_warn("黄金文件第 {0} 行解析失败: {1}".format(ln, err))
    for g in recs:
        qid = g.get("qid") or g.get("id")
        if qid is not None:
            key = str(qid)
            if key in gm:
                raise ValueError("黄金文件存在重复 qid: {0}".format(key))
            gm[key] = g
    return gm


# ---------------------------------------------------------------------------
# 单条判分
# ---------------------------------------------------------------------------

def is_available(record, timeout_ms):
    """系统可用 = pipeline_ok 且 answer_text 非空 且 不超时。返回 (bool, reason)。"""
    ok = record.get("pipeline_ok", False)
    text = str(record.get("answer_text") or "").strip()
    if ok is not True:
        return False, "pipeline_ok=False"
    if not text:
        return False, "answer_text为空"
    lat = record.get("latency_ms")
    if isinstance(lat, (int, float)) and lat is not None:
        try:
            if float(lat) > timeout_ms:
                return False, "超时({0:.0f}ms>{1:.0f}ms)".format(float(lat), timeout_ms)
        except (TypeError, ValueError):
            pass
    return True, ""


def evidence_score(evidence_chain):
    """证据完整率单条评分：规范来源引用条数 / 非空字符串条数。
       full: 比例为 1；partial: 比例在 0 和 1 之间；none: 比例为 0。
       `[src:` 与 `[src：` 均计入引用；标记比例不代表事实支持性。
    返回 (score, grade, n_src, n_total)。
    """
    if not evidence_chain:
        return 0.0, "none", 0, 0
    nonempty = [e for e in evidence_chain if isinstance(e, str) and e.strip()]
    if not nonempty:
        return 0.0, "none", 0, 0
    total = len(nonempty)
    n_src = sum(1 for e in nonempty if ("[src:" in e or "[src：" in e))
    ratio = n_src / total if total else 0.0
    grade = "full" if ratio >= 1.0 else ("partial" if ratio > 0 else "none")
    return ratio, grade, n_src, total


def check_format(answer_text, require_structure):
    """格式合规:answer_text 为非空字符串;(可选)要求含因果连接词。"""
    if not answer_text or not str(answer_text).strip():
        return False
    if require_structure:
        return any(m in str(answer_text) for m in _CAUSAL_CONNECTIVES)
    return True


def grade_record(record, gold_map, timeout_ms, coverage_threshold, require_structure,
                 element_results=None):
    """对单条记录判分,返回 grading dict + gold 三要素。"""
    qid = record.get("qid")
    gold = extract_gold(record, gold_map)
    answer_text = str(record.get("answer_text") or "")
    evidence_chain = record.get("evidence_chain") or []

    is_avail, reason = is_available(record, timeout_ms)
    es, egrade, n_src, n_total = evidence_score(evidence_chain)
    fmt_ok = check_format(answer_text, require_structure)

    # 三要素判定(仅当黄金三要素齐全)
    has_gold = all(gold.get(k) for k in ELEMENTS)
    elem = {}
    for k in ELEMENTS:
        if has_gold:
            if element_results is not None and k in element_results:
                elem[k] = bool(element_results[k])
            else:
                elem[k] = bool(llm_check(k, answer_text, gold.get(k), coverage_threshold))
        else:
            elem[k] = None
    all_correct = bool(elem["cause"] and elem["effect"] and elem["evidence_hint"]) if has_gold else None

    # 合格(可用基础上:因果正确+证据完整+格式合规)
    qualified = bool(is_avail and all_correct and es >= 1.0 and fmt_ok) if has_gold else False

    return {
        "qid": qid,
        "question": gold.get("question", ""),
        "schema": gold.get("schema", "causal"),
        "available": bool(is_avail),
        "unavailable_reason": reason,
        "cause_correct": elem["cause"],
        "effect_correct": elem["effect"],
        "evidence_hint_correct": elem["evidence_hint"],
        "causal_all_correct": all_correct,
        "evidence_grade": egrade,
        "evidence_score": es,
        "evidence_src_count": n_src,
        "evidence_total": n_total,
        "format_ok": bool(fmt_ok),
        "format_requires_structure": require_structure,
        "qualified": bool(qualified),
        "answer_text": answer_text,
        "evidence_chain": evidence_chain,
        "latency_ms": record.get("latency_ms"),
        "pipeline_ok": record.get("pipeline_ok") is True,
        "has_gold": has_gold,
    }, gold


# ---------------------------------------------------------------------------
# 双判分(--double-check)
# ---------------------------------------------------------------------------

def _llm_check_double(element, answer_text, gold_text, coverage_threshold, pass_no, rng):
    """同一题第 pass_no 次判分。

    默认启发式是确定性的,两种子给出同一结果(即固定 seed 下一致);
    接入真实 LLM 判分时,两次调用会各自独立跑 LLM(天然可能不一致),
    从而形成 conflict_list.jsonl 的人工仲裁输入。为演示接口,这里允许
    rng 参与——真实实现里它决定 LLM 采样温度/种子。
    """
    # 占位:让 rng 真正被使用(默认实现不改判定结果;真实实现可据此扰动采样)
    if rng is not None:
        rng.random()
    return llm_check(element, answer_text, gold_text, coverage_threshold)


def double_check_record(record, gold_map, timeout_ms, coverage_threshold, rng,
                        require_structure=False):
    """对单条记录做 2 次判分,返回 (grading, conflicts, element_results)。"""
    gold = extract_gold(record, gold_map)
    has_gold = all(gold.get(k) for k in ELEMENTS)
    element_results = {}
    conflicts = []
    if has_gold:
        ans = str(record.get("answer_text") or "")
        for k in ELEMENTS:
            a = _llm_check_double(k, ans, gold.get(k), coverage_threshold, 1, rng)
            b = _llm_check_double(k, ans, gold.get(k), coverage_threshold, 2, rng)
            element_results[k] = bool(a and b)
            if a != b:
                conflicts.append({
                    "qid": record.get("qid"),
                    "element": k,
                    "pass_a": bool(a),
                    "pass_b": bool(b),
                    "gold_text": gold.get(k, ""),
                })
    grading, _ = grade_record(record, gold_map, timeout_ms, coverage_threshold,
                              require_structure, element_results if has_gold else None)
    return grading, conflicts, element_results


# ---------------------------------------------------------------------------
# 统计工具:Wilson 95% CI
# ---------------------------------------------------------------------------

def wilson_ci(k, n, z=1.96):
    """Wilson score interval,返回 (low, high, p)。n=0 → (0,0,0)。"""
    if n <= 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin), p)


# ---------------------------------------------------------------------------
# 主判分流程
# ---------------------------------------------------------------------------

def score_records(records, gold_map, timeout_ms, coverage_threshold, require_structure,
                  seed, double_check, gold_ids=None, return_items=False):
    rng = random.Random(seed)
    items, conflicts = [], []
    by_qid = {str(rec.get("qid")): rec for rec in records if rec.get("qid") is not None}
    score_records_input = records
    if gold_ids is not None:
        score_records_input = [by_qid.get(str(qid), {"qid": qid, "pipeline_ok": False})
                               for qid in gold_ids]
    for rec in score_records_input:
        if double_check and rec.get("qid") is not None:
            g, c, _results = double_check_record(rec, gold_map, timeout_ms, coverage_threshold, rng,
                                                  require_structure)
            items.append(g)
            for conflict in c:
                conflict["answer_text"] = rec.get("answer_text")
                conflict["evidence_chain"] = rec.get("evidence_chain")
                conflict["latency_ms"] = rec.get("latency_ms")
                conflict["pipeline_ok"] = rec.get("pipeline_ok")
            conflicts.extend(c)
        else:
            g, _gold = grade_record(rec, gold_map, timeout_ms, coverage_threshold, require_structure)
            items.append(g)

    n = len(items)
    available = [x for x in items if x["available"]]
    n_avail = len(available)
    n_unavail = n - n_avail

    # --- availability ---
    reasons = {}
    for x in items:
        if not x["available"]:
            r = x.get("unavailable_reason") or "未知"
            # 归并成类别
            if "pipeline_ok" in r:
                c = "pipeline_ok=False"
            elif "为空" in r:
                c = "answer_text为空"
            else:
                c = "超时"
            reasons[c] = reasons.get(c, 0) + 1
    avail_low, avail_high, avail_p = wilson_ci(n_avail, n)

    # --- causal 三要素 ---
    causal_items = [x for x in items if x["has_gold"]]
    n_causal = len(causal_items)
    n_ungraded = n - n_causal
    cause_k = sum(1 for x in causal_items if x["cause_correct"])
    effect_k = sum(1 for x in causal_items if x["effect_correct"])
    evhint_k = sum(1 for x in causal_items if x["evidence_hint_correct"])
    all_k = sum(1 for x in causal_items if x["causal_all_correct"])
    ca_low, ca_high, ca_p = wilson_ci(all_k, n_causal)
    per_elem = {}
    for k, hit in (("cause", cause_k), ("effect", effect_k), ("evidence_hint", evhint_k)):
        per_elem[k] = {"n_hit": hit, "rate": (hit / n_causal) if n_causal else 0.0}

    # --- evidence ---
    full = sum(1 for x in items if x["evidence_grade"] == "full")
    part = sum(1 for x in items if x["evidence_grade"] == "partial")
    none_ = sum(1 for x in items if x["evidence_grade"] == "none")
    mean_ev = (sum(x["evidence_score"] for x in items) / n) if n else 0.0
    ev_full_rate = (full / n) if n else 0.0
    ev_partial_rate = (part / n) if n else 0.0
    ev_none_rate = (none_ / n) if n else 0.0

    # --- format ---
    fmt_ok = sum(1 for x in items if x["format_ok"])
    fmt_rate = (fmt_ok / n) if n else 0.0

    # --- qualified(端到端合格率) ---
    # 主口径:可用基础上。保守口径:全量分母。
    qual_avail = sum(1 for x in available if x["qualified"])
    qual_all = sum(1 for x in items if x["qualified"])
    qa_low, qa_high, qa_p = wilson_ci(qual_avail, n_avail)
    q_all_low, q_all_high, q_all_p = wilson_ci(qual_all, n)

    double_meta = None
    if double_check:
        conflict_qids = {str(c.get("qid")) for c in conflicts if c.get("qid") is not None}
        n_conflict = len(conflict_qids)
        n_pairs = sum(1 for item in items if item["has_gold"])  # 每条可判题判 2 次
        consist_low, consist_high, consist_p = wilson_ci(max(0, n_pairs - n_conflict), n_pairs) if n_pairs else (0, 0, 0)
        double_meta = {
            "n_checked": n_pairs,
            "n_conflict_records": n_conflict,
            "consistency_rate": consist_p,
            "consistency_ci95": [consist_low, consist_high],
            "conflict_ids": sorted(conflict_qids),
            "n_conflict_elements": len(conflicts),
        }

    return {
        "availability": {
            "n_total": n,
            "n_available": n_avail,
            "n_unavailable": n_unavail,
            "available_rate": avail_p,
            "ci95": [avail_low, avail_high],
            "unavailable_reasons": reasons,
        },
        "causal_accuracy": {
            "n_causal": n_causal,
            "n_ungraded": n_ungraded,
            "n_all_correct": all_k,
            "causal_accuracy": ca_p,
            "ci95": [ca_low, ca_high],
            "per_element": per_elem,
        },
        "evidence_completeness": {
            "n": n,
            "n_full": full,
            "n_partial": part,
            "n_none": none_,
            "full_rate": ev_full_rate,
            "partial_rate": ev_partial_rate,
            "none_rate": ev_none_rate,
            "mean_score": mean_ev,
        },
        "format_ok": {
            "n": n,
            "n_ok": fmt_ok,
            "format_ok_rate": fmt_rate,
            "requires_structure": require_structure,
        },
        "qualified": {
            "n_total": n,
            "n_available": n_avail,
            "n_qualified_avail": qual_avail,
            "n_qualified_all": qual_all,
            "end_to_end_qualified_rate": qa_p,     # 主口径(可用基础上)
            "end_to_end_qualified_rate_all": q_all_p,  # 保守(全量分母)
            "ci95": [qa_low, qa_high],
            "ci95_all": [q_all_low, q_all_high],
        },
        "double_check": double_meta,
        **({"_items": items, "_conflicts": conflicts} if return_items else {}),
    }


# ---------------------------------------------------------------------------
# 子命令:score
# ---------------------------------------------------------------------------

def cmd_score(argv):
    p = argparse.ArgumentParser(
        prog="eval_m1 score",
        description="对 answers.jsonl 判分,输出 M1 指标(scores.json)。",
    )
    p.add_argument("answers", help="系统输出 answers.jsonl(qid/answer_text/evidence_chain/latency_ms/pipeline_ok/trace)")
    p.add_argument("--gold", default=None, help="黄金 JSONL(如 memory_eval convert 产物),按 qid 补齐三要素")
    p.add_argument("--out", default=None, help="评分 JSON 输出路径(默认打印 stdout)")
    p.add_argument("--seed", type=int, default=42, help="固定判分随机性(默认 42)")
    p.add_argument("--double-check", action="store_true", help="同题判分 2 次,不一致写 conflict_list.jsonl")
    p.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="可用性超时阈值(默认 30000ms)")
    p.add_argument("--match-threshold", type=float, default=DEFAULT_COVERAGE_THRESHOLD,
                   help="三要素内容覆盖判分阈值(默认 0.45)")
    p.add_argument("--require-causal-structure", action="store_true",
                   help="format_ok 额外要求答案含因果连接词")
    p.add_argument("--conflict-out", default="conflict_list.jsonl", help="冲突清单输出路径(仅 --double-check)")
    args = p.parse_args(argv)

    records, errs = read_jsonl(args.answers)
    for ln, err in errs:
        log_warn("答案文件第 {0} 行解析失败: {1}".format(ln, err))
    if not records:
        log_warn("answers.jsonl 无记录;请检查路径/字段。")
        return 1
    gold_map = build_gold_map(args.gold)
    qids = [str(r.get("qid")) for r in records]
    if any(r.get("qid") in (None, "") for r in records):
        raise ValueError("答案记录缺少 qid")
    if len(set(qids)) != len(qids):
        raise ValueError("答案文件存在重复 qid")
    gold_ids = None
    if args.gold:
        gold_ids = list(gold_map.keys())
        unknown = sorted(set(qids) - set(gold_ids))
        if unknown:
            raise ValueError("答案文件包含未知 qid: {0}".format(",".join(unknown)))

    results = score_records(records, gold_map, args.timeout_ms, args.match_threshold,
                            args.require_causal_structure, args.seed, args.double_check,
                            gold_ids=gold_ids, return_items=True)
    scored_items = results.pop("_items")
    conflict_list = results.pop("_conflicts", [])

    meta = {
        "tool": "eval_m1",
        "version": __version__,
        "generated_at": _now_iso(),
        "seed": args.seed,
        "double_check": bool(args.double_check),
        "timeout_ms": args.timeout_ms,
        "coverage_threshold": args.match_threshold,
        "require_causal_structure": args.require_causal_structure,
        "judge_channel": ("rule_heuristic —— 接入真实 LLM 判分时替换 llm_check()" if True else ""),
        "n_predictions": len(records),
        "n_gold": len(gold_map),
        "missing_gold_ids": (sorted(set(gold_map) - set(qids)) if gold_map else []),
    }
    meta["judge_channel"] = ("rule_heuristic(默认,关键词/子串/结构判定)——"
                             "接入真实 LLM 判分时替换 llm_check()")
    out = {"meta": meta, "metrics": results, "items": None}
    # items(逐条判分明细)直接复用主评分流程结果，避免双判分重复调用。
    out["items"] = scored_items

    if args.double_check:
        # 使用 score_records 已完成的同一组两轮判定结果，避免再次调用判分器。
        write_jsonl(conflict_list, args.conflict_out)
        log_warn("双判分完成:一致率 {0:.4f},冲突 {1} 条 → {2}".format(
            results["double_check"]["consistency_rate"],
            len(conflict_list), args.conflict_out))

    if args.out:
        write_json(out, args.out)
        print("[eval_m1] score 完成:输出 {0};n={1},预测={2},gold={3},可用率={4:.1%},因果准确率={5:.1%},合格率={6:.1%}".format(
            args.out, results["availability"]["n_total"], len(records), len(gold_map),
            results["availability"]["available_rate"],
            results["causal_accuracy"]["causal_accuracy"],
            results["qualified"]["end_to_end_qualified_rate"]))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 子命令:report-md
# ---------------------------------------------------------------------------

def _pct(v, ndigits=1):
    return "N/A" if v is None else "{0:.{1}f}%".format(v * 100, ndigits)


def _cmp(v, baseline, target):
    """返回对硬/目标线的对照描述。"""
    if v is None:
        return "不可判(分母为 0)", "不可判", "不可判"
    lines = []
    hard_ok = v >= baseline
    tgt_ok = v >= target
    lines.append("主验收硬线 {0}:实测 {1} → {2}".format(
        _pct(baseline, 0), _pct(v), "达标" if hard_ok else "未达标"))
    lines.append("目标线 {0}:实测 {1} → {2}".format(
        _pct(target, 0), _pct(v), "达标" if tgt_ok else "未达标"))
    flag = "达标" if hard_ok else ("达标" if False else "未达标")
    return "；".join(lines), "达标" if hard_ok else "未达标", "达标" if tgt_ok else "未达标"


def cmd_report_md(argv):
    p = argparse.ArgumentParser(
        prog="eval_m1 report-md",
        description="把 scores.json 渲染为 Markdown 报告(三项原始数据 + 合格率 + 可用率 + 对照区间)。",
    )
    p.add_argument("scores", help="score 命令输出的 scores.json")
    p.add_argument("--out", default=None, help="Markdown 输出路径(默认 stdout)")
    p.add_argument("--baseline", type=float, default=DEFAULT_BASELINE, help="主验收/合格率硬线(默认 0.60)")
    p.add_argument("--target", type=float, default=DEFAULT_TARGET, help="目标线(默认 0.70)")
    p.add_argument("--availability-baseline", type=float, default=DEFAULT_AVAIL_BASELINE, help="可用率硬线(默认 0.90)")
    args = p.parse_args(argv)

    with open(args.scores, "r", encoding="utf-8") as fh:
        s = json.load(fh)

    meta = s.get("meta", {})
    m = s.get("metrics", {})
    lines = []
    lines.append("# M1 判分报告(因果问答 MVP)")
    lines.append("")
    lines.append("- **工具版本**: {0} ｜ 判分通道: {1}".format(meta.get("version", "?"), meta.get("judge_channel", "?")))
    lines.append("- **生成时间**: {0}".format(meta.get("generated_at", "?")))
    lines.append("- **seed**: {0} ｜ 双判分: {1}".format(meta.get("seed", "-"), "开启" if meta.get("double_check") else "关闭"))
    lines.append("- **题数(n)**: {0} ｜ 超时阈值: {1}ms ｜ 覆盖阈值: {2}".format(
        meta.get("n_predictions", m.get("availability", {}).get("n_total", 0)),
        meta.get("timeout_ms", "-"), meta.get("coverage_threshold", "-")))
    lines.append("")

    # --- 可用率 ---
    av = m.get("availability", {})
    avail_p = av.get("available_rate")
    lines.append("## 一、系统可用率")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append("| 总题数 | {0} |".format(av.get("n_total", 0)))
    lines.append("| 可用题数 | {0} |".format(av.get("n_available", 0)))
    lines.append("| 不可用题数 | {0} |".format(av.get("n_unavailable", 0)))
    lines.append("| **可用率** | **{0}** |".format(_pct(avail_p)))
    lines.append("| 95% CI | [{0}, {1}] |".format(_pct(av.get("ci95", [None, None])[0]),
                                                  _pct(av.get("ci95", [None, None])[1])))
    lines.append("| 硬线(≥{0}) | {1} |".format(_pct(args.availability_baseline, 0),
                                              "达标" if (avail_p or 0) >= args.availability_baseline and avail_p is not None else "未达标"))
    unc = av.get("unavailable_reasons", {})
    if unc:
        lines.append("| 不可用原因 | {0} |".format("；".join("{0}×{1}".format(k, v) for k, v in unc.items())))
    lines.append("")

    # --- causal ---
    ca = m.get("causal_accuracy", {})
    ca_p = ca.get("causal_accuracy")
    lines.append("## 二、因果准确率(主验收)")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append("| 可判分题数 | {0} |".format(ca.get("n_causal", 0)))
    lines.append("| 未判数(缺黄金三要素) | {0} |".format(ca.get("n_ungraded", 0)))
    lines.append("| **三要素全对题数** | {0} |".format(ca.get("n_all_correct", 0)))
    lines.append("| **因果准确率** | **{0}** |".format(_pct(ca_p)))
    lines.append("| 95% CI | [{0}, {1}] |".format(_pct(ca.get("ci95", [None, None])[0]),
                                                  _pct(ca.get("ci95", [None, None])[1])))
    lines.append("")
    lines.append("### 三要素逐项原始数据")
    lines.append("")
    lines.append("| 要素 | 判对题数 | 判对率 |")
    lines.append("|---|---|---|")
    for k, label in (("cause", "触发(cause)"), ("effect", "目的(effect)"),
                     ("evidence_hint", "决策者/证据(evidence_hint)")):
        pe = ca.get("per_element", {}).get(k, {})
        lines.append("| {0} | {1} | {2} |".format(label, pe.get("n_hit", 0), _pct(pe.get("rate"))))
    lines.append("")
    hard_txt, hard_flag, tgt_flag = _cmp(ca_p, args.baseline, args.target)
    lines.append("### 与主验收对照(主验收硬线 {0} / 目标 {1})".format(_pct(args.baseline, 0), _pct(args.target, 0)))
    lines.append("")
    lines.append("- {0}".format(hard_txt.replace("；", " ｜ ")))
    lines.append("")

    # --- evidence ---
    ev = m.get("evidence_completeness", {})
    lines.append("## 三、证据完整率")
    lines.append("")
    lines.append("| 级别 | 判分 | 题数 | 占比 |")
    lines.append("|---|---|---|---|")
    lines.append("| 完整(所有非空条目均含来源引用) | 1.0 | {0} | {1} |".format(ev.get("n_full", 0), _pct(ev.get("full_rate"))))
    lines.append("| 部分(仅部分非空条目含来源引用) | 0 < 比例 < 1 | {0} | {1} |".format(ev.get("n_partial", 0), _pct(ev.get("partial_rate"))))
    lines.append("| 无(无规范来源引用或无证据) | 0.0 | {0} | {1} |".format(ev.get("n_none", 0), _pct(ev.get("none_rate"))))
    lines.append("| **平均证据完整率** | — | {0} | **{1}** |".format(ev.get("n", 0), _pct(ev.get("mean_score"))))
    lines.append("")

    # --- format ---
    fm = m.get("format_ok", {})
    lines.append("## 四、格式合规(format_ok)")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append("| 合规题数 | {0} |".format(fm.get("n_ok", 0)))
    lines.append("| **格式合规率** | **{0}** |".format(_pct(fm.get("format_ok_rate"))))
    lines.append("| 是否要求因果连接词 | {0} |".format("是" if fm.get("requires_structure") else "否"))
    lines.append("")

    # --- qualified ---
    q = m.get("qualified", {})
    q_p = q.get("end_to_end_qualified_rate")
    lines.append("## 五、端到端合格率")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append("| 分母(可用题数) | {0} |".format(q.get("n_available", 0)))
    lines.append("| 合格题数(可用基础上) | {0} |".format(q.get("n_qualified_avail", 0)))
    lines.append("| **合格率(可用基础上)** | **{0}** |".format(_pct(q_p)))
    lines.append("| 95% CI | [{0}, {1}] |".format(_pct(q.get("ci95", [None, None])[0]),
                                                  _pct(q.get("ci95", [None, None])[1])))
    lines.append("| 保守口径(全量分母) | {0}({1}/{2}) |".format(
        _pct(q.get("end_to_end_qualified_rate_all")), q.get("n_qualified_all", 0), q.get("n_total", 0)))
    lines.append("")
    qhard_txt, _, _ = _cmp(q_p, args.baseline, args.target)
    lines.append("### 与硬线 {0} / 目标 {1} 对照".format(_pct(args.baseline, 0), _pct(args.target, 0)))
    lines.append("")
    lines.append("- {0}".format(qhard_txt.replace("；", " ｜ ")))
    lines.append("")

    # --- 双判分 ---
    if m.get("double_check"):
        dc = m["double_check"]
        lines.append("## 六、判分器一致性(--double-check)")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append("| 已判题数(2 次/题) | {0} |".format(dc.get("n_checked", 0)))
        lines.append("| 冲突记录数 | {0} |".format(dc.get("n_conflict_records", 0)))
        lines.append("| **一致率** | **{0}** |".format(_pct(dc.get("consistency_rate"))))
        lines.append("| 95% CI | [{0}, {1}] |".format(_pct(dc.get("consistency_ci95", [None, None])[0]),
                                                      _pct(dc.get("consistency_ci95", [None, None])[1])))
        lines.append("")
        lines.append("> 需人工仲裁的 qid: {0}(见 conflict_list.jsonl)。".format(
            "、".join(map(str, dc.get("conflict_ids", []))) if dc.get("conflict_ids") else "无"))
        lines.append("")

    # --- 关键要点 ---
    lines.append("## 关键要点")
    lines.append("")
    lines.append("1. **判分通道**: {0}。指标为规则启发式近似,正式结题建议接入真实 LLM 判分后人工抽检约 20%。".format(meta.get("judge_channel", "?")))
    lines.append("2. **三项原始数据**(Gate 3 必须):因果准确率 / 证据完整率 / 端到端合格率,均已在上面给出;另附可用率与 95% CI。")
    lines.append("3. **口径**: 合格率公式(近似)= 因果准确率 × 证据完整率,格式项另行计;主验收与合格率硬线同题集、同判分器(见 M1 立项书 §2.4)。")
    lines.append("")
    lines.append("> 由 `eval_m1.py report-md` 自动生成,供人工复核与 Gate 3 使用。主验收按题数阈值判定(18/30 硬、21/30 目标),本报告另附 95% CI 便于对照。")

    report = "\n".join(lines)
    if args.out:
        ensure_parent(args.out)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("[eval_m1] report-md 完成:输出 {0}".format(args.out))
    else:
        print(report)
    return 0


# ---------------------------------------------------------------------------
# 子命令:finalize(人工仲裁回填)
# ---------------------------------------------------------------------------

def cmd_finalize(argv):
    p = argparse.ArgumentParser(
        prog="eval_m1 finalize",
        description="把 conflict_list.jsonl + 人工 final.jsonl 回填到 scores.json,重算指标。",
    )
    p.add_argument("scores", help="score 命令输出的 scores.json")
    p.add_argument("conflicts", help="conflict_list.jsonl(双判分不一致清单)")
    p.add_argument("final", help="人工 final.jsonl(每条含 qid + 人工判定的布尔结果)")
    p.add_argument("--out", default=None, help="回填后的 scores.json(默认 stdout)")
    args = p.parse_args(argv)

    with open(args.scores, "r", encoding="utf-8") as fh:
        s = json.load(fh)
    conf_recs, _c_err = read_jsonl(args.conflicts)
    final_recs, _f_err = read_jsonl(args.final)
    conflict_qids = {str(row.get("qid")) for row in conf_recs if row.get("qid") is not None}

    # 把人工最终判定整理为 qid -> {element: bool} 覆盖。
    # 只能仲裁实际发生冲突的题，避免任意改写非冲突结果。
    overrides = {}
    for fr in final_recs:
        qid = fr.get("qid")
        if qid is None:
            continue
        if str(qid) not in conflict_qids:
            raise ValueError("finalize 只能覆盖冲突题 qid: {0}".format(qid))
        ov = {}
        for k in ELEMENTS:
            # 兼容两种命名: cause / cause_correct
            if fr.get(k) is not None:
                value = fr.get(k)
                if not isinstance(value, bool):
                    raise ValueError("finalize 字段 {0} 必须是 JSON bool".format(k))
                ov[k] = value
            elif fr.get(k + "_correct") is not None:
                value = fr.get(k + "_correct")
                if not isinstance(value, bool):
                    raise ValueError("finalize 字段 {0}_correct 必须是 JSON bool".format(k))
                ov[k] = value
        if "evidence_score" in fr and fr.get("evidence_score") is not None:
            ov["evidence_score"] = float(fr.get("evidence_score"))
        if "format_ok" in fr and fr.get("format_ok") is not None:
            value = fr.get("format_ok")
            if not isinstance(value, bool):
                raise ValueError("finalize 字段 format_ok 必须是 JSON bool")
            ov["format_ok"] = value
        overrides[str(qid)] = ov

    n_overridden = 0
    for item in s.get("items", []):
        qid = item.get("qid")
        ov = overrides.get(str(qid))
        if not ov:
            continue
        n_overridden += 1
        # 人工判定覆盖
        if "cause" in ov:
            item["cause_correct"] = ov["cause"]
        if "effect" in ov:
            item["effect_correct"] = ov["effect"]
        if "evidence_hint" in ov:
            item["evidence_hint_correct"] = ov["evidence_hint"]
        if all(item.get(k + "_correct") is not None for k in ELEMENTS):
            item["causal_all_correct"] = bool(item["cause_correct"] and item["effect_correct"]
                                              and item["evidence_hint_correct"])
        if "evidence_score" in ov:
            item["evidence_score"] = ov["evidence_score"]
            item["evidence_grade"] = ("full" if ov["evidence_score"] >= 1.0
                                      else ("partial" if ov["evidence_score"] > 0.0 else "none"))
        if "format_ok" in ov:
            item["format_ok"] = ov["format_ok"]
        item["qualified"] = bool(item.get("available")
                                 and item.get("causal_all_correct")
                                 and item.get("evidence_score", 0) >= 1.0
                                 and item.get("format_ok"))

    # 由回填后的 items 重算聚合指标(简单重算,调用带(已判好)语义的分支)
    recomputed = _recompute_from_items(s.get("items", []))

    # 合并重算结果(保留 availability 等,覆盖 causal/evidence/format/qualified)
    old = s.get("metrics", {})
    merged = dict(old)
    merged["causal_accuracy"] = recomputed["causal_accuracy"]
    merged["evidence_completeness"] = recomputed["evidence_completeness"]
    merged["format_ok"] = recomputed["format_ok"]
    merged["qualified"] = recomputed["qualified"]
    merged["manual_override"] = {"n_overridden": n_overridden}
    s["metrics"] = merged
    s["meta"]["finalized_at"] = _now_iso()
    s["meta"]["manual_overrides"] = n_overridden

    if args.out:
        write_json(s, args.out)
        print("[eval_m1] finalize 完成:回填 {0} 条人工判定,输出 {1}".format(n_overridden, args.out))
    else:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


def _recompute_from_items(items):
    """从逐条判分明细重算 causal/evidence/format/qualified 聚合。"""
    n = len(items)
    causal_items = [x for x in items if x.get("has_gold")]
    n_causal = len(causal_items)
    all_k = sum(1 for x in causal_items if x.get("causal_all_correct"))
    ca_low, ca_high, ca_p = wilson_ci(all_k, n_causal)
    per_elem = {}
    for k in ELEMENTS:
        hit = sum(1 for x in causal_items if x.get(k + "_correct"))
        per_elem[k] = {"n_hit": hit, "rate": (hit / n_causal) if n_causal else 0.0}

    full = sum(1 for x in items if x.get("evidence_grade") == "full")
    part = sum(1 for x in items if x.get("evidence_grade") == "partial")
    none_ = sum(1 for x in items if x.get("evidence_grade") == "none")
    mean_ev = (sum(x.get("evidence_score", 0) for x in items) / n) if n else 0.0

    fmt_ok = sum(1 for x in items if x.get("format_ok"))
    fmt_rate = (fmt_ok / n) if n else 0.0

    available = [x for x in items if x.get("available")]
    n_avail = len(available)
    qual_avail = sum(1 for x in available if x.get("qualified"))
    qual_all = sum(1 for x in items if x.get("qualified"))
    qa_low, qa_high, qa_p = wilson_ci(qual_avail, n_avail)
    q_all_low, q_all_high, q_all_p = wilson_ci(qual_all, n)

    return {
        "causal_accuracy": {
            "n_causal": n_causal,
            "n_ungraded": n - n_causal,
            "n_all_correct": all_k,
            "causal_accuracy": ca_p,
            "ci95": [ca_low, ca_high],
            "per_element": per_elem,
        },
        "evidence_completeness": {
            "n": n, "n_full": full, "n_partial": part, "n_none": none_,
            "full_rate": (full / n) if n else 0.0,
            "partial_rate": (part / n) if n else 0.0,
            "none_rate": (none_ / n) if n else 0.0,
            "mean_score": mean_ev,
        },
        "format_ok": {"n": n, "n_ok": fmt_ok, "format_ok_rate": fmt_rate,
                      "requires_structure": False},
        "qualified": {
            "n_total": n, "n_available": n_avail,
            "n_qualified_avail": qual_avail, "n_qualified_all": qual_all,
            "end_to_end_qualified_rate": qa_p,
            "end_to_end_qualified_rate_all": q_all_p,
            "ci95": [qa_low, qa_high], "ci95_all": [q_all_low, q_all_high],
        },
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="eval_m1",
        description="M1 判分器接入草案(单文件,纯标准库,不调用 LLM)。",
    )
    parser.add_argument("--version", action="version", version="eval_m1 {0}".format(__version__))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("score", help="对 answers.jsonl 判分,输出 M1 指标").set_defaults(func=cmd_score)
    sub.add_parser("report-md", help="把 scores.json 渲染为 Markdown 报告").set_defaults(func=cmd_report_md)
    sub.add_parser("finalize", help="人工仲裁回填,重算指标").set_defaults(func=cmd_finalize)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        build_parser().print_help()
        return 0
    if argv[0] in ("--version", "-V"):
        print("eval_m1 {0}".format(__version__))
        return 0
    command, sub_argv = argv[0], argv[1:]
    sub = build_parser()
    try:
        # 找到 command 对应的子 parser
        target = None
        for action in sub._actions:
            if getattr(action, "dest", None) == "command" and hasattr(action, "choices"):
                target = action.choices.get(command)
                break
        if target is None or not hasattr(target, "_defaults") or "func" not in target._defaults:
            log_warn("未知命令: {0}(可用: score|report-md|finalize)".format(command))
            sub.print_help(sys.stderr)
            return 2
        func = target._defaults["func"]
        return func(sub_argv)
    except SystemExit:
        raise
    except (ValueError, TypeError) as exc:
        log_warn("参数/数据错误: {0}".format(exc))
        return 1
    except FileNotFoundError as exc:
        log_warn("文件不存在: {0}".format(exc))
        return 1
    except Exception as exc:
        log_warn("发生未预期错误: {0!r}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
