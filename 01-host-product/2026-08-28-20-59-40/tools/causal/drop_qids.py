#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""删除 answers-llm.jsonl 中指定 qid 的行(补跑失败题用)。"""
import io
import sys

p = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump\m1_10_v1r4\answers-llm.jsonl"
drop = {"Q017", "Q018", "Q030"}
lines = [l for l in io.open(p, encoding="utf-8-sig") if l.strip()]
keep = []
for l in lines:
    import json
    try:
        qid = json.loads(l)["qid"]
    except Exception:
        keep.append(l)
        continue
    if qid in drop:
        continue
    keep.append(l)
io.open(p, "w", encoding="utf-8", newline="").write("".join(keep))
print(f"rows {len(keep)} (dropped {len(lines) - len(keep)})")
