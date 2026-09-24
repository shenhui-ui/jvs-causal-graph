# -*- coding: utf-8 -*-
r"""把定版图库 ingest 到新库 causal_graph_final.db(2026-09-02)。

- 事件源: docs/host_memory_dump/all-real-events.jsonl(393 事件)
- 定版边: docs/host_memory_dump/all-real-edges-finalized.jsonl(284 边,v5 基准)
- 目标库: docs/host_memory_dump/causal_graph_final.db(新文件,不覆盖 v3/v4 等旧库)

流程: 读两 jsonl → 校验 284 边端点全在 393 事件内 → init_db → ingest_events →
      ingest_edges → 汇总校验(计数/strength/type 分布) → 摘要写盘
      (ingest-final-summary-20260902.json)。
本脚本不依赖终端打印;结果写文件后用 read 读取。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import causal_graph as cg

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
EVENTS = os.path.join(BASE, "all-real-events.jsonl")
EDGES = os.path.join(BASE, "all-real-edges-finalized.jsonl")
DB = os.path.join(BASE, "causal_graph_final.db")
SUMMARY = os.path.join(BASE, "ingest-final-summary-20260902.json")


def load_jsonl(path):
    items = []
    with io.open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main():
    events = load_jsonl(EVENTS)
    edges = load_jsonl(EDGES)
    ev_ids = set()
    for e in events:
        eid = e.get("id") or e.get("event_id")
        if eid:
            ev_ids.add(str(eid))

    missing_from = sorted({str(e.get("from_event")) for e in edges} - ev_ids)
    missing_to = sorted({str(e.get("to_event")) for e in edges} - ev_ids)

    if missing_from or missing_to:
        summary = {
            "ok": False,
            "error": "edge endpoints missing in events",
            "missing_from": missing_from,
            "missing_to": missing_to,
        }
        with io.open(SUMMARY, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("ENDPOINT CHECK FAILED -> " + SUMMARY)
        return 1

    # 建库 + 导入(events 先于 edges,整体批量事务)
    cg.init_db(DB)
    conn = cg.connect(DB)
    try:
        curs = conn.cursor()
        ev_stats = cg.ingest_events(curs, events)
        ed_stats = cg.ingest_edges(curs, edges)
        conn.commit()
    finally:
        conn.close()

    # 汇总校验
    conn = cg.connect(DB)
    try:
        n_ev = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        n_ed = conn.execute("SELECT COUNT(*) c FROM causal_edges").fetchone()["c"]
        strength_rows = conn.execute(
            "SELECT strength, COUNT(*) c FROM causal_edges GROUP BY strength").fetchall()
        type_rows = conn.execute(
            "SELECT type, COUNT(*) c FROM causal_edges GROUP BY type").fetchall()
        n_strong = conn.execute(
            "SELECT COUNT(*) c FROM causal_edges WHERE strength='strong'").fetchone()["c"]
    finally:
        conn.close()

    summary = {
        "ok": True,
        "db": DB,
        "generated": "2026-09-02",
        "events_source": EVENTS,
        "edges_source": EDGES,
        "ingest_events": ev_stats,
        "ingest_edges": ed_stats,
        "db_counts": {
            "events": n_ev,
            "edges": n_ed,
        },
        "edges_strength": {r["strength"] or "NULL": r["c"] for r in strength_rows},
        "edges_type": {r["type"] or "NULL": r["c"] for r in type_rows},
        "edges_strong": n_strong,
        "endpoint_missing": {"from": missing_from, "to": missing_to},
    }
    with io.open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("INGEST DONE -> " + SUMMARY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
