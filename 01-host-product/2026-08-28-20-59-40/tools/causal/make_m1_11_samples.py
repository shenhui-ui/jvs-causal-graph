# -*- coding: utf-8 -*-
"""M1-11 三类降级样例生成器（**改判后的口径**，2026-09-28）。

验收口径（**改判**，见 `docs/host_memory_dump/M1-11-待裁定项决策记录-20260928.md` §4）
------------------------------------------------------------------------------
| 类 | 新验收口径 | 为什么这样定 |
|---|---|---|
| **无链** | **≥3** | 生产图谱有孤立事件，可直接构造 |
| **弱关联** | **≥3** 或 空集证明 | Q-2 裁定「改实现让 weak 可扩展」⇒ 该类**变为可达**（`JVS_WEAK_EXPAND=1`） |
| **低置信** | **空集证明** + **fixture 样例 ≥3** | Q-3 裁定「在生产图谱上构造」，而该图上 `conf ≤ 0.6` 的边 = **0 条** ⇒ 客观为空集 |

⚠️ **口径纪律**：
- 台账用「**改判**」，**不用「豁免」**；整体豁免 = 否；
- 「低置信」的 fixture 样例**必须**标 `source: fixture` 且 `counts_as_real_graph_hit: false`，
  **不得**计入「真实图谱命中」类统计。

⚠️ **`弱关联` 依赖环境变量**：其记录里带 `requires_env`，复跑时必须先设该变量，
否则结果与记录不符（生成器自身会在扫描前设置，并把它写进记录）。

用法
----
    python make_m1_11_samples.py --db <db> --out <jsonl> [--per-class 3] [--max-hops 3]

退出码：0 = 三类口径全部满足；2 = 某一类不满足（素材问题，须复核图谱/开关）。
"""

import argparse
import io
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import causal_graph as cg
import why_fallback as wf

WEAK_ENV = cg.WEAK_EXPAND_ENV
CLASSES = ("no_chain", "low_confidence", "weak_only")


# ---------------------------------------------------------------- 图谱查询

def _isolated_events(db, limit):
    """无任何入边/出边的事件（`why()` 必返回 0 条边）。"""
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute(
            "SELECT e.id FROM events e WHERE NOT EXISTS("
            "SELECT 1 FROM causal_edges x WHERE x.from_event=e.id OR x.to_event=e.id) "
            "ORDER BY e.id LIMIT ?", (limit,))]
    finally:
        con.close()


def _event_with_edges(db):
    """负向夹具：**有**边的事件 —— 必须**不**被判为「无链」。"""
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT DISTINCT from_event FROM causal_edges ORDER BY from_event LIMIT 1").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _low_conf_census(db):
    """「低置信」空集证明的原始计数（⚠️ `confidence` 是 real，但必须 cast 后再比较）。"""
    con = sqlite3.connect(db)
    try:
        q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
        return {
            "n_edges": q("SELECT COUNT(*) FROM causal_edges"),
            "n_conf_le_0_6": q("SELECT COUNT(*) FROM causal_edges "
                               "WHERE CAST(confidence AS REAL) <= 0.6"),
            "n_conf_lt_0_5": q("SELECT COUNT(*) FROM causal_edges "
                               "WHERE CAST(confidence AS REAL) < 0.5"),
            "min_confidence": q("SELECT MIN(CAST(confidence AS REAL)) FROM causal_edges"),
            "n_events": q("SELECT COUNT(*) FROM events"),
        }
    finally:
        con.close()


def _scan_class(db, target, cfg, max_hops, limit):
    """在真实图谱上扫出判定为 `target` 的锚点（按 id 排序，确定性）。"""
    con = sqlite3.connect(db)
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM events ORDER BY id")]
    finally:
        con.close()
    hits = []
    for eid in ids:
        out = wf.classify(cg.why(db, eid, max_hops=max_hops), cfg)
        if out["degradation_class"] == target:
            hits.append((eid, out))
            if len(hits) >= limit:
                break
    return hits


