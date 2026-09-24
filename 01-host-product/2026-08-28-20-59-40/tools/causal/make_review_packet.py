#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成 168 对编造边复核·分派包(带事件原文,供人工分派)。

输出:
  - review-52-关键对.csv / .md   : 52 条(分歧15+单判37)需逐条人工判定
  - review-116-批量通过.csv      : 116 条(一致35+未判81)可批量抽看
判据:编造边 = 证据/rationale 引用了事件原文中不存在的内容 → 记为编造,需修复重判。
"""
import collections
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


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

    critical, pass_ = [], []
    for r in rows:
        v5, v6 = r.get("v5"), r.get("v6")
        if v5 and v6:
            # 2026-09-02 修复: 原 definition 只比 strength 不比 type,导致 15 条 type 不同的边被误标"一致"
            flag = "一致" if (v5.get("strength") == v6.get("strength")
                              and v5.get("type") == v6.get("type")) else "分歧"
        elif v5 or v6:
            flag = "单判"
        else:
            flag = "未判"
        r["flag"] = flag
        r["from_text"] = evt_text(evs.get(r["from_event"], {}))
        r["to_text"] = evt_text(evs.get(r["to_event"], {}))
        (critical if flag in ("分歧", "单判") else pass_).append(r)

    # 关键对 CSV
    csv = io.open(BASE + r"\review-52-关键对.csv", "w", encoding="utf-8-sig")
    csv.write("序号,批,对,事件from(原文),事件to(原文),v5判定,v6判定,人工:是否编造(Y/N),人工:采纳哪个/最终强度,备注\n")
    for i, r in enumerate(critical, 1):
        v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—(未判)"
        v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—(未判)"
        # 去 CSV 里的逗号/换行,避免破坏单元格
        ft = r["from_text"].replace(",", "，").replace("\n", " ")
        tt = r["to_text"].replace(",", "，").replace("\n", " ")
        csv.write(f"{i},{r['batch_no']},{r['pair_no_global']},\"{ft}\",\"{tt}\",{v5s},{v6s},,,,\n")
    csv.close()

    # 关键对 MD(供阅读/打印)
    md = io.open(BASE + r"\review-52-关键对.md", "w", encoding="utf-8")
    md.write("# 编造边复核·关键 52 条(分歧 15 + 单判 37)\n\n")
    md.write("> 判据:**编造 = evidence/rationale 引用了事件原文中不存在的内容**。逐条判定是否编造(Y/N),并标采纳哪个模型/最终强度。\n\n")
    by_flag = collections.defaultdict(list)
    for r in critical:
        by_flag[r["flag"]].append(r)
    for flag in ("分歧", "单判"):
        md.write(f"## {flag}({len(by_flag[flag])} 条)\n\n")
        md.write("| # | 批 | 对 | from(原文) | to(原文) | v5 | v6 | 编造? | 采纳 | 备注 |\n")
        md.write("|---|----|-----|-----------|----------|----|----|------|------|------|\n")
        for i, r in enumerate(by_flag[flag], 1):
            v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—"
            v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—"
            ft = r["from_text"].replace("|", "｜")[:80]
            tt = r["to_text"].replace("|", "｜")[:80]
            md.write(f"| {i} | {r['batch_no']} | {r['pair_no_global']} | {ft} | {tt} | {v5s} | {v6s} |  |  |  |\n")
        md.write("\n")
    md.write("## 判定结论(填)\n\n- 编造边总数:____ / 52\n- 编造条目清单:____\n")
    md.close()

    # 批量通过 CSV
    pcsv = io.open(BASE + r"\review-116-批量通过.csv", "w", encoding="utf-8-sig")
    pcsv.write("批,对,事件对,类型(flag),v5,v6\n")
    for r in pass_:
        v5s = f"{r['v5']['type']}/{r['v5']['strength']}" if r["v5"] else "—"
        v6s = f"{r['v6']['type']}/{r['v6']['strength']}" if r["v6"] else "—"
        pcsv.write(f"{r['batch_no']},{r['pair_no_global']},{r['from_event']}→{r['to_event']},{r['flag']},{v5s},{v6s}\n")
    pcsv.close()

    print(f"关键对(分歧+单判): {len(critical)} → review-52-关键对.csv/.md")
    print(f"批量通过(一致+未判): {len(pass_)} → review-116-批量通过.csv")
    print("flag 分布:", dict(collections.Counter(r['flag'] for r in rows)))


if __name__ == "__main__":
    raise SystemExit(main())
