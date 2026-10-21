#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""judge_drift_guard.py — 判分侧漂移的**前置自证件**（P1-3，把红线 25 制度化）。

## 它拦的是什么

**LLM 判分会随时间漂移**：同一模型名、同一输入，09-18 判 `cause=true`、09-23 判 `false`，
而**我方可控输入逐项未变**（判分器代码、端点、模型名、提示词模板、gold、答案文本全同）。
⇒ **新旧 `scores.json` 之差不得当效应量**；凡要跨时间对照判分派生的指标，
**必须先跑本件自证「同输入重判」**。

## 判据（全部自包含在 `decide()` 内 —— 红线 10：守卫必须自包含在被测的那个函数里）

1. **集合完备性**：两侧 qid 集合必须**完全相等**（同轴 + 有重叠，红线 18）。
   缺一题 / 多一题 / 空交集 ⇒ `REFUSE`，**不得**退化成「漂移 0」这种假阴性。
2. **判分有效性**：任一侧 `judge_ok is not True` ⇒ `REFUSE`（红线 23：**判分失败 ≠ 判定为假**，
   拿占位的 `false` 去比会凭空造出漂移）。
3. **字段类型**：四个判据字段必须是 `bool`；缺失或非 bool ⇒ `REFUSE`。
4. **qid 唯一**：任一侧出现重复 qid ⇒ `REFUSE`。
5. **同输入机器核验**（红线 25 的核心）：两侧都带 `input_fingerprint` 时，逐题必须**相同**；
   不同 ⇒ `NOT_SAME_INPUT`（**这不是「同输入重判」，而是两次不同输入的运行** ——
   它们之间的翻转**不能**归因于判分侧漂移，须先做真正的同输入重判）。
   任一侧缺该字段 ⇒ 无法机器核验，报告里显式标注 `same_input_verified=false`（不静默放过）。
6. 以上都过，才逐题比较 `cause/effect/evidence_hint/available`；任一翻转 ⇒ `DRIFT`。

**退出码**：`0` = PASS；`1` = DRIFT / REFUSE / NOT_SAME_INPUT（**一律拒绝**，不得据以出结论）；
`2` = 用法错误。

## 用法

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
G="C:/Users/<user>/jvs-src/01-host-product/2026-08-28-20-59-40/tools/causal/judge_drift_guard.py"

# ① 离线（门禁用；零 LLM）：重放录制好的判分响应
"$PY" -B "$G" --baseline <旧 scores.json> --replay <responses.jsonl> --out <report.json>

# ② 对比两份**判分产物**（同输入重判的常见形态）
"$PY" -B "$G" --baseline <旧 scores.json> --replay-scores <新 scores.json> \
      --qids D10-H01 --out <report.json>

# ③ 在线（有配额时）：走真实入口重判同一批输入
"$PY" -B "$G" --baseline <旧 scores.json> --answers <a.jsonl> --gold <g.jsonl> \
      --live --out <report.json>

