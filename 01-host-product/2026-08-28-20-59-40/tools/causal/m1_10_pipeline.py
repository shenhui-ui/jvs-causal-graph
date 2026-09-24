#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1-10 因果准确率基线 · 最小 QA 答案管线骨架(基于 v7 图库)。

流程:30 题(query-set-real-v1.jsonl)→ 关键词检索事件 → why() 强边多跳 → 答案模板
      → 输出 eval_m1 格式 answers.jsonl + gold.jsonl → 判分出基线。

判分口径(决策 A):三要素判分不依赖图多跳;本骨架让「答案」由图的证据链生成,
图作为证据展示层;eval_m1 按 cause/effect/evidence_hint 语义判。

用法:
  python m1_10_pipeline.py --db causal_graph_v7.db \
      --questions query-set-real-v1.jsonl --out m1_10_baseline/ [--judge]
"""
import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import causal_graph as cg


def load_events(db):
    conn = cg.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    optional = ", corpus" if "corpus" in cols else ""
    rows = conn.execute(
        "SELECT id, ts, type, subject, action, object, before, after, confidence, source_hash"
        + optional + " FROM events").fetchall()
    conn.close()
    events = [dict(r) for r in rows]
    for event in events:
        event["event_id"] = event["id"]
    return events


def _search_text(e):
    return " ".join(str(e.get(k) or "") for k in ("subject", "object", "after", "before"))


def extract_keywords(question):
    """从题面提取检索词:优先「…」与【…】内短语,再取 2+ 字中文/英文 token。"""
    kws = []
    for pat in ("「([^」]{2,30})」", "【([^】]{2,30})】", "『([^』]{2,30})』"):
        kws.extend(re.findall(pat, question))
    # 去掉含纯"由/从/改成"等结构词过短项
    kws = [k for k in kws if len(k) >= 2]
    # 兜底:题面里非引号的长词(按分隔符切)
    body = re.sub(r"[「」【】『』（）()，。？！、:：；;]", " ", question)
    for w in body.split():
        if len(w) >= 2 and w not in kws:
            kws.append(w)
    # 过滤通用词
    stop = {"为什么", "在", "把", "的", "处理", "方式", "从", "改成", "了", "改", "成"}
    return [k for k in kws if k not in stop][:8]


def retrieve(events, kws):
    """按检索词命中事件,优先 change/decision/bugfix,取命中词最多者。"""
    scored = []
    for e in events:
        txt = _search_text(e)
        hits = sum(1 for k in kws if k and k in txt)
        if hits:
            scored.append((hits, e))
    if not scored:
        return None
    # 命中词数优先,其次 change/decision/bugfix 类型,再按时间
    def rank(x):
        n, e = x
        pref = 1 if e.get("type") in ("change", "decision", "bugfix") else 0
        return (n, pref, e.get("ts") or "")
    return sorted(scored, key=rank, reverse=True)[0][1]


def build_answer(db, q, events):
    """生成一条因果答案(模板:当时 X → 后来 Y → 原因 → 证据链)。"""
    kws = extract_keywords(q.get("question", ""))
    anchor = retrieve(events, kws)
    if anchor is None:
        return ("(未找到相关事件,无法回答)", [], kws)
    wid = anchor["id"]
    why = cg.why(db, wid, max_hops=3)
    evs = {e["id"]: e for e in events}
    chain = why.get("edges", []) if why.get("ok") else []
    parts = [f"关于「{anchor.get('subject') or anchor.get('object')}」"]
    # 事件本体
    parts.append(f"- 事件:({anchor.get('ts')}) {anchor.get('action')} {anchor.get('object')}"
                 + (f" → {anchor.get('after')}" if anchor.get("after") else ""))
    # 因果链(强边)
    for ed in chain:
        frm = evs.get(ed.get("from_event"), {})
        to = evs.get(ed.get("to_event"), {})
        parts.append(
            f"- {ed.get('type')}({ed.get('strength') or '?'}): "
            f"{frm.get('action')}{frm.get('object')} → {to.get('action')}{to.get('object')} "
            f"(理由:{ed.get('rationale')})")
    # 原因:从强边 rationale 提炼
    cause = next((ed.get("rationale") for ed in chain if ed.get("type") in ("cause", "trigger")), None)
    if cause:
        parts.append(f"- 原因:{cause}")
    # 证据引用
    src = anchor.get("source_hash") or (chain[0].get("source_hash") if chain else "")
    if src:
        parts.append(f"- 证据:[src: workspace/events #{str(src)[:12]}]")
    ev_chain = []
    for ed in chain:
        for eid in (ed.get("from_event"), ed.get("to_event")):
            e = evs.get(eid, {})
            if e.get("source_hash"):
                ev_chain.append(f"[src: workspace/events #{str(e['source_hash'])[:12]}]")
    return ("\n".join(parts), ev_chain, kws)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--judge", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    events = load_events(args.db)
    qs = []
    with io.open(args.questions, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qs.append(json.loads(line))

    answers, golds = [], []
    for q in qs:
        ans_text, ev_chain, kws = build_answer(args.db, q, events)
        g = q.get("gold_answer") or {}
        answers.append({
            "qid": q["qid"], "question": q.get("question"),
            "answer_text": ans_text, "evidence_chain": ev_chain,
            "latency_ms": -1, "pipeline_ok": bool(ans_text and not ans_text.startswith("(未找到")),
            "trace": f"kws={kws}",
        })
        golds.append({
            "qid": q["qid"],
            "cause": g.get("cause", ""),
            "evidence_hint": g.get("evidence_hint", ""),
            "effect": g.get("effect", ""),
        })

    ans_path = os.path.join(args.out, "answers.jsonl")
    gold_path = os.path.join(args.out, "gold.jsonl")
    with io.open(ans_path, "w", encoding="utf-8") as f:
        for a in answers:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    with io.open(gold_path, "w", encoding="utf-8") as f:
        for g in golds:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    n_ok = sum(1 for a in answers if a["pipeline_ok"])
    print(f"[m1_10] 管线完成:{len(answers)} 题,pipeline_ok={n_ok}")
    print(f"  → {ans_path}")
    print(f"  → {gold_path}")
    if args.judge:
        import subprocess
        py = sys.executable
        score = os.path.join(args.out, "scores.json")
        cmd = [py, os.path.join(os.path.dirname(__file__), "..", "eval", "eval_m1.py"),
               "score", ans_path, "--gold", gold_path, "--seed", "42", "--out", score]
        subprocess.run(cmd, check=True)
        print(f"  → 判分 {score}")


if __name__ == "__main__":
    raise SystemExit(main())
