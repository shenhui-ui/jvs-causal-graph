# -*- coding: utf-8 -*-
r"""校验 causal_graph_final.db(2026-09-02 定版库 ingest 后)。

检查项:
1. events / causal_edges 计数与 strength/type 分布;
2. why() 多跳只走 strong 边(weak 不参与):抽若干锚点事件,统计链中边 strength;
3. spot-check 3 个锚点(R60063 / R60175 / R60244)的链条长度与类型。
结果写文件 verify-final-db-20260902.json。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import causal_graph as cg

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
DB = os.path.join(BASE, "causal_graph_final.db")
OUT = os.path.join(BASE, "verify-final-db-20260902.json")

ANCHORS = ["R60063", "R60175", "R60244", "R60208", "R70080"]


def main():
    conn = cg.connect(DB)
    try:
        n_ev = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        n_ed = conn.execute("SELECT COUNT(*) c FROM causal_edges").fetchone()["c"]
        strength_rows = conn.execute(
            "SELECT strength, COUNT(*) c FROM causal_edges GROUP BY strength").fetchall()
        type_rows = conn.execute(
            "SELECT type, COUNT(*) c FROM causal_edges GROUP BY type").fetchall()
        weak_rows = conn.execute(
            "SELECT COUNT(*) c FROM causal_edges WHERE strength='weak'").fetchone()["c"]
    finally:
        conn.close()

    checks = []
    all_chain_strengths = set()
    for a in ANCHORS:
        res = cg.why(DB, a, max_hops=3)
        ok = res.get("ok", False)
        edges = res.get("edges", []) if ok else []
        sts = sorted({(e.get("strength") or "NULL") for e in edges})
        all_chain_strengths.update(sts)
        checks.append({
            "anchor": a,
            "ok": ok,
            "n_events": len(res.get("events", [])) if ok else 0,
            "n_edges": len(edges),
            "chain_strengths": sts,
            "types": sorted({(e.get("type") or "NULL") for e in edges}),
            "error": res.get("error") if not ok else None,
        })

    summary = {
        "db": DB,
        "db_counts": {"events": n_ev, "edges": n_ed, "weak_edges": weak_rows},
        "edges_strength": {r["strength"] or "NULL": r["c"] for r in strength_rows},
        "edges_type": {r["type"] or "NULL": r["c"] for r in type_rows},
        "why_anchors": checks,
        "chain_strengths_observed": sorted(all_chain_strengths),
        "multi_hop_uses_strong_only": all_chain_strengths <= {"strong"},
    }
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("VERIFY DONE -> " + OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
