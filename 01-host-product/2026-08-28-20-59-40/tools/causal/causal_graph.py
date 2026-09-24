# -*- coding: utf-8 -*-
"""因果层 PoC:SQLite 邻接表建库与端口函数(纯标准库,单文件)。

由链 A 任务书 S5 生成,2026-08-29。

本模块为因果推理层提供一个最小可用的数据底盘:建立 events / causal_edges /
evid_docs 三张 SQLite 表(邻接表建模因果图),提供写入与两类查询端口函数,
并暴露一个命令行界面。不依赖任何第三方库,不引入 Graphiti。

设计要点
--------
- 数据模型:事件(events)是节点,因果边(causal_edges,from_event -> to_event)
  是有向弧,构成一张因果邻接表。evid_docs 记录原始证据文档。
- 写入端口:ingest_events / ingest_edges 接收 cursor,由调用方管理事务,
  便于被链 B 或后续 M1 直接 import 并把提交时机握在自己手里。
- 入库闸门:边入库前按 `confidence` 归一 `strength`(见 `CONF_GATE` / `_normalize_edge`)——
  `confidence` 不可解析或 ≤ 0.6 ⇒ 改标 `pending` **但仍入库**(保留"一跳线索"用途,
  不再参与多跳);> 0.6 ⇒ `strong`/`weak` 二值。可用环境变量 `JVS_CONF_GATE=0` 关闭。
- 查询端口:why / timeline 接收 db_path,自行打开连接并关闭。
- 数据校验:事件 id 唯一(重复报警并跳过);边必须两端事件已存在(否则跳过
  并计入 warn);ts 为空时落为字符串 "unknown"。

============================== README ==============================
命令示例
--------
    初始化建库:
        python causal_graph.py init ./g.db

    导入 events 与 edges(events 先于 edges,整体一个批量事务):
        python causal_graph.py ingest ./g.db events.jsonl edges.jsonl

    因果溯源(双向 BFS,优先 to_event 正向,反向仅限 cause|trigger):
        python causal_graph.py why ./g.db evt-001 --max-hops 3

    事件时间线(按 ts 排序,含边标注,可叠加 subject/时间区间过滤):
        python causal_graph.py timeline ./g.db --subject alice
        python causal_graph.py timeline ./g.db --from 2026-01-01 --to 2026-12-31

输出格式
--------
why:以 JSON 结构返回;终端打印为「事件列表 + 每条边 rationale/evidence_ref」。
    events 按 BFS 发现顺序,edges 每条含
    from --type--> to,confidence、rationale、expandable、from/to 侧 evidence_ref。
timeline:按 ts 升序(unknown 排最后)的事件列表,每个事件下方缩进列出与其
    关联的边(out = 该事件为 from_event,in = 该事件为 to_event)。
====================================================================
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict, deque

# 允许进入反向 BFS 的边类型(反向 = 顺着 to_event -> from_event 回溯)。
_ALLOWED_REVERSE_TYPES = ("cause", "trigger", "supersede", "evolve", "resolve")

# 入库闸门阈值。口径来源:`tools/causal/README-run.md`
# 「`strength` 由 `confidence` 推断:>0.6→弱以上、≤0.6→weak;pending = confidence ≤ 0.6
# (与提示词铁律一致,不参与多跳)」。
# ⇒ 判定「能否继续多跳」的唯一权威是 **confidence**(见 `_edge_expandable`),
#    `strength` 只是它的**展示标签**,不得与 `confidence` 不一致。
CONF_GATE = 0.6


def _gate_enabled():
    """闸门可回退开关:`JVS_CONF_GATE=0` 时退回旧行为(不合一、不拒绝)。"""
    return os.environ.get("JVS_CONF_GATE", "1") != "0"


def _conf_of(edge):
    """取 confidence 为 float;无法解析时返回 None(视为不可信)。"""
    c = edge.get("confidence")
    if c is None:
        return None
    try:
        return float(c)
    except (TypeError, ValueError):
        return None


def _normalize_edge(edge, stats):
    """按 `confidence` 归一 `strength` 标签,返回**应当写入库的 strength 值**。

    规则(与 `_edge_expandable` 完全对齐,单一事实源):
      - `confidence` 不可解析 / 为 None  → 标 `pending`(不可信 ⇒ 不入多跳)
      - `confidence` <= `CONF_GATE`      → 标 `pending`
      - 其余                              → 保留 strong/weak 语义,但**取值只允许这两种**
        (`medium` 未出现在任何边集;若出现则压平为 `strong`,与 `_edge_expandable`
         的「非 weak/pending 即视为可展开」保持一致)

    ⚠️ 本函数**只改标、不剔边**:低置信边仍入库,以保留其「一跳线索」用途
    (`llm_answer` 明文:低置信边可作当前节点的一跳线索,但不继续扩展)。
    """
    conf = _conf_of(edge)
    cur = edge.get("strength")
    if conf is None:
        new = "pending"
        stats["gated_conf_none"] += 1
    elif conf <= CONF_GATE:
        new = "pending"
    else:
        new = "weak" if cur == "weak" else "strong"
        if cur is None:
            stats["gated_strength_missing"] += 1
    if new == "pending" and cur != "pending":
        stats["relabeled_pending"] += 1
        if cur not in (None, "weak"):
            # ⚠️ 一致性缺陷面:`strength` 由判边模型自报,可能与 `confidence` 相反。
            # ⚠️ 更正(2026-09-23,实测):**这些边从来就不参与多跳** ——
            #    `_edge_expandable` 是「strength ∉ {weak,pending}」**AND**「conf > 0.6」的合取,
            #    `conf <= 0.6` 已单独使第二项为假。故改标**并未**使其「退出」多跳;
            #    本计数是「标签与证据自相矛盾」的**一致性**指标,**不是**「堵口拦下的可达性」。
            #    (生产库实测:`conf<=0.6` 中可展开 = 0;`strong ∧ conf<=0.6` 仅 A5530 一条。)
            # 只计数+告警,**不阻断**(存量处置另立专项)。
            stats["gated_strength_conflict"] += 1
            print("WARN: strength=%s 与 confidence=%s 不一致(按 confidence 改标 pending): %s"
                  % (cur, conf, edge.get("id") or edge.get("edge_id")),
                  file=sys.stderr)
    return new


def _edge_expandable(edge):
    """只有非 weak/pending 且置信度高于 0.6 的边才可继续多跳。"""
    if edge.get("strength") in ("weak", "pending"):
        return False
    confidence = edge.get("confidence")
    if confidence is None:
        return False
    try:
        return float(confidence) > 0.6
    except (TypeError, ValueError):
        return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id TEXT PRIMARY KEY,
    ts TEXT,
    type TEXT,
    subject TEXT,
    action TEXT,
    object TEXT,
    before TEXT,
    after TEXT,
    confidence REAL,
    source_hash TEXT,
    trust_level TEXT,
    evidence_ref TEXT,
    corpus TEXT
);
CREATE TABLE IF NOT EXISTS causal_edges(
    id TEXT PRIMARY KEY,
    from_event TEXT,
    to_event TEXT,
    type TEXT,
    rationale TEXT,
    confidence REAL,
    source_hash TEXT,
    evidence TEXT,
    strength TEXT,
    FOREIGN KEY(from_event) REFERENCES events(id),
    FOREIGN KEY(to_event) REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS evid_docs(
    hash TEXT PRIMARY KEY,
    path TEXT,
    content TEXT,
    ingested_at TEXT
);
"""


