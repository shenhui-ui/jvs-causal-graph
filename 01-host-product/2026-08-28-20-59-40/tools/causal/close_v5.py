#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""决策A闭环:合并 relay 批 → 统计 → 入库 v5 → 为什么改覆盖 → 抽检清单(全部本地,无 LLM)。

用法:
  python close_v5.py --prefix relay_edge --edges-out all-real-edges-v5.jsonl --db causal_graph_v5.db
"""
import argparse
import collections
import io
import json
import os
import re
import sqlite3
import sys

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
TOOLS = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\tools\causal"


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
    ap.add_argument("--prefix", default="relay_edge")
    ap.add_argument("--nbatch", type=int, default=14)
    ap.add_argument("--edges-out", default="")
    ap.add_argument("--db", default="")
    ap.add_argument("--skip-ingest", action="store_true")
    args = ap.parse_args()

    # 1) 合并
    all_e, per, empty = [], {}, []
    for b in range(args.nbatch):
        arr = extract_array(f"{BASE}\\{args.prefix}_{b}.txt")
        per[b] = len(arr)
        all_e.extend(arr)
        if not arr:
            empty.append(b)
    seen, final = set(), []
    for e in all_e:
        key = (e.get("from_event"), e.get("to_event"))
        if key in seen:
            continue
        seen.add(key)
        final.append(e)
    for i, e in enumerate(final, 1):
        e["edge_id"] = f"A{i:04d}"
    print("每批边数:", per)
    print("空批:", empty if empty else "无")
    print("合并后(去重):", len(final))
    print("strength:", dict(collections.Counter(e.get("strength") for e in final)))
    print("type    :", dict(collections.Counter(e.get("type") for e in final)))
    print("pending(conf<=0.6):", sum(1 for e in final if (e.get("confidence") or 0) <= 0.6))

    edges_out = args.edges_out or (BASE + r"\all-real-edges-v5.jsonl")
    with io.open(edges_out, "w", encoding="utf-8") as f:
        for e in final:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("→", edges_out)

    if args.skip_ingest:
        return 0

    # 2) 入库 v5
    db = args.db or (BASE + r"\causal_graph_v5.db")
    if os.path.exists(db):
        os.remove(db)
    sys.path.insert(0, TOOLS)
    import causal_graph as cg
    cg.init_db(db)
    conn = cg.connect(db)
    evs = []
    with io.open(BASE + r"\all-real-events.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evs.append(json.loads(line))
    st1 = cg.ingest_events(conn, evs)
    st2 = cg.ingest_edges(conn, final)
    conn.commit()
    print("入库 v5:", db, "| events", st1, "| edges", st2)
    conn.close()

    # 3) 为什么改覆盖
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    edges = [dict(r) for r in conn.execute(
        "SELECT id,from_event,to_event,type,strength FROM causal_edges")]
    g_any, g_strong = collections.defaultdict(set), collections.defaultdict(set)
    for e in edges:
        g_any[e["from_event"]].add(e["to_event"])
        g_any[e["to_event"]].add(e["from_event"])
        if e.get("strength") == "strong":
            g_strong[e["from_event"]].add(e["to_event"])
            g_strong[e["to_event"]].add(e["from_event"])
    def reach(g, s, t):
        if s == t:
            return True
        seen, st = {s}, [s]
        while st:
            x = st.pop()
            if x == t:
                return True
            for y in g.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    st.append(y)
        return False
    qs = []
    with io.open(BASE + r"\query-set-real-v1.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qs.append(json.loads(line))
    pat = re.compile(r"(R\d{4,6})")
    print("\n== 12 条为什么改 cause→effect 覆盖 ==")
    for q in qs:
        if q.get("type") != "为什么改":
            continue
        g = q.get("gold_answer") or {}
        refs = pat.findall((g.get("cause") or "") + (g.get("effect") or ""))
        if len(refs) >= 2:
            c, t = refs[0], refs[-1]
            print(f"  {q['qid']}: {c}→{t} | any={reach(g_any,c,t)} | strong={reach(g_strong,c,t)}")
        else:
            print(f"  {q['qid']}: refs不足({refs})")
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
