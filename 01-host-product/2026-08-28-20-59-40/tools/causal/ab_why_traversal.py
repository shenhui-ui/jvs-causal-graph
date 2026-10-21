# -*- coding: utf-8 -*-
"""A/B 回归件：**遍历类**改动的机械等价性验证（调真实入口，比遍历输出）。

为什么必须这样比（红线 12）
--------------------------
判据随改动类型走：
    - **局部**改动 ⇒ 比端点集；
    - **谓词**改动 ⇒ 比集合；
    - **遍历**改动 ⇒ **调真实入口跑前后、比遍历输出**。

`why()` 与 `chain_recall()` 都是遍历实现，且本项目**同一张图有两套遍历实现**
（`causal_graph.why` 与 `llm_answer.chain_recall`，各带一份 `_edge_expandable`）
⇒ 两套都要测，且比的是**有序边序列的逐字段快照**，不是「边集合相等」。

用法
----
    # 产出快照（对某一份模块目录）
    python ab_why_traversal.py snapshot --db <db> --module-dir <dir> \
        --out <jsonl> --limit 40

    # 比对两份快照
    python ab_why_traversal.py compare --a <jsonl> --b <jsonl>

`compare` 退出码：0 = 完全一致；1 = 存在差异（差异逐条打印）。
"""

import argparse
import json
import os
import sys

# 参与快照的边字段（顺序固定 ⇒ 序列化可比对）。
EDGE_FIELDS = ("edge_id", "id", "from_event", "to_event", "type", "strength",
               "confidence", "direction", "expandable", "rationale", "source_hash")


def _load(module_dir):
    """从指定目录导入 `causal_graph` 与 `llm_answer`（隔离 sys.path）。"""
    module_dir = os.path.abspath(module_dir)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    import causal_graph
    import llm_answer
    got = {causal_graph.__file__, llm_answer.__file__}
    for path in got:
        if os.path.abspath(os.path.dirname(path)) != module_dir:
            raise SystemExit("模块来源不符：期望 %s，实得 %s" % (module_dir, path))
    return causal_graph, llm_answer


def _ser_edge(edge):
    """把一条边序列化成可逐字段比对的字符串。"""
    parts = []
    for f in EDGE_FIELDS:
        if f in edge:
            v = edge[f]
            parts.append("%s=%r" % (f, v))
    return "|".join(parts)


def _ser_edges(edges):
    return [_ser_edge(e) for e in edges]


def _anchors(db, limit):
    """确定性地取锚点（按 id 排序，避免集合序漂移）。"""
    import sqlite3
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT from_event FROM causal_edges ORDER BY from_event LIMIT ?",
            (limit,))]
    finally:
        con.close()


def cmd_snapshot(args):
    cg, llm = _load(args.module_dir)
    rows = []
    for eid in _anchors(args.db, args.limit):
        why = cg.why(args.db, eid, max_hops=args.max_hops)
        chain = llm.chain_recall(args.db, eid, max_hops=args.max_hops)
        rows.append({
            "anchor": eid,
            "why_ok": why.get("ok"),
            "why_events": [e.get("id") for e in (why.get("events") or [])],
            "why_edges": _ser_edges(why.get("edges") or []),
            "why_skipped_type_reverse": why.get("skipped_type_reverse"),
            "chain_edges": _ser_edges(chain.get("edges") or []),
            "chain_skipped_reverse": chain.get("skipped_reverse"),
        })
    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print("快照 %d 锚点 -> %s" % (len(rows), args.out))
    return 0


def _read(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                out[row["anchor"]] = row
    return out


def _edge_ids(rows):
    """从序列化项里取 id。

    ⚠️ 本函数被两类字段共用：`*_edges`（序列化边串，形如 `id='A001'|...`）
    与 `why_events`（**裸事件 id 列表**，形如 `G02-C01-0001`）⇒ 必须两种都认，
    否则第二种会 `IndexError`（本轮实际踩到）。
    """
    out = []
    for row in rows:
        if "=" not in row:
            out.append(row.strip("'"))
            continue
        head = row.split("|", 1)[0]
        out.append(head.split("=", 1)[1].strip("'"))
    return out


def _summarize(field, va, vb, limit):
    """把一处字段差异压成一行可读摘要（列表类只报条数与 id 增减）。"""
    if isinstance(va, list) and isinstance(vb, list):
        ia, ib = _edge_ids(va), _edge_ids(vb)
        only_a = [x for x in ia if x not in set(ib)]
        only_b = [x for x in ib if x not in set(ia)]
        head = "len %d->%d ｜ 仅A %d ｜ 仅B %d" % (len(va), len(vb), len(only_a), len(only_b))
        if only_a[:limit]:
            head += " ｜ 仅A id: %s" % ",".join(only_a[:limit])
        if only_b[:limit]:
            head += " ｜ 仅B id: %s" % ",".join(only_b[:limit])
        if not only_a and not only_b:
            head += " ｜ ⚠️ 同 id 但顺序/字段不同"
        return head
    return "A=%r B=%r" % (va, vb)


def cmd_compare(args):
    a, b = _read(args.a), _read(args.b)
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    per_field = {}
    diffs = []
    for key in sorted(set(a) & set(b)):
        ra, rb = a[key], b[key]
        for field in sorted(set(ra) | set(rb)):
            if ra.get(field) != rb.get(field):
                per_field[field] = per_field.get(field, 0) + 1
                diffs.append((key, field, ra.get(field), rb.get(field)))

    print("锚点数：A=%d B=%d ｜ 仅 A=%d 仅 B=%d ｜ 字段差异=%d"
          % (len(a), len(b), len(only_a), len(only_b), len(diffs)))
    if per_field:
        print("按字段统计：%s" % ", ".join("%s=%d" % kv for kv in sorted(per_field.items())))
    for key in only_a[:5]:
        print("  仅 A 有锚点：%s" % key)
    for key in only_b[:5]:
        print("  仅 B 有锚点：%s" % key)
    for key, field, va, vb in diffs[:args.max_detail]:
        print("  差异 %s.%s：%s" % (key, field, _summarize(field, va, vb, args.max_ids)))
    if len(diffs) > args.max_detail:
        print("  ...（其余 %d 处差异省略，见 --max-detail）" % (len(diffs) - args.max_detail))
    if not diffs and not only_a and not only_b:
        print("判定：逐字段完全一致（遍历输出 0 差异）")
        return 0
    print("判定：存在差异")
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="why()/chain_recall() 遍历 A/B")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot")
    s.add_argument("--db", required=True)
    s.add_argument("--module-dir", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--max-hops", type=int, default=3)
    s.set_defaults(func=cmd_snapshot)

    c = sub.add_parser("compare")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--max-detail", type=int, default=8, help="最多打印多少处差异")
    c.add_argument("--max-ids", type=int, default=8, help="每处差异最多列几个 id")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
