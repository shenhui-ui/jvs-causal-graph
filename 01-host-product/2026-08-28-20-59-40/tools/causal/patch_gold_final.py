#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""金标准人工定稿:G005/G030 两处"待人工复核"→ 定稿(用户确认 2026-08-30)。"""
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
PATH = BASE + r"\gold-events-real-50-init.jsonl"

rows = []
with io.open(PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

FIX = {
    "G005": {"gold_before": None,
             "备注": "change类全数纳入;before原值原文未给出 → 人工定稿为 None(最小可证实表述;2026-08-30 负责人确认)"},
    "G030": {"gold_before": "finger6d checker 内",
             "备注": "change类全数纳入;迁移前位置由原句「finger6d checker 里的内容」直接支撑 → 人工定稿(2026-08-30 负责人确认)"},
}
for r in rows:
    if r["gid"] in FIX:
        r.update(FIX[r["gid"]])
        r["review_meta"] = {"reviewed_by": "user", "review_date": "2026-08-30",
                            "item": r["gid"], "verdict": "定稿"}

with io.open(PATH, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

left = [r["gid"] for r in rows if "待人工复核" in (r.get("备注") or "")]
print("定稿完成;剩余待复核:", left if left else "无(2/2 已清)")
print("G005:", [r for r in rows if r["gid"] == "G005"][0]["gold_before"])
print("G030:", [r for r in rows if r["gid"] == "G030"][0]["gold_before"])
