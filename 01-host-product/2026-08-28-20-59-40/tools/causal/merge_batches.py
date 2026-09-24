#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""判边批结果合并:解析批文件→合并→去重→统计(可指定前缀/输出)。"""
import argparse
import collections
import io
import json
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


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
                try:
                    return json.loads(raw[start:idx + 1])
                except Exception:
                    return []
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="v21_edge", help="批文件前缀,如 v21b_edge")
    ap.add_argument("--out", default="", help="输出 jsonl 路径")
    ap.add_argument("--nbatch", type=int, default=14)
    args = ap.parse_args()

    all_e, per = [], {}
    for b in range(args.nbatch):
        arr = extract_array(f"{BASE}\\{args.prefix}_{b}.txt")
        per[b] = len(arr)
        all_e.extend(arr)
    seen, final = set(), []
    for e in all_e:
        key = (e.get("from_event"), e.get("to_event"))
        if key in seen:
            continue
        seen.add(key)
        final.append(e)
    for i, e in enumerate(final, 1):
        e["edge_id"] = f"A{i:04d}"
    out = args.out or f"{BASE}\\all-real-edges-{args.prefix.split('_')[0]}.jsonl"
    with io.open(out, "w", encoding="utf-8") as f:
        for e in final:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("每批边数:", per)
    print("合并后(去重):", len(final), "→", out)
    print("strength:", dict(collections.Counter(e.get("strength") for e in final)))
    print("type    :", dict(collections.Counter(e.get("type") for e in final)))
    print("pending(conf<=0.6):", sum(1 for e in final if (e.get("confidence") or 0) <= 0.6))
    for b, n in per.items():
        if n == 0:
            print(f"  ! batch {b} 空(可能超时未重试)")


if __name__ == "__main__":
    raise SystemExit(main())
