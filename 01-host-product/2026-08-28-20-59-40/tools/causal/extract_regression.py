#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1-09 抽取回归(事件抽取)脚本。

用途:对 S3 阶段的事件抽取批结果做回归核对,输出 regression-report.md。

  - 读取 s3_* 批结果文件(路径列表用通配),对每个文件调用与 s4_pipeline 同源的
    extract_array(括号配对)定位其中的 JSON 数组,汇总为抽取事件集。
  - 与既有 393 真实事件(all-real-events.jsonl)做内容指纹 diff:
      新增(抽取到但 393 无)、缺失(393 有但未抽到)、命中。
  - 若存在 gold-events-real-50-init.jsonl,做 v3 口径核对(2-gram + after,
    参照 tools\causal\score_recall.py 思路):对每个金标准事件在抽取集中找候选
    (ts 相同 + 主体/对象命中,或 after 相似 ≥ 0.35),按「候选数升序」确定性
    贪心分配,输出 命中 / 漏抽 / 粒度合并 / 过提取 判定表。
  - 全程不调用真实 LLM。

用法示例:
  python extract_regression.py --base . --pattern 's3_*.txt'
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

# 复用 s4_pipeline 的括号配对数组提取(保证 M1-08 / M1-09 用同一接口)。
from s4_pipeline import extract_array, load_jsonl


# ---------------------------------------------------------------------------
# 规范化 / 指纹 / 2-gram 相似(参照 score_recall.py 思路)
# ---------------------------------------------------------------------------
def norm(s):
    return re.sub(r"[\s（）()【】\[\],。·、:：\-—/\\]", "", s or "")


def _ngrams(s, n=2):
    s = norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}


def _sim(a, b):
    A, B = _ngrams(a), _ngrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, min(len(A), len(B)))


def fingerprint(ev):
    """事件内容指纹:ts + 规范化(主体,动作,对象)。用于与 393 去重对比。"""
    return (ev.get("ts"), norm(ev.get("subject")),
            norm(ev.get("action")), norm(ev.get("object")))


# ---------------------------------------------------------------------------
# 抽取批结果收集
# ---------------------------------------------------------------------------
def collect_extracted(paths):
    """从多个批结果文件提取事件(list of dict),去重(按指纹)并统计来源。"""
    raw_events = []
    src_counts = Counter()
    skipped_files = []
    for path in paths:
        arr = extract_array(path) or []
        # 有些文件可能是纯 JSONL(每行一个事件),兜底处理。
        if not arr and _looks_like_jsonl(path):
            arr = [json.loads(x) for x in _read_lines(path) if x]
        evs = [e for e in arr if isinstance(e, dict) and e.get("event_id")]
        if evs:
            raw_events.extend(evs)
            src_counts[path] = len(evs)
        else:
            skipped_files.append(path)

    # 按指纹去重(同一事实不重复计数)。
    seen = {}
    for e in raw_events:
        fp = fingerprint(e)
        if fp not in seen:
            seen[fp] = e
    return list(seen.values()), raw_events, src_counts, skipped_files


