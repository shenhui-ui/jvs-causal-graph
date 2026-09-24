#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""v2.1 重判结果汇总:解析14批→合并→统计→(ingest 由调用方执行)。"""
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
    all_e = []
    per = {}
    for b in range(14):
        arr = extract_array(f"{BASE}\\v21_edge_{b}.txt")
        per[b] = len(arr)
        all_e.extend(arr)
    # 去重 (from,to),edge_id 顺序重排
    seen, final = set(), []
    for e in all_e:
        key = (e.get("from_event"), e.get("to_event"))
        if key in seen:
            continue
        seen.add(key)
        final.append(e)
    for i, e in enumerate(final, 1):
        e["edge_id"] = f"A{i:04d}"
    with io.open(BASE + r"\all-real-edges-v21.jsonl", "w", encoding="utf-8") as f:
        for e in final:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("每批边数:", per)
    print("合并后(去重):", len(final))
    print("strength:", dict(collections.Counter(e.get("strength") for e in final)))
    print("type    :", dict(collections.Counter(e.get("type") for e in final)))
    print("pending(conf<=0.6):", sum(1 for e in final if (e.get("confidence") or 0) <= 0.6))
    # 超时/空批检查
    for b, n in per.items():
        if n == 0:
            print(f"  ! batch {b} 空(可能超时未重试)")


if __name__ == "__main__":
    raise SystemExit(main())
