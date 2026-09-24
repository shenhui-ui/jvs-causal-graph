#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""v2 试判结果评分:v2sample_reply ↔ sample-pairs-60 预期标签对照,输出验收表预填 + 统计。"""
import io
import json
import re
from collections import Counter


def extract_array(path):
    raw = io.open(path, encoding="utf-8").read()
    start = None
    for m in re.finditer(r"\[", raw):
        if raw[m.start():m.start() + 2] in ("[{", "[\n", "[ "):
            start = m.start()
            break
    if start is None:
        return []
    depth = 0
    for idx in range(start, len(raw)):
        if raw[idx] == "[":
            depth += 1
        elif raw[idx] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:idx + 1])
    return []


def main():
    base = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
    edges = extract_array(base + r"\s4_v2sample_reply.txt")
    pairs = []
    with io.open(base + r"\sample-pairs-60.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    by_no = {i + 1: p for i, p in enumerate(pairs)}  # payload 中 pair_no 按文件行序编列

    cat_stat = {}
    rows = []
    judged_no = set()
    for e in edges:
        pn = e.get("pair_no") or 0
        p = by_no.get(pn)
        cat = (p or {}).get("category", "?")
        st = e.get("strength", "?")
        cat_stat.setdefault(cat, Counter())[st] += 1
        if p and cat in ("strong", "weak"):
            ok = (st == cat)
        else:
            ok = False
        rows.append({"pair_no": pn, "category": cat, "expected": (p or {}).get("expected_strength"),
                     "judged": True, "strength": st, "type": e.get("type"),
                     "match": ok, "edge_id": e.get("edge_id")})
        judged_no.add(pn)

    out = io.open(base + r"\M1-07-小样本验收-预填.csv", "w", encoding="utf-8-sig")
    out.write("pair_no,category,expected_strength,判定边?,判边强度,type,match\n")
    for c in sorted(rows, key=lambda x: x["pair_no"]):
        out.write("{},{},{},{},{},{},{}\n".format(
            c["pair_no"], c["category"], c["expected"], "是", c["strength"], c.get("type", ""), c["match"]))
    for p in pairs:
        pn = int(p.get("pair_no", 0))
        if pn not in judged_no:
            out.write("{},{},{},否,,,-\n".format(pn, p.get("category"), p.get("expected_strength")))
    out.close()

    print("edges:", len(edges), "; 未判边(跳过)对:", len(pairs) - len(judged_no))
    print("per-category:", {k: dict(v) for k, v in cat_stat.items()})
    # 三线通过率
    strong_expect = [(i + 1, p) for i, p in enumerate(pairs) if p.get("category") == "strong"]
    weak_expect = [(i + 1, p) for i, p in enumerate(pairs) if p.get("category") == "weak"]
    rand_expect = [(i + 1, p) for i, p in enumerate(pairs) if p.get("category") == "random"]
    strong_hit = sum(1 for pn, p in strong_expect if pn in judged_no and
                     any(r["pair_no"] == pn and r["strength"] == "strong" for r in rows))
    weak_hit = sum(1 for pn, p in weak_expect if pn in judged_no and
                   any(r["pair_no"] == pn and r["strength"] == "weak" for r in rows))
    rand_judged = sum(1 for pn, p in rand_expect if pn in judged_no)
    print("strong 判 strong: {}/{}；weak 判 weak: {}/{}；random 被判边: {}/{}".format(
        strong_hit, len(strong_expect), weak_hit, len(weak_expect), rand_judged, len(rand_expect)))
    print("通过线: strong>=16/20, weak>=16/20, random<=4/20")


if __name__ == "__main__":
    raise SystemExit(main())
