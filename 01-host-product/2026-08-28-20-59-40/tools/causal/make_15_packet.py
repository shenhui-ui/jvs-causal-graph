#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成 15 条新增分歧的补审包(flag 修复后升为分歧的条目,带事件原文)。

依据:dsh-takeover-20260902.md 中的 15 条清单(批/对)。输出 review-15-补审.csv/.md。
判据不变:编造 = evidence/rationale 引用了事件原文中不存在的内容。
"""
import collections
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"

# 来自 takeover 报告的 15 条(批, 对)
NEW_15 = [(1,48),(1,52),(2,62),(2,75),(4,181),(4,190),(4,194),(5,294),
          (6,336),(6,319),(10,555),(12,717),(12,690),(12,708),(12,696)]


def load_events():
    evs = {}
    with io.open(BASE + r"\all-real-events.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                evs[e["event_id"]] = e
    return evs


def evt_text(e):
    parts = [f"{e.get('type')}|{e.get('subject')}|{e.get('action')}|{e.get('object')}"]
    if e.get("before") is not None:
        parts.append(f"before:{e.get('before')}")
    if e.get("after"):
        parts.append(f"after:{e.get('after')}")
    return "  ".join(parts)


def main():
    evs = load_events()
    rows = []
    with io.open(BASE + r"\s4_audit_two-model.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    want = {(b, p) for b, p in NEW_15}
    picked = [r for r in rows if (r["batch_no"], r["pair_no_global"]) in want]
    # 校验齐全
    have = {(r["batch_no"], r["pair_no_global"]) for r in picked}
    missing = want - have
    if missing:
        print("! 缺失条目:", sorted(missing))

    csv = io.open(BASE + r"\review-15-补审.csv", "w", encoding="utf-8-sig")
    csv.write("序号,批,对,事件from(原文),事件to(原文),v5判定,v6判定,人工:是否编造(Y/N),人工:采纳哪个/最终类型强度,备注\n")
    for i, r in enumerate(picked, 1):
        v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—(未判)"
        v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—(未判)"
        ft = evt_text(evs.get(r["from_event"], {})).replace(",", "，").replace("\n", " ")
        tt = evt_text(evs.get(r["to_event"], {})).replace(",", "，").replace("\n", " ")
        csv.write(f"{i},{r['batch_no']},{r['pair_no_global']},\"{ft}\",\"{tt}\",{v5s},{v6s},,,,\n")
    csv.close()

    md = io.open(BASE + r"\review-15-补审.md", "w", encoding="utf-8")
    md.write("# 15 条新增分歧·补审包(flag 修复后)\n\n")
    md.write("> 背景:原 15 条因只比 strength 被误标「一致」,修复后升为「分歧」,需补审。\n")
    md.write("> 判据:编造 = evidence/rationale 引用了事件原文中不存在的内容。\n\n")
    md.write("| # | 批 | 对 | from(原文) | to(原文) | v5 | v6 | 编造? | 采纳 | 备注 |\n")
    md.write("|---|----|-----|-----------|----------|----|----|------|------|------|\n")
    for i, r in enumerate(picked, 1):
        v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—"
        v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—"
        ft = evt_text(evs.get(r["from_event"], {})).replace("|", "｜")[:80]
        tt = evt_text(evs.get(r["to_event"], {})).replace("|", "｜")[:80]
        md.write(f"| {i} | {r['batch_no']} | {r['pair_no_global']} | {ft} | {tt} | {v5s} | {v6s} |  |  |  |\n")
    md.write("\n## 判定结论(填)\n\n- 编造边总数:____ / 15\n- 编造条目:____\n")
    md.close()
    print(f"补审包: {len(picked)}/15 条 → review-15-补审.csv/.md")


if __name__ == "__main__":
    raise SystemExit(main())
