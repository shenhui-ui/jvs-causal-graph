#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成题集审定视图 md:30 条拆字段 + 高亮问题点(人工审定专用)。"""
import io
import json
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
SRC = BASE + r"\query-set-real-init.jsonl"
OUT = BASE + r"\query-set-real-审定视图.md"


def flag(q):
    marks = []
    if q["qid"] == "Q001":
        marks.append("⚠️ 引用笔误已修正(R600162→R60162),请确认")
    if q["qid"] == "Q015":
        marks.append("🔴 唯一 graph_dependency=no,待你裁决:剔除 or 保留标注")
    txt = json.dumps(q.get("gold_answer", ""), ensure_ascii=False)
    if "R900" in txt or "R701" in txt:
        marks.append("🔶 引用低置信群聊事件(R900xx/unknown ts 0.3),建议保留+标注")
    if q.get("type") == "证据定位":
        marks.append("🔷 证据定位题:核对 [src: corpus#hash] 格式")
    if q.get("source", "").startswith("C01b"):
        marks.append("◻ orig=C01b 改写(已改真实语境,抽查溯源)")
    return marks


def main():
    qs = []
    with io.open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qs.append(json.loads(line))
    lines = ["# 题集审定视图(30 条,人工审定版)",
             "",
             "> 来源:`query-set-real-init.jsonl` ｜ 生成:2026-08-30 ｜ 用途:对照审定(目标 30 分钟~1 小时)",
             "> 审定后:落 `query-set-real-v1.jsonl`(盖章版)+ 代表性评估记录。",
             "",
             "## 速览",
             "",
             "| 构成 | 数量 | 审核点 |",
             "|---|---|---|",
             "| 为什么改 | 12 | 全部 C01b 改写,抽查行为链 |",
             "| 改了什么 | 8 | Q015 待裁决(剔除/保留) |",
             "| 因果推理 | 5 | 三要素与事件链一致性 |",
             "| 证据定位 | 5 | [src: hash] 格式 |",
             "",
             "**重点条目:Q001(确认笔误)、Q015(裁决)、3 条 R900xx 低置信(保留+标注)。**",
             ""]
    for i, q in enumerate(qs, 1):
        g = q.get("gold_answer", {})
        marks = flag(q)
        m = " ｜ ".join(marks) if marks else "—"
        lines.append(f"### {q['qid']} [{q.get('type')}] {q.get('difficulty')} ｜ {q.get('source')}")
        lines.append(f"- 问题:{q.get('question')}")
        lines.append(f"- 因(gold):{g.get('cause', '')}")
        lines.append(f"- 果(gold):{g.get('effect', '')}")
        lines.append(f"- 证据提示(gold):{g.get('evidence_hint', '')}")
        lines.append(f"- graph_dependency:{q.get('graph_dependency')}"
                     + (f" ｜ 原因:{q.get('graph_dependency_reason')}" if q.get("graph_dependency_reason") else ""))
        lines.append(f"- 审核标记:{m}")
        lines.append(f"- 人工裁定(填):__________")
        lines.append("")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[ok] {len(qs)} 条 → {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