def _read_lines(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def _looks_like_jsonl(path):
    """粗略判断文件是否整行为 JSONL(每行一个对象)。"""
    try:
        lines = _read_lines(path)
        return any(x.startswith("{") for x in lines) and len(lines) > 0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# v3 口径核对(参照 score_recall 的候选 + 确定性贪心)
# ---------------------------------------------------------------------------
def normalize_gold(g):
    """gold-events-real-50-init.jsonl 使用 gold_* 前缀字段,归一为通用字段名。"""
    m = {"gold_ts": "ts", "gold_type": "type", "gold_subject": "subject",
         "gold_action": "action", "gold_object": "object",
         "gold_before": "before", "gold_after": "after"}
    for k, v in m.items():
        if k in g and v not in g:
            g[v] = g[k]
    return g


def gold_candidates(g, exts):
    """对单个金标准事件在抽取集中找候选(与 score_recall.candidates 一致)。"""
    gs, go = norm(g.get("subject")), norm(g.get("object"))
    ga = norm(g.get("after") or "")
    c = []
    for idx, e in enumerate(exts):
        if e.get("ts") != g.get("ts"):
            continue
        es, eo = norm(e.get("subject")), norm(e.get("object"))
        ea = norm(e.get("after") or "")
        hit_subj = any(a and b and (a in b or b in a) and min(len(a), len(b)) >= 2
                       for a, b in [(gs, es), (gs, eo), (go, es), (go, eo)])
        hit_after = ga and ea and _sim(ga, ea) >= 0.35
        if hit_subj or hit_after:
            c.append(idx)
    return c


def match_gold(gold, exts):
    """v3 口径判定:返回 (hit, missed, merged, over_idx)。"""
    plan = []
    for gi, g in enumerate(gold):
        cands = gold_candidates(g, exts)
        plan.append((len(cands), gi, cands))

    used, hits, merged = set(), [], []
    for _, gi, cands in sorted(plan, key=lambda x: (x[0], x[1])):
        g = gold[gi]
        free = [i for i in cands if i not in used]
        if free:
            used.add(free[0])
            hits.append((g, free[0]))
        elif cands and any(i in used for i in cands):
            merged.append(g)
    missed = [g for _, g, cands in sorted(plan, key=lambda x: (x[0], x[1]))
              if not cands or not any(i in used for i in cands)]
    over_idx = [i for i in range(len(exts)) if i not in used]
    return hits, missed, merged, over_idx


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _diag(ev):
    return "[%s] %s | %s | %s %s %s" % (
        ev.get("ts"), ev.get("type"), ev.get("subject"),
        ev.get("action") or "", ev.get("object") or "", ev.get("after") or "")


def main():
    ap = argparse.ArgumentParser(description="M1-09 抽取回归(不调真实 LLM)")
    ap.add_argument("--base", default=".",
                    help="批结果所在目录(默认为当前目录)")
    ap.add_argument("--pattern", default="s3_*.txt",
                    help="批结果文件通配(默认 s3_*.txt);可逗号分隔多个模式")
    ap.add_argument("--events", default="all-real-events.jsonl",
                    help="既有 393 真实事件 jsonl")
    ap.add_argument("--gold", default="gold-events-real-50-init.jsonl",
                    help="v3 口径核对用的金标准(存在才核对)")
    ap.add_argument("--out", default="regression-report.md")
    args = ap.parse_args()

    # 1. 收集 s3_* 批结果文件(通配,可多个模式)。
    paths = []
    for pat in args.pattern.split(","):
        pat = pat.strip()
        if not pat:
            continue
        if os.path.isabs(pat) or os.sep in pat:
            paths += sorted(glob.glob(pat))
        else:
            paths += sorted(glob.glob(os.path.join(args.base, pat)))
    paths = sorted(set(paths))
    print("匹配到的 s3_* 文件数:", len(paths))
    for p in paths:
        print("  -", p)

    extracted, raw_events, src_counts, skipped = collect_extracted(paths)

    # 2. 事件数与来源分布。
    lines = []
    lines.append("# M1-09 事件抽取回归报告\n")
    lines.append("> 生成时间与口径:读取 s3_* 批结果,extract_array 提取;不调用真实 LLM。\n")
    lines.append("## 一、抽取事件统计\n")
    lines.append("- 批结果文件数:%d;成功解析:%d;无可抽取事件:%d"
                 % (len(paths), len(paths) - len(skipped), len(skipped)))
    lines.append("- **抽取事件总数(按指纹去重):%d**;原始抽取条数:%d"
                 % (len(extracted), len(raw_events)))
    if src_counts:
        lines.append("- 各文件抽取数(去重前):")
        for p, n in sorted(src_counts.items(), key=lambda x: -x[1]):
            lines.append("  - `%s`:%d" % (p, n))

    # 3. 与既有 393 的 diff。
    lines.append("\n## 二、与既有 393 真实事件的 diff(内容指纹)\n")
    have_393 = os.path.exists(args.events)
    if have_393:
        real = load_jsonl(args.events)
        real_fp = {fingerprint(e): e for e in real}
        ext_fp = {fingerprint(e): e for e in extracted}
        matched = [fp for fp in ext_fp if fp in real_fp]
        new_fp = [fp for fp in ext_fp if fp not in real_fp]
        miss_fp = [fp for fp in real_fp if fp not in ext_fp]
        lines.append("- 既有 393 真实事件数:%d" % len(real))
        lines.append("- 命中(抽取 ∩ 393):%d" % len(matched))
        lines.append("- **新增(抽取到但 393 无):%d** → 需人工判定是否误提取/新事实"
                     % len(new_fp))
        lines.append("- **缺失(393 有但未抽到):%d** → 真漏/口径漂移" % len(miss_fp))
        lines.append("\n### 新增清单(前 50)")
        for fp in new_fp[:50]:
            lines.append("- " + _diag(ext_fp[fp]))
        lines.append("\n### 缺失清单(前 50)")
        for fp in miss_fp[:50]:
            lines.append("- " + _diag(real_fp[fp]))
    else:
        lines.append("- 未找到 `%s`,跳过与 393 的 diff。" % args.events)

    # 4. 与 gold 的 v3 口径核对(2-gram + after)。
    lines.append("\n## 三、v3 口径核对(2-gram + after,参照 score_recall)\n")
    if os.path.exists(args.gold):
        gold = []
        for e in load_jsonl(args.gold):
            gold.append(normalize_gold(e))
        hits, missed, merged, over_idx = match_gold(gold, extracted)
        fact_slots = len(gold) - len(merged)
        total = len(extracted)
        prec = len(hits) / total if total else 0.0
        rec = len(hits) / fact_slots if fact_slots else 0.0
        lines.append("- 金标准:%d;粒度合并:%d;事实槽:%d" % (len(gold), len(merged), fact_slots))
        lines.append("- 抽取:%d;命中:%d;漏抽:%d;过提取:%d"
                     % (total, len(hits), len(missed), len(over_idx)))
        lines.append("- **召回率(按事实槽)=%.1f%%  精确率=%.1f%%**" % (rec * 100, prec * 100))
        lines.append("\n### 判定表命中(每金标准 → 抽取项映射)\n")
        lines.append("| 金标准事件 | 命中的抽取项 |")
        lines.append("|------------|--------------|")
        hit_map = dict((g.get("event_id"), idx) for (g, idx) in hits)
        for g in gold:
            idx = hit_map.get(g.get("event_id"))
            if idx is None:
                lines.append("| %s | **(未命中/漏抽)** |" % _diag(g))
            else:
                lines.append("| %s | #%d %s |" % (
                    _diag(g), idx, _diag(extracted[idx])))
        lines.append("\n### 粒度合并(一个抽取项承载两个金标准事实)\n")
        for g in merged:
            lines.append("- " + _diag(g))
        lines.append("\n### 过提取清单(待人工判定:语料真实→过提取;无依据→编造)\n")
        for i in over_idx:
            e = extracted[i]
            lines.append("- #%d %s (before=%s, after=%s)" % (
                i, _diag(e), e.get("before"), e.get("after")))
    else:
        lines.append("- 未找到 `%s`,跳过 v3 口径核对(金标准可选)。" % args.gold)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("已写出:", args.out)
    print("抽取事件数:", len(extracted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