def connect(db_path):
    """打开数据库连接,启用外键约束,并使用 sqlite3.Row 行工厂。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# 建库 / 端口函数
# ---------------------------------------------------------------------------
def init_db(db_path):
    """按 S5 定案 schema 建表(幂等,可重复执行)。"""
    conn = connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return db_path


def ingest_events(cursor, events):
    """批量导入事件。

    参数:
        cursor: sqlite3.Cursor,事务由调用方管理。
        events: 可迭代的 dict,每项至少含 id;ts 为空落为 "unknown"。
    返回:
        统计 dict:{inserted, skipped_duplicate, skipped_no_id}。
    校验:事件 id 唯一;重复 id 触发 warn 并跳过。
    """
    stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_no_id": 0}
    for ev in events:
        eid = ev.get("id") or ev.get("event_id")  # 兼容抽取 prompt 的 event_id 字段
        if not eid:
            stats["skipped_no_id"] += 1
            print("WARN: 事件缺少 id,跳过:", ev, file=sys.stderr)
            continue
        if cursor.execute("SELECT 1 FROM events WHERE id = ?", (eid,)).fetchone():
            stats["skipped_duplicate"] += 1
            print("WARN: 事件 id 重复,跳过:", eid, file=sys.stderr)
            continue
        ts = ev.get("ts")
        if not ts:
            ts = "unknown"
        event_cols = {row[1] for row in cursor.execute("PRAGMA table_info(events)")}
        if "corpus" in event_cols:
            cursor.execute(
                "INSERT INTO events(id, ts, type, subject, action, object, before, after,"
                " confidence, source_hash, trust_level, evidence_ref, corpus)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, ts, ev.get("type"), ev.get("subject"), ev.get("action"),
                 ev.get("object"), ev.get("before"), ev.get("after"),
                 ev.get("confidence"), ev.get("source_hash"),
                 ev.get("trust_level"), ev.get("evidence_ref"), ev.get("corpus")),
            )
        else:
            cursor.execute(
                "INSERT INTO events(id, ts, type, subject, action, object, before, after,"
                " confidence, source_hash, trust_level, evidence_ref)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, ts, ev.get("type"), ev.get("subject"), ev.get("action"),
                 ev.get("object"), ev.get("before"), ev.get("after"),
                 ev.get("confidence"), ev.get("source_hash"),
                 ev.get("trust_level"), ev.get("evidence_ref")),
            )
        stats["inserted"] += 1
    return stats


def ingest_edges(cursor, edges):
    """批量导入因果边。

    参数:
        cursor: sqlite3.Cursor,事务由调用方管理。
        edges: 可迭代的 dict,每项至少含 id、from_event、to_event。
    返回:
        统计 dict:{inserted, skipped_duplicate, skipped_missing_endpoint,
        skipped_no_id, relabeled_pending, gated_conf_none,
        gated_strength_conflict, gated_strength_missing}。
    校验:边 id 唯一;两端事件必须已存在,否则跳过并 warn 计数。
    闸门:`confidence` 不可解析或 ≤ `CONF_GATE` 的边**改标 `pending` 后仍入库**(不剔边),
        以同时满足「不参与多跳」与「保留一跳线索」两个用途;
        可用 `JVS_CONF_GATE=0` 退回旧行为(不改标)。
    """
    stats = {"inserted": 0, "skipped_duplicate": 0,
             "skipped_missing_endpoint": 0, "skipped_no_id": 0,
             "relabeled_pending": 0, "gated_conf_none": 0,
             "gated_strength_conflict": 0, "gated_strength_missing": 0}
    gate = _gate_enabled()
    for edge in edges:
        eid = edge.get("id") or edge.get("edge_id")  # 兼容抽取 prompt 的 edge_id 字段
        if not eid:
            stats["skipped_no_id"] += 1
            print("WARN: 边缺少 id,跳过:", edge, file=sys.stderr)
            continue
        if cursor.execute("SELECT 1 FROM causal_edges WHERE id = ?", (eid,)).fetchone():
            stats["skipped_duplicate"] += 1
            print("WARN: 边 id 重复,跳过:", eid, file=sys.stderr)
            continue
        fe = edge.get("from_event")
        te = edge.get("to_event")
        fe_ok = cursor.execute("SELECT 1 FROM events WHERE id = ?", (fe,)).fetchone()
        te_ok = cursor.execute("SELECT 1 FROM events WHERE id = ?", (te,)).fetchone()
        if not (fe_ok and te_ok):
            stats["skipped_missing_endpoint"] += 1
            print("WARN: 边两端事件缺失,跳过:", eid,
                  "(from=%s, to=%s)" % (fe, te), file=sys.stderr)
            continue
        cursor.execute(
            "INSERT INTO causal_edges(id, from_event, to_event, type, rationale,"
            " confidence, source_hash, evidence, strength) VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, fe, te, edge.get("type"), edge.get("rationale"),
             edge.get("confidence"), edge.get("source_hash"), edge.get("evidence"),
             _normalize_edge(edge, stats) if gate else edge.get("strength")),
        )
        stats["inserted"] += 1
    return stats


def why(db_path, event_id, max_hops=3):
    """沿 causal_edges 双向 BFS 的因果溯源。

    低置信/weak/pending 边可作为当前节点的一跳线索，但不会将目标节点继续入队。

    遍历规则:
        - 深度上限 max_hops(走过的边数)。
        - 优先 to_event 方向(即当前节点作为 from_event 时,顺着因果推进)。
        - 反向(当前节点作为 to_event 时,回溯 to from_event)仅当边类型
          type in {cause, trigger};其余方向遍历一律剔除。
        - 用 visited 集合防环。
    返回:
        dict:{ok, start_event, max_hops, events, edges, skipped_type_reverse}
        其中 events 为按发现顺序的事件列表(含 evidence_ref),edges 为每条
        遍历到的边(含 rationale、confidence 与来自事件侧的 evidence_ref)。
    若起始事件不存在,返回 {ok: False, error: ...}。
    """
    conn = connect(db_path)
    try:
        event_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
        event_corpus = ", corpus" if "corpus" in event_cols else ""
        ev_rows = conn.execute(
            "SELECT id, ts, type, subject, action, object, before, after,"
            " confidence, source_hash, evidence_ref" + event_corpus + " FROM events"
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(causal_edges)")]
        if "strength" in cols:
            edge_rows = conn.execute(
                "SELECT id, from_event, to_event, type, rationale, confidence,"
                " source_hash, evidence, strength FROM causal_edges"
            ).fetchall()
        else:
            edge_rows = conn.execute(
                "SELECT id, from_event, to_event, type, rationale, confidence,"
                " source_hash, evidence, NULL AS strength FROM causal_edges"
            ).fetchall()
    finally:
        conn.close()

    events = {}
    for r in ev_rows:
        events[r["id"]] = dict(r)

    if event_id not in events:
        return {"ok": False, "error": "event not found: %s" % event_id,
                "start_event": event_id, "max_hops": max_hops}

    forward = defaultdict(list)   # node -> [(edge, to_node)]
    backward = defaultdict(list)  # node -> [(edge, from_node)]
    for r in edge_rows:
        e = dict(r)
        # 保留低置信边作为当前节点的一跳线索；是否可继续扩展由 BFS 单独判断。
        forward[e["from_event"]].append((e, e["to_event"]))
        backward[e["to_event"]].append((e, e["from_event"]))

    visited = {event_id}
    order = [event_id]
    used_edges = []          # 依发现顺序记录
    skipped_type_reverse = 0
    queue = deque([(event_id, 0)])

    while queue:
        node, depth = queue.popleft()
        if depth >= max_hops:
            continue
        # 先收集候选,保证正向(to_event 方向)优先于反向。
        candidates = []
        for e, nxt in forward.get(node, []):
            if nxt not in visited:
                candidates.append((e, nxt, "forward"))
        for e, nxt in backward.get(node, []):
            if (e["type"] or "") in _ALLOWED_REVERSE_TYPES:
                if nxt not in visited:
                    candidates.append((e, nxt, "backward"))
            else:
                skipped_type_reverse += 1
        for e, nxt, direction in candidates:
            if nxt in visited:
                continue
            visited.add(nxt)
            order.append(nxt)
            used_edges.append({
                "edge_id": e["id"],
                "from_event": e["from_event"],
                "to_event": e["to_event"],
                "type": e["type"],
                "strength": e.get("strength"),
                "rationale": e["rationale"],
                "confidence": e["confidence"],
                "source_hash": e["source_hash"],
                "direction": direction,
                "expandable": _edge_expandable(e),
                "from_evidence_ref": events.get(e["from_event"], {}).get("evidence_ref"),
                "to_evidence_ref": events.get(e["to_event"], {}).get("evidence_ref"),
            })
            if _edge_expandable(e):
                queue.append((nxt, depth + 1))

    return {
        "ok": True,
        "start_event": event_id,
        "max_hops": max_hops,
        "events": [events[nid] for nid in order],
        "edges": used_edges,
        "skipped_type_reverse": skipped_type_reverse,
    }


def timeline(db_path, subject=None, from_=None, to_=None):
    """按 ts 排序输出事件时间线,并为每个事件附带关联的边标注。

    过滤:subject 精确匹配事件 subject 字段;from_/to_ 为闭区间字符串比较,
    对 ts == "unknown" 的事件不做区间剔除(仍参与排序,排最后)。
    返回:
        dict:{ok, subject, from, to, events}。
        events 内每个事件含 edges 列表,元素为 {edge_id, direction, ...}。
    """
    conn = connect(db_path)
    try:
        event_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
        event_corpus = ", corpus" if "corpus" in event_cols else ""
        ev_rows = conn.execute(
            "SELECT id, ts, type, subject, action, object, before, after,"
            " confidence, source_hash, evidence_ref" + event_corpus + " FROM events"
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT id, from_event, to_event, type, rationale, confidence,"
            " source_hash, evidence FROM causal_edges"
        ).fetchall()
    finally:
        conn.close()

    edge_by_node = defaultdict(list)
    for r in edge_rows:
        e = dict(r)
        edge_by_node[e["from_event"]].append((e, "out"))  # 该事件 = from_event
        edge_by_node[e["to_event"]].append((e, "in"))     # 该事件 = to_event

    result = []
    for r in ev_rows:
        ev = dict(r)
        if subject is not None and subject not in (ev["subject"] or ""):
            continue
        ts = ev["ts"]
        if from_ is not None and ts != "unknown" and ts < from_:
            continue
        if to_ is not None and ts != "unknown" and ts > to_:
            continue
        incident = []
        for e, direction in edge_by_node.get(ev["id"], []):
            incident.append({
                "edge_id": e["id"],
                "direction": direction,
                "from_event": e["from_event"],
                "to_event": e["to_event"],
                "type": e["type"],
                "rationale": e["rationale"],
                "confidence": e["confidence"],
            })
        ev["edges"] = incident
        result.append(ev)

    # 'unknown' 排最后,再按 ts、id 升序。
    result.sort(key=lambda ev: (ev["ts"] == "unknown", ev["ts"], ev["id"]))
    return {"ok": True, "subject": subject, "from": from_, "to": to_,
            "events": result}


# ---------------------------------------------------------------------------
# 终端输出
# ---------------------------------------------------------------------------
def _fmt_event(ev):
    parts = ["id=%s" % ev["id"], "ts=%s" % ev["ts"]]
    if ev.get("type"):
        parts.append("type=%s" % ev["type"])
    if ev.get("subject"):
        parts.append("subject=%s" % ev["subject"])
    if ev.get("action"):
        parts.append("action=%s" % ev["action"])
    if ev.get("object"):
        parts.append("object=%s" % ev["object"])
    return "  " + "  ".join(parts) + "\n"


def _print_why(res):
    if not res["ok"]:
        print("ERROR:", res["error"])
        return
    print("证据链:start=%s  max_hops=%s  访问事件=%d  遍历边=%d" % (
        res["start_event"], res["max_hops"],
        len(res["events"]), len(res["edges"])))
    if res.get("skipped_type_reverse"):
        print("  (另有 %d 条反向边因类型不在 %s 被剔除)"
              % (res["skipped_type_reverse"], _ALLOWED_REVERSE_TYPES))
    print("事件:")
    for ev in res["events"]:
        print(_fmt_event(ev), end="")
    print("边:")
    for e in res["edges"]:
        line = "  %s: %s --%s--> %s  conf=%s" % (
            e["edge_id"], e["from_event"], e["type"], e["to_event"],
            e["confidence"])
        print(line)
        if e["rationale"]:
            print("      rationale: %s" % e["rationale"])
        print("      evidence_ref(from)=%s  evidence_ref(to)=%s" % (
            e["from_evidence_ref"], e["to_evidence_ref"]))


def _print_timeline(res):
    if not res["ok"]:
        print("ERROR:", res["error"])
        return
    flt = []
    if res["subject"] is not None:
        flt.append("subject=%s" % res["subject"])
    if res["from"] is not None:
        flt.append("from=%s" % res["from"])
    if res["to"] is not None:
        flt.append("to=%s" % res["to"])
    print("时间线(%s):共 %d 个事件"
          % (", ".join(flt) if flt else "全部", len(res["events"])))
    for ev in res["events"]:
        print("  ts=%s  id=%s  type=%s" % (ev["ts"], ev["id"],
                                            ev.get("type") or ""))
        for e in ev["edges"]:
            arrow = "--%s--> %s" % (e["type"], e["to_event"]) \
                if e["direction"] == "out" else \
                "%s <--%s--" % (e["from_event"], e["type"])
            extra = "  conf=%s" % e["confidence"] if e["confidence"] is not None else ""
            print("      %s: %s%s" % (e["edge_id"], arrow, extra))
            if e["rationale"]:
                print("        rationale: %s" % e["rationale"])


# ---------------------------------------------------------------------------
# 导入辅助
# ---------------------------------------------------------------------------
def _load_jsonl(path):
    """逐行读取 JSONL,返回 dict 列表;空行/解析失败的行跳过并 warn。"""
    items = []
    with open(path, "r", encoding="utf-8-sig") as fh:  # 容忍 BOM,兼容外部工具输出
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("WARN: %s 第 %d 行 JSON 解析失败,跳过: %s"
                      % (path, lineno, exc), file=sys.stderr)
    return items


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="因果层 PoC:SQLite 邻接表(由链 A 任务书 S5 生成,2026-08-29)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="建表(events/causal_edges/evid_docs)")
    p_init.add_argument("db")

    p_ingest = sub.add_parser("ingest", help="导入 events 与 edges(批量事务)")
    p_ingest.add_argument("db")
    p_ingest.add_argument("events_jsonl")
    p_ingest.add_argument("edges_jsonl")

    p_why = sub.add_parser("why", help="因果溯源(双向 BFS)")
    p_why.add_argument("db")
    p_why.add_argument("event_id")
    p_why.add_argument("--max-hops", type=int, default=3)

    p_tl = sub.add_parser("timeline", help="事件时间线(含边标注)")
    p_tl.add_argument("db")
    p_tl.add_argument("--subject", default=None)
    p_tl.add_argument("--from", dest="from_", default=None)
    p_tl.add_argument("--to", dest="to_", default=None)

    args = parser.parse_args(argv)

    if args.command == "init":
        init_db(args.db)
        print("已初始化数据库:", args.db)
        return 0

    if args.command == "ingest":
        events = _load_jsonl(args.events_jsonl)
        edges = _load_jsonl(args.edges_jsonl)
        conn = connect(args.db)
        try:
            curs = conn.cursor()
            ev_stats = ingest_events(curs, events)
            ed_stats = ingest_edges(curs, edges)
            conn.commit()
        finally:
            conn.close()
        print("events:", ev_stats)
        print("edges:", ed_stats)
        return 0

    if args.command == "why":
        _print_why(why(args.db, args.event_id, args.max_hops))
        return 0

    if args.command == "timeline":
        _print_timeline(timeline(args.db, args.subject, args.from_, args.to_))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
