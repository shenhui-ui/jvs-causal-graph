#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S6 收尾:因果问答 CLI(答案模板 + 挂载点探测辅助)。

用法:
  python causal_qa.py <db> find "关键词"           # 在事件表中模糊查找候选事件
  python causal_qa.py <db> answer --event E019     # 给出「当时X→后来Y→原因Z(证据)」模板答案
  python causal_qa.py <db> answer "支付超时重试"    # 自动选:命中关键词的最新 change/decision 事件

模板规则:
- 优先取 supersede 边:from_event 的 after = 「当时是 X」,to_event 的 after = 「后来改为 Y」;
- 原因:优先取 to_event.after 中「原因:…」片段,无则用 rationale;
- 证据:引用 evidence 与 source_hash;
- 反事实/无匹配:输出婉拒与提示,不编造。
"""

import argparse
import json
import re
import sys

import causal_graph as cg

REASON_RE = re.compile(r"原因[:：]([^；;.]{2,60})")


def _fetch(db):
    conn = cg.connect(db)
    rows = conn.execute(
        "SELECT id, ts, type, subject, action, object, before, after, confidence, source_hash, evidence_ref"
        " FROM events").fetchall()
    edges = conn.execute(
        "SELECT id, from_event, to_event, type, rationale, confidence, source_hash"
        " FROM causal_edges").fetchall()
    conn.close()
    return [dict(r) for r in rows], [dict(r) for r in edges]


def find(db, kw):
    evs, _ = _fetch(db)
    hits = [e for e in evs if kw in (e.get("subject") or "") or kw in (e.get("object") or "")]
    for e in hits:
        print(f"  {e['id']} {e['ts']} {e['type']} {e['subject']}|{e['object']}")
    if not hits:
        print("  (无匹配;试试 find '支付' / '验收' 等)"
        )
    return hits


def pick(evs, kw):
    hits = [e for e in evs if kw in (e.get("subject") or "") or kw in (e.get("object") or "")]
    if not hits:
        return None
    # 优先 change/decision,取 ts 最新
    pref = sorted(hits, key=lambda e: (e.get("type") in ("change", "decision"), e.get("ts") or ""), reverse=True)
    return pref[0]


def answer(db, target):
    evs, edges = _fetch(db)
    ev_by_id = {e["id"]: e for e in evs}
    if isinstance(target, str) and not target.startswith("E"):
        ev = pick(evs, target)
    else:
        ev = ev_by_id.get(target)
    if not ev:
        print("未找到相关事件;可用 find '关键词' 检索。")
        return

    # 入边:被谁取代/成因/演进
    related = [e for e in edges if e.get("to_event") == ev["id"]]
    supersede = [e for e in related if e.get("type") == "supersede"]
    causes = [e for e in related if e.get("type") in ("cause", "trigger", "evolve")]

    lines = []
    lines.append(f"【{ev.get('subject')}】")
    if supersede:
        fr = ev_by_id.get(supersede[0]["from_event"])
        if fr and fr.get("after") and ev.get("after"):
            lines.append(f"- 当时是:{fr['after']}({fr.get('ts')})")
            lines.append(f"- 后来改为:{ev['after']}({ev.get('ts')})")
        lines.append(f"- 判定:{supersede[0].get('type')}(conf {supersede[0].get('confidence')})")
    else:
        lines.append(f"- 事件:({ev.get('ts')}) {ev.get('action')} {ev.get('object')} → {ev.get('after') or '—'}")

    reason = None
    clue = None
    m = REASON_RE.search(ev.get("after") or "")
    if m:
        reason = m.group(1)
    elif supersede and supersede[0].get("rationale"):
        reason = supersede[0].get("rationale")
    elif causes:
        cause_edge = next((c for c in causes if c.get("type") in ("cause", "trigger")), None)
        reason = cause_edge.get("rationale") if cause_edge else None
        if not reason:
            clue = causes[0].get("rationale")
    if reason:
        lines.append(f"- 原因:{reason}")
    if clue:
        lines.append(f"- 线索(演进/连带):{clue}")
    if causes:
        for c in causes[:2]:
            fr = ev_by_id.get(c.get("from_event"))
            lines.append(f"- 沿着:{c.get('type')}({fr.get('ts')} {fr.get('subject')} {fr.get('action')} {fr.get('object')})")

    evidence_edge = next((edge for edge in related if edge.get("evidence") or edge.get("source_hash")), None)
    if evidence_edge:
        evidence = evidence_edge.get("evidence") or f"source_hash={str(evidence_edge.get('source_hash') or ev.get('source_hash'))[:12]}"
    elif ev.get("evidence_ref") or ev.get("source_hash"):
        evidence = ev.get("evidence_ref") or f"source_hash={str(ev.get('source_hash'))[:12]}"
    else:
        evidence = "证据不足：该事件没有关联边或来源引用"
    lines.append(f"- 证据:{str(evidence)[:120]}")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("find")
    p1.add_argument("kw")
    p2 = sub.add_parser("answer")
    p2.add_argument("target")  # 事件 id 或关键词
    args = ap.parse_args()
    if args.cmd == "find":
        find(args.db, args.kw)
    else:
        answer(args.db, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