# ---------------------------------------------------------------- 记录构造

def _real_record(db, anchor, expected, kind, max_hops, cfg, requires_env=None):
    out = wf.classify(cg.why(db, anchor, max_hops=max_hops), cfg)
    actual = out["degradation_class"]
    match = (actual == expected) if expected != "not_no_chain" else (actual != "no_chain")
    rec = {
        "sample_id": "M1-11/%s/%s" % (expected, anchor),
        "kind": kind,
        "source": "production_graph",
        "counts_as_real_graph_hit": True,
        "db_basename": os.path.basename(db),
        "anchor_event": anchor,
        "max_hops": max_hops,
        "degradation_class_expected": expected,
        "degradation_class_actual": actual,
        "all_matched": out["all_matched"],
        "match": match,
        "measures": out["measures"],
        "statement": out["statement"],
        "warnings": out["warnings"],
        "config_status": out["config_status"],
        "reproduce": "python why_fallback.py <db> %s --max-hops %d --json" % (anchor, max_hops),
    }
    if requires_env:
        rec["requires_env"] = requires_env
        rec["reproduce"] = ("JVS_WEAK_EXPAND=1 " + rec["reproduce"]) if requires_env else rec["reproduce"]
    return rec


def _fixture(edges, start, expected, tag, cfg, extra=None):
    """合成 `why()` 形状的输入（**非真实图谱**）⇒ 用于「低置信」类的可验证样例。"""
    res = {"ok": True, "start_event": start, "max_hops": 3,
           "events": [{"id": start}], "edges": edges, "skipped_type_reverse": 0}
    out = wf.classify(res, cfg)
    actual = out["degradation_class"]
    rec = {
        "sample_id": "M1-11/%s/fixture-%s" % (expected, tag),
        "kind": "fixture",
        "source": "fixture",
        "counts_as_real_graph_hit": False,
        "note": "合成输入：生产图谱上该类为空集（见 empty_set_proof），故以 fixture 覆盖判据分支",
        "degradation_class_expected": expected,
        "degradation_class_actual": actual,
        "all_matched": out["all_matched"],
        "match": actual == expected,
        "measures": out["measures"],
        "statement": out["statement"],
        "warnings": out["warnings"],
        "config_status": out["config_status"],
        "reproduce": "python -B test_why_fallback.py   # fixture 与回归件同源",
    }
    if extra:
        rec.update(extra)
    return rec


def _edge(frm, to, conf, strength, expandable, eid, direction="forward"):
    return {"edge_id": eid, "from_event": frm, "to_event": to, "type": "cause",
            "strength": strength, "rationale": "fixture", "confidence": conf,
            "source_hash": "fixture", "direction": direction, "expandable": expandable,
            "from_evidence_ref": None, "to_evidence_ref": None}


def full_scan(db, max_hops, cfg):
    """**全量**类分布（两种开关状态）—— 台账 §8 的可复跑命令。

    ⚠️ 与 `_scan_class` 的区别：后者**按类限额提前 break**，只服务于构造样例；
    本函数扫**全部**事件，给出完整分布。两者不可互相替代
    （2026-09-28 审核 P2-1：此前台账只有期望值、没有可执行命令）。
    """
    con = sqlite3.connect(db)
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM events ORDER BY id")]
    finally:
        con.close()
    saved = os.environ.get(WEAK_ENV)
    out = {}
    try:
        for label, val in (("off", None), ("on", "1")):
            if val is None:
                os.environ.pop(WEAK_ENV, None)
            else:
                os.environ[WEAK_ENV] = val
            cnt = {}
            for eid in ids:
                cls = wf.classify(cg.why(db, eid, max_hops=max_hops), cfg)["degradation_class"]
                cnt[cls] = cnt.get(cls, 0) + 1
            out[label] = cnt
    finally:
        if saved is None:
            os.environ.pop(WEAK_ENV, None)
        else:
            os.environ[WEAK_ENV] = saved
    return len(ids), out


