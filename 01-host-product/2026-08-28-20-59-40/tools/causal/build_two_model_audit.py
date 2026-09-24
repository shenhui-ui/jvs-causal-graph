#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""168 对编造边抽检(两模型对照版,§2.3)。

口径:14 批 × 12 对 = 168 对,从 candidates 按批抽样(seed 固定);对同一候选对
分别列出 v5(GLM-5.2)与 v6(flash-lite)的判边(strength/type/conf/rationale 前 120)。
未判边 → 标「—(未判)」。用于人工核对编造边(§2.3 主判据=修复重判后编造边 0)。
"""
import argparse
import collections
import io
import json
import random

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


def load_jsonl(path):
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nbatch", type=int, default=14)
    ap.add_argument("--per", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cand = load_jsonl(BASE + r"\candidates-m1.jsonl")
    v5 = {(e["from_event"], e["to_event"]): e for e in load_jsonl(BASE + r"\all-real-edges-v5.jsonl")}
    v6 = {(e["from_event"], e["to_event"]): e for e in load_jsonl(BASE + r"\all-real-edges-v6.jsonl")}
    rnd = random.Random(args.seed)

    rows = []
    for b in range(args.nbatch):
        start, end = b * 60, min((b + 1) * 60, len(cand))
        pool = list(range(start, end))
        sample = rnd.sample(pool, min(args.per, len(pool)))
        for idx in sample:
            c = cand[idx]
            key = (c["from_event"], c["to_event"])
            e5, e6 = v5.get(key), v6.get(key)
            rows.append({
                "batch_no": b + 1, "pair_no_global": idx + 1,
                "from_event": c["from_event"], "to_event": c["to_event"],
                "v5": None if not e5 else {"type": e5.get("type"), "strength": e5.get("strength"),
                                           "conf": e5.get("confidence"), "r": (e5.get("rationale") or "")[:120]},
                "v6": None if not e6 else {"type": e6.get("type"), "strength": e6.get("strength"),
                                           "conf": e6.get("confidence"), "r": (e6.get("rationale") or "")[:120]},
            })

    out = args.out or (BASE + r"\s4_audit_two-model.jsonl")
    with io.open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 统计
    n5 = sum(1 for r in rows if r["v5"])
    n6 = sum(1 for r in rows if r["v6"])
    both = sum(1 for r in rows if r["v5"] and r["v6"])
    strong5 = sum(1 for r in rows if r["v5"] and r["v5"]["strength"] == "strong")
    strong6 = sum(1 for r in rows if r["v6"] and r["v6"]["strength"] == "strong")
    print(f"抽检对: {len(rows)} (批次 {len(set(r['batch_no'] for r in rows))})")
    print(f"v5 判边 {n5} (strong {strong5}) | v6 判边 {n6} (strong {strong6}) | 两模型都判 {both}")
    print("→", out)


if __name__ == "__main__":
    raise SystemExit(main())
