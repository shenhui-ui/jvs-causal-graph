#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""为断链的「为什么改」题补候选判边:4 对缺失 cause→effect 对。

输出:payload 文本(供 relay_call)与补边 jsonl(判边后合并)。
"""
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
PAIRS = [
    ("R60059", "R60063"),   # Q001: 定位根因 → 明确责任层(语料组A, 5/4→5/5)
    ("R60193", "R60208"),   # Q002: Kafka 运维成本高 → 改回 RocketMQ
    ("R70079", "R70018"),   # Q007: 语料组B → 质检(跨语料)
    ("R70069", "R60242"),   # Q008: 跨语料
]


def summarize(evs, eid):
    e = evs.get(eid, {})
    core = (f"{e.get('event_id')}|{e.get('ts')}|{e.get('type')}|{e.get('subject')} "
            f"{e.get('action')} {e.get('object')}")
    b, a = e.get("before"), e.get("after")
    if b is not None and a:
        return f"{core} | 变更:由「{b}」→「{a}」"
    if a:
        return f"{core} | 变更:{a}"
    return core


def main():
    evs = {}
    with io.open(BASE + r"\all-real-events.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                evs[e["event_id"]] = e
    objs = []
    for i, (a, b) in enumerate(PAIRS, 1):
        objs.append({"pair_no": i, "from_event": a, "to_event": b})
        print(f"pair {i}: {a} → {b}")
        print("   from:", summarize(evs, a)[:100])
        print("   to  :", summarize(evs, b)[:100])
    with io.open(BASE + r"\fill_pairs.jsonl", "w", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print("[ok] fill_pairs.jsonl(ID 对,交 compose_pairs_payload 组装)")


if __name__ == "__main__":
    raise SystemExit(main())
