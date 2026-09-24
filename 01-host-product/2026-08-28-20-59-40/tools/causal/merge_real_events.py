#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""合并全部真实事件(防撞号重命名)并输出 all-real-events.jsonl。"""
import io
import json
import re


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


def load_jsonl(path):
    evs = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evs.append(json.loads(line))
    return evs


def main():
    base = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
    merged = []
    counters = {}

    def push(evs, corpus, prefix, src_hash=None):
        counters[prefix] = counters.get(prefix, 0)
        for e in evs:
            if src_hash is not None and not str(e.get("source_hash", "")).startswith(src_hash):
                continue
            counters[prefix] += 1
            e["orig_id"] = e.get("event_id")
            e["event_id"] = f"{prefix}{counters[prefix]:04d}"
            e["corpus"] = corpus
            merged.append(e)

    import hashlib
    with io.open(base + r"\incoming_real\语料文件A-20260829.md", encoding="utf-8") as f:
        h1 = hashlib.sha256(f.read().strip().encode("utf-8")).hexdigest()[:12]
    print("doc1 hash:", h1)

    push(load_jsonl(base + r"\incoming_real\语料组A\events-语料组A.jsonl"), "语料组A", "R6")
    push(load_jsonl(base + r"\incoming_real\语料组B\events-语料组B.jsonl"), "语料组B", "R7")
    push(extract_array(base + r"\s3_real1_v12_raw_reply.txt"), "群聊1", "R8", src_hash=h1)
    push(extract_array(base + r"\s3_real2_v12.txt"), "群聊2", "R9")
    push(extract_array(base + r"\s3_real3_v12.txt"), "群聊3", "R9")
    push(extract_array(base + r"\s3_real4_v12.txt"), "群聊4", "R9")

    print("merged total:", len(merged))
    groups = {}
    for e in merged:
        groups[e["corpus"]] = groups.get(e["corpus"], 0) + 1
    print("by corpus:", groups)
    with io.open(base + r"\all-real-events.jsonl", "w", encoding="utf-8") as f:
        for e in merged:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("saved all-real-events.jsonl")


if __name__ == "__main__":
    raise SystemExit(main())
