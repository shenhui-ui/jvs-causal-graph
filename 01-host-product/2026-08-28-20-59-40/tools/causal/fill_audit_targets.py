#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""fill_audit_targets.py — 支线A：对失分审计确认的 12 个候选对做豆包判边,
通过的并入图库 v3(all-real-edges-finalized-v3.jsonl → causal_graph_final_v3.db)。

候选对来自 M1-10-失分审计表-20260903.md(A 类图真缺口 7 题)。
结果写盘: fill-audit-result-20260903.json + ingest-v3-summary-20260903.json
"""
import io
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import causal_graph as cg
from llm_answer import chat, provider_conf

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
DB_V2 = os.path.join(BASE, "causal_graph_final_v2.db")
EDGES_V2 = os.path.join(BASE, "all-real-edges-finalized-v2.jsonl")
EDGES_V3 = os.path.join(BASE, "all-real-edges-finalized-v3.jsonl")
DB_V3 = os.path.join(BASE, "causal_graph_final_v3.db")
RESULT = os.path.join(BASE, "fill-audit-result-20260903.json")
INGEST_SUMMARY = os.path.join(BASE, "ingest-v3-summary-20260903.json")
EVENTS = os.path.join(BASE, "all-real-events.jsonl")
GAP = int(os.environ.get("LLM_GAP", "6"))

# (from, to, 服务题目) —— 全部来自失分审计表 A 类清单
PAIRS = [
    ("R90018", "R90017", "Q019/Q029:真实性欠缺误标(应为行为违规)→启用新标签体系"),
    ("R90019", "R90017", "Q019/Q029:真实性欠缺判断依据说明→启用新标签体系"),
    ("R90010", "R90011", "Q020:Z24报告此类视频多应给错误→开始执行判严重违规"),
    ("R90012", "R90011", "Q020:专项对接建议(手机播放视频可同步质检)→开始执行"),
    ("R70071", "R70072", "Q014:6月待质检数据量统计→修改无效场景举例(同日)"),
    ("R60233", "R60242", "Q023:新版本上线+重刷计划→确定交付优先原则"),
    ("R60029", "R60242", "Q023:新版本不稳定影响交付→确定交付优先原则"),
    ("R60232", "R60242", "Q023:优化checker通过率卡交付→确定交付优先原则"),
    ("R60231", "R60244", "Q018:漏报主因yolo2d检测失败需整合→finger6d内容迁移"),
    ("R60106", "R60137", "Q003:确定回刷方案(4.13后数据)→决定新checker先别上"),
    ("R60107", "R60137", "Q003:回刷剩28511条正在刷→决定新checker先别上"),
    ("R60196", "R60208", "Q002:宽松CPD自测no-pass 19.5%→8.9%→调阈值1%到2%/5%"),
]


def ev_summary(ev):
    if not ev:
        return "(缺失)"
    parts = [f"{ev['id']} ({ev.get('ts') or 'unknown'}) {ev.get('type')}|{ev.get('subject')}|"
             f"{ev.get('action')}|{ev.get('object')}"]
    if ev.get("before") is not None or ev.get("after"):
        parts.append(f" 变更:「{ev.get('before') or ''}」→「{ev.get('after') or ''}」")
    return "".join(parts)


def judge_prompt(a, b, note):
    return (
        "你是因果图构建审核员。判断事件A是否是事件B发生的(真实)前置原因或触发因素。\n"
        "判定标准：\n"
        "- 只有依据两事件文本能合理推断「A 促成/导致了 B」才判 true；时间先后是必要条件。\n"
        "- type 从 {cause, trigger, evolve, supersede, resolve} 中选：cause=实质性因果；"
        "trigger=决策/事件触发后续动作；evolve=同一事项演进；supersede=替代；resolve=解决。\n"
        "- strength：strong=文本直接支持；weak=推断合理但证据间接。\n"
        "- 保守判定：宁可 false 不可勉强。背景任务备注(仅参考,不代表已成立)：" + note + "\n"
        f"事件A：{a}\n事件B：{b}\n"
        '只输出 JSON：{"is_edge": true/false, "type": "...", "strength": "strong/weak", '
        '"confidence": 0.0-1.0, "rationale": "一句话理由"}'
    )


def main():
    conn = sqlite3.connect(DB_V2)
    conn.row_factory = sqlite3.Row
    evs = {}
    for r in conn.execute("SELECT * FROM events"):
        evs[r["id"]] = dict(r)
    conn.close()

    _ = provider_conf()
    results = []
    accepted = []
    for i, (fa, tb, note) in enumerate(PAIRS, 1):
        a, b = evs.get(fa), evs.get(tb)
        prompt = judge_prompt(ev_summary(a), ev_summary(b), note)
        try:
            import re
            raw = chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.0)
            m = re.search(r"\{[^{}]*\}", raw, __import__("re").S)
            j = json.loads(m.group(0))
        except Exception as e:  # noqa: BLE001
            j = {"is_edge": False, "type": None, "strength": None,
                 "confidence": 0.0, "rationale": f"判边调用失败: {e}"}
        rec = {"pair_no": i, "from": fa, "to": tb, "note": note, **j}
        results.append(rec)
        print(f"[{i}/{len(PAIRS)}] {fa}->{tb} is_edge={j.get('is_edge')} "
              f"{j.get('type')}/{j.get('strength')} conf={j.get('confidence')}", flush=True)
        if j.get("is_edge"):
            accepted.append(rec)
        import time
        time.sleep(GAP)

    # 并入 v3
    with io.open(EDGES_V2, encoding="utf-8") as f:
        edges = [json.loads(l) for l in f if l.strip()]
    base_ids = {e.get("edge_id") for e in edges}
    n0 = len(edges)
    for k, rec in enumerate(accepted, 1):
        fa, tb = rec["from"], rec["to"]
        edges.append({
            "edge_id": f"F{k:04d}", "pair_no": 9000 + rec["pair_no"],
            "from_event": fa, "to_event": tb,
            "type": rec.get("type") or "cause",
            "strength": rec.get("strength") or "weak",
            "rationale": rec.get("rationale", ""),
            "evidence": f"from:「{evs[fa]['subject']} {evs[fa]['action']} {evs[fa]['object']}」;"
                        f"to:「{evs[tb]['subject']} {evs[tb]['action']} {evs[tb]['object']}」",
            "confidence": rec.get("confidence", 0.5),
            "source_hash": evs[fa].get("source_hash") or "",
            "merge_note": f"audit-fill-20260903(服务{rec['note'][:20]})",
        })
    with io.open(EDGES_V3, "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ingest v3
    with io.open(EVENTS, encoding="utf-8-sig") as f:
        events = [json.loads(l) for l in f if l.strip()]
    ev_ids = {str(e.get("id") or e.get("event_id")) for e in events}
    missing = sorted({str(e["from_event"]) for e in edges} - ev_ids) + \
             sorted({str(e["to_event"]) for e in edges} - ev_ids)
    summary = {"ok": not missing, "v2_edges": n0, "added": len(accepted),
               "total": len(edges), "missing_endpoints": missing,
               "accepted": [{k: r[k] for k in ("from", "to", "type", "strength", "confidence", "note")}
                            for r in accepted]}
    if os.path.exists(DB_V3):
        os.remove(DB_V3)
    if not missing:
        cg.init_db(DB_V3)
        c2 = cg.connect(DB_V3)
        try:
            cur = c2.cursor()
            cg.ingest_events(cur, events)
            cg.ingest_edges(cur, edges)
            c2.commit()
        finally:
            c2.close()
        c3 = cg.connect(DB_V3)
        try:
            summary["db_counts"] = {
                "events": c3.execute("SELECT COUNT(*) c FROM events").fetchone()["c"],
                "edges": c3.execute("SELECT COUNT(*) c FROM causal_edges").fetchone()["c"]}
        finally:
            c3.close()

    with io.open(RESULT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with io.open(INGEST_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"[fill] accepted {len(accepted)}/{len(PAIRS)} → {EDGES_V3}", flush=True)
    print(f"[fill] summary → {INGEST_SUMMARY}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