# ④ 负向自证：13 类夹具，含「能拒绝」「不是恒拒」「拒绝非同输入」三个方向
"$PY" -B "$G" --self-test
```

`--replay` 文件格式（JSONL，一行一题）：`{"qid": "D10-H01", "raw": "<判分器返回的 JSON 原文>"}`
`--qids a,b,c`：**显式声明同轴题集**（两侧都按此过滤后再做完备性检查）。
⚠️ 它不是绕过完备性检查的手段 —— 过滤后仍逐题要求两侧齐备。
"""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
JUDGE_PATH = Path(os.environ.get("JVS_JUDGE_MODULE_PATH") or (HERE / "llm_judge.py"))
COMPARE_FIELDS = ("cause", "effect", "evidence_hint", "available")
FP_FIELD = "input_fingerprint"


def _load_judge(path=None):
    """加载判分器（复用其 parse_json，保证与生产同源解析）。"""
    path = Path(path or JUDGE_PATH)
    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("llm_judge", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["llm_judge"] = mod
    spec.loader.exec_module(mod)
    return mod


def _index(items):
    idx, duplicates = {}, []
    for item in items:
        if not isinstance(item, dict) or item.get("qid") is None:
            continue
        qid = str(item["qid"])
        if qid in idx:
            duplicates.append(qid)
        idx[qid] = item
    return idx, duplicates


def decide(baseline_items, replay_items):
    r"""自包含判据（红线 10）。返回 (verdict, reasons, flips, same_input_verified)。

    verdict ∈ {"PASS", "DRIFT", "REFUSE", "NOT_SAME_INPUT"}。
    """
    base, dup_b = _index(baseline_items)
    replay, dup_r = _index(replay_items)
    reasons = []
    if dup_b:
        reasons.append(f"baseline 存在重复 qid：{sorted(set(dup_b))}")
    if dup_r:
        reasons.append(f"replay 存在重复 qid：{sorted(set(dup_r))}")
    if not base:
        reasons.append("baseline 无有效条目（qid 缺失或为空）")
    if not replay:
        reasons.append("replay 无有效条目（qid 缺失或为空）")
    if reasons:
        return "REFUSE", reasons, [], False

    only_base = sorted(set(base) - set(replay))
    only_replay = sorted(set(replay) - set(base))
    if only_base or only_replay:
        reasons.append(f"题集不完备（同轴前提不成立）：baseline 独有 {only_base}；"
                       f"replay 独有 {only_replay}")
        return "REFUSE", reasons, [], False
    if not set(base) & set(replay):  # 显式留痕：不得把空交集报成「无漂移」
        reasons.append("两侧 qid 交集为空 —— 不可比，拒绝给出任何漂移结论")
        return "REFUSE", reasons, [], False

    flips, invalid = [], []
    for qid in sorted(base):
        for label, row in (("baseline", base[qid]), ("replay", replay[qid])):
            if row.get("judge_ok") is not True:
                invalid.append(f"{qid}:{label} judge_ok={row.get('judge_ok')!r}")
                continue
            for field in COMPARE_FIELDS:
                if type(row.get(field)) is not bool:
                    invalid.append(f"{qid}:{label}.{field}={row.get(field)!r} 非 bool")
    if invalid:
        reasons.append("判分有效性/字段类型不满足（红线 23：判分失败 ≠ 判定为假）："
                       + "；".join(invalid[:6]) + ("…" if len(invalid) > 6 else ""))
        return "REFUSE", reasons, [], False

    fp_rows = [(q, base[q].get(FP_FIELD), replay[q].get(FP_FIELD)) for q in sorted(base)]
    verified = all(isinstance(b, str) and b and isinstance(r, str) and r for _, b, r in fp_rows)
    mismatched = [q for q, b, r in fp_rows
                  if isinstance(b, str) and b and isinstance(r, str) and r and b != r]
    if mismatched:
        reasons.append("两侧 input_fingerprint 不一致 ⇒ **不是同输入重判**（红线 25）："
                       f"{mismatched[:6]}"
                       + ("…" if len(mismatched) > 6 else "")
                       + " —— 两次运行输入不同，其间翻转不得归因判分侧漂移；"
                         "请先做真正的同输入重判")
        return "NOT_SAME_INPUT", reasons, [], False
    if not verified:
        reasons.append("⚠️ 任一侧缺 input_fingerprint ⇒ 无法机器核验「同输入」"
                       "（结论按不可核验处理，须人工确认答案文本/gold/模型逐项一致）")

    for qid in sorted(base):
        for field in COMPARE_FIELDS:
            if base[qid][field] != replay[qid][field]:
                flips.append((qid, field, base[qid][field], replay[qid][field]))
    if flips:
        reasons.append(f"检出判分侧漂移：{len(flips)} 处翻转 / {len(base)} 题 × "
                       f"{len(COMPARE_FIELDS)} 字段"
                       + ("（同输入已机器核验）" if verified else ""))
        return "DRIFT", reasons, flips, verified
    reasons.append(f"同输入重判一致（{len(base)} 题 × {len(COMPARE_FIELDS)} 字段，0 翻转"
                   + ("，同输入已机器核验" if verified else "，同输入未能机器核验") + "）")
    return "PASS", reasons, [], verified


# ------------------------------- 输入装载 -------------------------------

def load_scores(path):
    with io.open(path, encoding="utf-8-sig") as f:
        doc = json.load(f)
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return doc["items"]
    raise ValueError(f"{path} 不是判分产物（缺 items 数组）")


def load_replay(path, judge):
    rows = []
    with io.open(path, encoding="utf-8-sig") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            qid = str(rec["qid"])
            kwargs = {"with_rationale": True} if hasattr(judge, "RATIONALE_FIELD") else {}
            try:
                fields = judge.parse_json(rec.get("raw"), **kwargs)
                rows.append({"qid": qid, "judge_ok": True, **fields})
            except Exception as exc:  # noqa: BLE001 —— 解析失败按「判分未完成」处理，不当假
                rows.append({"qid": qid, "judge_ok": False,
                             "error_type": getattr(exc, "error_type", "parse_error"),
                             **dict.fromkeys(COMPARE_FIELDS)})
    return rows


def filter_qids(items, qids):
    if not qids:
        return items
    want = set(qids)
    return [i for i in items if isinstance(i, dict) and str(i.get("qid")) in want]


def run_live(answers, gold, out_dir, parse_retries=1):
    """走**真实入口**（llm_judge.py 子进程）重判，不另写一套判分路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "scores.json"
    cmd = [sys.executable, "-B", str(HERE / "llm_judge.py"),
           "--answers", str(answers), "--gold", str(gold),
           "--out", str(out_path), "--parse-retries", str(parse_retries)]
    proc = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if not out_path.exists():
        raise RuntimeError(f"live 重判未产出 scores.json（rc={proc.returncode}）："
                           f"{(proc.stderr or proc.stdout or '')[-400:]}")
    return load_scores(out_path), proc


# ------------------------------- 主流程 -------------------------------

def main():
    ap = argparse.ArgumentParser(description="判分侧漂移前置自证件（P1-3）")
    ap.add_argument("--baseline", help="旧判分产物 scores.json")
    ap.add_argument("--replay", help="录制响应 JSONL（离线模式）")
    ap.add_argument("--replay-scores", help="另一份判分产物 scores.json（同输入重判对照）")
    ap.add_argument("--answers", help="答案 JSONL（--live 模式）")
    ap.add_argument("--gold", help="gold JSONL（--live 模式）")
    ap.add_argument("--live", action="store_true", help="真调判分器重判（消耗 LLM 配额）")
    ap.add_argument("--live-out", default=None, help="live 模式中间产物目录")
    ap.add_argument("--qids", default=None, help="逗号分隔的同轴题集（两侧同过滤）")
    ap.add_argument("--out", help="漂移报告输出路径（JSON）")
    ap.add_argument("--self-test", action="store_true", help="负向自证：13 类夹具")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.baseline or not (args.replay or args.replay_scores or args.live):
        ap.error("需要 --baseline 且（--replay / --replay-scores / --live --answers --gold）")

    qids = [q.strip() for q in (args.qids or "").split(",") if q.strip()]
    judge = _load_judge()
    baseline_items = filter_qids(load_scores(args.baseline), qids)
    if args.live:
        if not (args.answers and args.gold):
            ap.error("--live 需要 --answers 与 --gold")
        out_dir = args.live_out or tempfile.mkdtemp(prefix="drift-live-")
        replay_items, proc = run_live(args.answers, args.gold, out_dir)
        replay_items = filter_qids(replay_items, qids)
        mode, desc = "live", f"{args.answers}（重判于 {out_dir}）"
        print(f"[drift-guard] live 重判 rc={proc.returncode}", flush=True)
    elif args.replay_scores:
        replay_items = filter_qids(load_scores(args.replay_scores), qids)
        mode, desc = "replay-scores", args.replay_scores
    else:
        replay_items = filter_qids(load_replay(args.replay, judge), qids)
        mode, desc = "replay", args.replay

    verdict, reasons, flips, verified = decide(baseline_items, replay_items)
    report = {
        "guard": "judge_drift_guard",
        "guard_scope": "红线 25 —— 跨时间对照判分派生指标前的同输入重判自证",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "baseline": str(args.baseline),
        "replay": desc,
        "qids": qids or "全部",
        "verdict": verdict,
        "same_input_verified": verified,
        "n_compared": len(baseline_items),
        "flips": [{"qid": q, "field": f, "baseline": b, "replay": r} for q, f, b, r in flips],
        "reasons": reasons,
        "usage_rule": ("verdict=PASS 才可把两侧 scores 当同轴对照；DRIFT / REFUSE / "
                       "NOT_SAME_INPUT 时**不得**以新旧 scores 之差作效应量"),
    }

    print("=" * 84)
    print(f" 判分侧漂移前置自证 · mode={mode} · verdict={verdict} · "
          f"同输入核验={verified} · 题数={len(baseline_items)}")
    print("=" * 84)
    print(f"  baseline: {args.baseline}")
    print(f"  replay  : {desc}")
    for reason in reasons:
        print(f"  · {reason}")
    for q, f, b, r in flips:
        print(f"    {q:<12} {f:<14} {str(b):<6} -> {str(r)}")
    if verdict != "PASS":
        print("  ⇒ **拒绝**：不得以新旧 scores 之差作效应量（红线 25）")
    print("=" * 84)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f" 报告已写: {args.out}", flush=True)
    return 0 if verdict == "PASS" else 1


# ------------------------------- 负向自证 -------------------------------

def _rows(n=3, fp=None, **flags):
    out = []
    for i in range(1, n + 1):
        row = {"qid": f"Q{i:03d}", "judge_ok": True,
               **{f: flags.get(f, True) for f in COMPARE_FIELDS}}
        if fp is not None:
            row[FP_FIELD] = fp
        out.append(row)
    return out


def _fixtures():
    base = _rows(fp="fp-A")
    one_flip = [dict(r) for r in base]
    one_flip[1]["cause"] = False
    many_flip = [dict(r, cause=False, effect=False) for r in base]
    no_overlap = [dict(r, qid=f"X{i:03d}") for i, r in enumerate(base, 1)]
    bad_judge_ok = [dict(r) for r in base]
    bad_judge_ok[0]["judge_ok"] = False
    non_bool = [dict(r) for r in base]
    non_bool[0]["cause"] = "true"
    missing_field = [dict(r) for r in base]
    missing_field[0].pop("evidence_hint")
    dup_qid = [dict(r) for r in base] + [dict(base[0])]
    other_fp = _rows(fp="fp-B")
    no_fp = _rows()
    return [
        ("① 完全一致 ⇒ PASS（正对照：证明不是恒拒）", base, [dict(r) for r in base], "PASS", True),
        ("② 翻转 1 个布尔 ⇒ DRIFT", base, one_flip, "DRIFT", True),
        ("③ 成片翻转 ⇒ DRIFT（量级无关，全部检出）", base, many_flip, "DRIFT", True),
        ("④ 无 qid 交集 ⇒ REFUSE（不得报「漂移 0」）", base, no_overlap, "REFUSE", False),
        ("⑤ 一侧缺题（集合不完备）⇒ REFUSE", base, [dict(r) for r in base[:2]], "REFUSE", False),
        ("⑥ 一侧 judge_ok=false ⇒ REFUSE（红线 23）", base, bad_judge_ok, "REFUSE", False),
        ("⑦ 判据字段非 bool ⇒ REFUSE", base, non_bool, "REFUSE", False),
        ("⑧ 判据字段缺失 ⇒ REFUSE", base, missing_field, "REFUSE", False),
        ("⑨ 重复 qid ⇒ REFUSE", base, dup_qid, "REFUSE", False),
        ("⑩ replay 为空 ⇒ REFUSE", base, [], "REFUSE", False),
        ("⑪ 指纹不同（非同输入）⇒ NOT_SAME_INPUT", base, other_fp, "NOT_SAME_INPUT", False),
        ("⑫ 指纹缺失 ⇒ 仍判定但 same_input_verified=false",
         no_fp, [dict(r, cause=False) for r in no_fp], "DRIFT", False),
        ("⑬ 指纹缺失且完全一致 ⇒ PASS 但未核验",
         no_fp, [dict(r) for r in no_fp], "PASS", False),
    ]


def self_test():
    print("=" * 84)
    print(" judge_drift_guard.py --self-test  （判据：能拒绝才算校验，红线 10）")
    print("=" * 84)
    ok = True
    for label, base, replay, expected, expect_verified in _fixtures():
        verdict, reasons, flips, verified = decide(base, replay)
        flag = verdict == expected and verified == expect_verified
        ok &= flag
        print(f"  {'[PASS]' if flag else '[FAIL]'} {label}")
        if not flag:
            print(f"         期望 {expected}/{expect_verified} 实得 {verdict}/{verified} | {reasons}")
        elif verdict == "DRIFT":
            print(f"         翻转 {len(flips)} 处（{sorted({f[0] for f in flips})}）")
        else:
            print(f"         {reasons[0][:76]}")
    kinds = [e for *_, e, _ in _fixtures()]
    n_refuse, n_drift, n_pass = (kinds.count("REFUSE"), kinds.count("DRIFT"), kinds.count("PASS"))
    ok &= (n_refuse >= 6 and n_drift >= 2 and n_pass >= 1)
    print("-" * 84)
    print(f"  共 {len(kinds)} 类夹具：REFUSE {n_refuse} · DRIFT {n_drift} · "
          f"NOT_SAME_INPUT {kinds.count('NOT_SAME_INPUT')} · PASS {n_pass}")
    print(f"  ⇒ {'判据既能拒绝、也不是恒拒' if ok else '自证失败 —— 判据不可信'}")
    print("=" * 84)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
