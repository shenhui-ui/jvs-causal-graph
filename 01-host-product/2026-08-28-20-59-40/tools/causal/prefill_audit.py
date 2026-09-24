#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""168 对编造边抽检·AI 预填:两模型判定一致性 + 编造风险预标(纯本地,无 LLM)。

风险分级(预填,供人工复核):
  - [一致] 两模型都判,且 strength 相同 → 低风险
  - [分歧] 两模型都判,但 strength/type 不同 → 需人工(重点)
  - [单判] 只有一模型判 → 需人工(中等)
  - [未判] 两模型都未判边 → 无异常(候选对本身被两模型跳过)
  - conf<0.7 标记
输出:s4_audit-result-prefill.csv / .md
"""
import collections
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


def main():
    rows = []
    with io.open(BASE + r"\s4_audit_two-model.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    buckets = collections.defaultdict(list)
    for r in rows:
        v5, v6 = r.get("v5"), r.get("v6")
        if v5 and v6:
            if v5.get("strength") == v6.get("strength"):
                flag = "一致"
            else:
                flag = "分歧"
        elif v5 or v6:
            flag = "单判"
        else:
            flag = "未判"
        low_conf = (v5 and v5.get("conf") and v5["conf"] < 0.7) or (v6 and v6.get("conf") and v6["conf"] < 0.7)
        r["flag"] = flag
        r["low_conf"] = bool(low_conf)
        buckets[flag].append(r)

    # CSV 预填
    csv = io.open(BASE + r"\s4_audit-result-prefill.csv", "w", encoding="utf-8-sig")
    csv.write("batch,pair,v5(type/strength/conf),v6(type/strength/conf),预标,低置信?,人工判定(编造?Y/N),备注\n")
    for r in rows:
        v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—"
        v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—"
        csv.write(f"{r['batch_no']},{r['pair_no_global']},{v5s},{v6s},{r['flag']},{'是' if r['low_conf'] else ''},,\n")
    csv.close()

    # MD 汇总
    md = io.open(BASE + r"\s4_audit-result-prefill.md", "w", encoding="utf-8")
    md.write("# 168 对编造边抽检·AI 预填(2026-08-31)\n\n")
    md.write("> 口径:两模型(v5=glm-5.2 / v6=flash-lite)对照;人工复核决定正式图库;主判据=编造边 0。\n\n")
    order = ["一致", "分歧", "单判", "未判"]
    total = len(rows)
    for k in order:
        v = buckets.get(k, [])
        md.write(f"## {k}:{len(v)}/168\n\n")
        if k in ("一致", "未判"):
            md.write(f"- {k} 项为低风险,可批量通过(人工抽看即可)。\n\n")
            continue
        md.write("| # | 批 | 对 | 事件对 | v5(glm-5.2) | v6(flash-lite) | 低置信 | 人工判定 | 备注 |\n")
        md.write("|---|----|-----|--------|------------|------------|------|----------|------|\n")
        for i, r in enumerate(v, 1):
            pair = f"{r['from_event']}→{r['to_event']}"
            v5s = f"{r['v5']['type']}/{r['v5']['strength']}/{r['v5']['conf']}" if r["v5"] else "—"
            v6s = f"{r['v6']['type']}/{r['v6']['strength']}/{r['v6']['conf']}" if r["v6"] else "—"
            lc = "⚠" if r["low_conf"] else ""
            md.write(f"| {i} | {r['batch_no']} | {r['pair_no_global']} | {pair} | {v5s} | {v6s} | {lc} |  |  |\n")
        md.write("\n")
    md.write("## 汇总\n\n")
    for k in order:
        md.write(f"- {k}: {len(buckets.get(k, []))}\n")
    md.write(f"- 总: {total}\n")
    md.write("- **不需人工逐条**:一致+未判批量过;重点看分歧+单判(共 X 条)。\n")
    md.close()

    print("预填统计:", {k: len(v) for k, v in buckets.items()})
    print("分歧+单判合计:", len(buckets["分歧"]) + len(buckets["单判"]))
    print("→ s4_audit-result-prefill.csv / .md")


if __name__ == "__main__":
    raise SystemExit(main())