def build(db, per_class, max_hops, cfg):
    recs = []

    # ---- 类 1：无链（真实图谱）----
    for anchor in _isolated_events(db, per_class):
        recs.append(_real_record(db, anchor, "no_chain", "positive", max_hops, cfg))
    neg = _event_with_edges(db)
    if neg:
        recs.append(_real_record(db, neg, "not_no_chain", "negative", max_hops, cfg))

    # ---- 类 3：弱关联（真实图谱，**需 JVS_WEAK_EXPAND=1**）----
    saved = os.environ.get(WEAK_ENV)
    os.environ[WEAK_ENV] = "1"
    try:
        for anchor, _out in _scan_class(db, "weak_only", cfg, max_hops, per_class):
            recs.append(_real_record(db, anchor, "weak_only", "positive", max_hops, cfg,
                                     requires_env={WEAK_ENV: "1"}))
    finally:
        if saved is None:
            os.environ.pop(WEAK_ENV, None)
        else:
            os.environ[WEAK_ENV] = saved

    # ---- 类 2：低置信（空集证明 + fixture）----
    census = _low_conf_census(db)
    recs.append({
        "sample_id": "M1-11/low_confidence/EMPTY_SET_PROOF",
        "kind": "empty_set_proof",
        "source": "production_graph",
        "counts_as_real_graph_hit": False,
        "degradation_class_expected": "low_confidence",
        "degradation_class_actual": None,
        "match": None,
        "census": census,
        "conclusion": ("生产图谱上 conf<=0.6 的边 = %d 条、conf<0.5 的边 = %d 条（最小 conf = %s）"
                       "⇒ 该类在该图上**客观为空集**，无真实样例可构造"
                       % (census["n_conf_le_0_6"], census["n_conf_lt_0_5"],
                          census["min_confidence"])),
        "reproduce": ("python make_m1_11_samples.py --db <db> --out <jsonl>  # census 段现算；"
                      "⚠️ 比较前必须 CAST(confidence AS REAL)"),
    })
    # 三条**真正的**低置信正例（**全部边 conf <= 0.6** ⇒ 按现行 `max_confidence` 口径也命中）
    recs.append(_fixture([_edge("F0", "F1", 0.55, "strong", True, "F-A")],
                         "F0", "low_confidence", "single-edge", cfg))
    recs.append(_fixture([_edge("F0", "F1", 0.42, "strong", True, "F-B"),
                          _edge("F1", "F2", 0.58, "strong", True, "F-C")],
                         "F0", "low_confidence", "chain-all-low", cfg))
    recs.append(_fixture([_edge("F0", "F1", None, "strong", True, "F-D")],
                         "F0", "low_confidence", "unparsable-confidence", cfg))
    # 负向 fixture：强边高置信 ⇒ 必须**不**判低置信（防「什么都判降级」）
    recs.append(_fixture([_edge("F0", "F1", 0.95, "strong", True, "F-E")],
                         "F0", "none", "negative-high-confidence", cfg))
    # ⚠️ 口径敏感性**演示件**（**不是**正例）：边置信 0.42 与 0.9 ⇒ 按现行 `max` 口径判 `none`，
    #    按**未采纳**的 `min` 口径会判 `low_confidence`。保留它是为了把裁定 Q-1「未采纳选项」的
    #    后果留在样例里可复核；⚠️ **不计入**「低置信正例」计数（台账须写明）。
    #    （2026-09-28 审核 P1-2：此前该件被当作达标正例计数，属口径缺口，已更正。）
    recs.append(_fixture([_edge("F0", "F1", 0.42, "strong", True, "F-F"),
                          _edge("F1", "F2", 0.9, "strong", True, "F-G")],
                         "F0", "none", "metric-sensitivity-demo", cfg,
                         extra={
                             "demo_for": "fallback.yaml rulings.Q-1 的**未采纳**选项（metric=min_confidence）",
                             "counts_as_low_confidence_positive": False,
                             "would_be_under_other_metric": {"min_confidence": "low_confidence"},
                             "why_kept": "把口径分歧的后果留在样例里，供评审会复核 Q-1 的裁定影响",
                         }))
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser(description="M1-11 三类降级样例生成器（改判后口径）")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", help="样例输出路径（--full-scan 时可不给）")
    ap.add_argument("--per-class", type=int, default=3)
    ap.add_argument("--max-hops", type=int, default=3)
    ap.add_argument("--full-scan", action="store_true",
                    help="只做全量类分布（两种开关状态），不写样例；台账 §8 的复跑命令")
    args = ap.parse_args(argv)

    cfg = wf.load_config()

    if args.full_scan:
        n, dist = full_scan(args.db, args.max_hops, cfg)
        print("全量扫描 %d 事件 ｜ max_hops=%d" % (n, args.max_hops))
        for label in ("off", "on"):
            shown = "<未设置>" if label == "off" else "=1"
            print("  %s%-8s %s" % (WEAK_ENV, shown, dist[label]))
        if args.out:
            with io.open(args.out, "w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "class_census", "n_events": n,
                                    "max_hops": args.max_hops,
                                    "distribution": dist}, ensure_ascii=False,
                                   sort_keys=True) + "\n")
            print("→ %s" % args.out)
        return 0

    if not args.out:
        ap.error("--out 必填（除非用 --full-scan）")

    recs = build(args.db, args.per_class, args.max_hops, cfg)
    with io.open(args.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    def cnt(cls, kind):
        return sum(1 for r in recs
                   if r["degradation_class_expected"] == cls and r["kind"] == kind)

    # ⚠️ 「低置信正例」只数 `expected == low_confidence` 且 `match` 的 fixture；
    #    口径敏感性演示件（expected=none）与负向件**不计入**（审核 P1-2 的更正）。
    lc_pos = [r for r in recs
              if r["kind"] == "fixture" and r["degradation_class_expected"] == "low_confidence"]
    lc_demo = [r for r in recs if r.get("counts_as_low_confidence_positive") is False]
    bad = [r["sample_id"] for r in recs if r.get("match") is False]
    ok = True
    print("无链     真实正例 %d（要求 >=3）｜负例 %d"
          % (cnt("no_chain", "positive"), cnt("not_no_chain", "negative")))
    print("弱关联   真实正例 %d（要求 >=3 或空集证明）" % cnt("weak_only", "positive"))
    print("低置信   空集证明 %d ｜ **正例 fixture %d**（要求 >=3）｜ 演示件 %d ｜ 负例 %d"
          % (cnt("low_confidence", "empty_set_proof"), len(lc_pos), len(lc_demo),
             cnt("none", "fixture") - len(lc_demo)))
    print("记录总数 %d ｜ match=false 的 %d 条 %s"
          % (len(recs), len(bad), ("（" + ", ".join(bad) + "）") if bad else ""))
    if cnt("no_chain", "positive") < 3:
        print("⚠️ 无链类不足 3 条 ⇒ 素材问题", file=sys.stderr)
        ok = False
    if cnt("weak_only", "positive") < 3:
        print("⚠️ 弱关联类不足 3 条 ⇒ 检查 %s=1 与图谱" % WEAK_ENV, file=sys.stderr)
        ok = False
    if len(lc_pos) < 3:
        print("⚠️ 低置信**正例** fixture 不足 3 条", file=sys.stderr)
        ok = False
    if bad:
        print("⚠️ 存在 match=false 的记录 ⇒ 必须逐条说明或修正（不得计入达标）", file=sys.stderr)
        ok = False
    print("→ %s" % args.out)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
