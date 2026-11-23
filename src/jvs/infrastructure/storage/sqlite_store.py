"""SQLite persistence layer with FTS5 trigram indexing and optional sqlite-vec support."""

from __future__ import annotations

import re
import sqlite3
import struct
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import networkx as nx

from jvs.domain.models import (
    CausalEdge,
    Event,
    EvidenceDoc,
    TimelineEdge,
    TimelineEvent,
    TimelineResult,
)
from jvs.domain.rules import normalize_edge_strength
from jvs.domain.traversal import build_networkx_graph

try:
    import sqlite_vec  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — optional extra
    sqlite_vec = None  # type: ignore[assignment]

_CORE_SCHEMA = """
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
    corpus TEXT,
    reason TEXT
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
CREATE TABLE IF NOT EXISTS jvs_meta(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON causal_edges(from_event);
CREATE INDEX IF NOT EXISTS idx_edges_to ON causal_edges(to_event);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject);
CREATE INDEX IF NOT EXISTS idx_events_corpus ON events(corpus);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    event_id UNINDEXED,
    content,
    tokenize='trigram'
);
"""


def _serialize_f32(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _escape_fts5_term(term: str) -> str:
    cleaned = term.replace('"', '""').strip()
    return f'"{cleaned}"'


_FTS_STOP_TRIGRAMS = frozenset(("为什么", "怎么样", "是什么", "有没有", "能不能"))


def _extract_trigram_terms(query: str) -> list[str]:
    """Extract safe >=3-char phrases, alnum tokens, and CJK trigrams for FTS5 trigram MATCH."""
    terms: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        if len(tok) >= 3 and tok not in seen and tok not in _FTS_STOP_TRIGRAMS:
            seen.add(tok)
            terms.append(tok)

    for pat in (r"「([^」]{3,30})」", r"【([^】]{3,30})】", r"『([^』]{3,30})』"):
        for m in re.findall(pat, query):
            _add(m.strip())

    for tok in re.findall(r"[0-9A-Za-z]{3,}", query):
        if not tok.isdigit():
            _add(tok)

    for cjk_run in re.findall(r"[\u4e00-\u9fff]{3,}", query):
        for i in range(len(cjk_run) - 2):
            _add(cjk_run[i : i + 3])

    return terms


class SQLiteGraphStore:
    """Indexed SQLite causal graph repository with FTS5 trigram and optional sqlite-vec."""

    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None:
        self.raw_path = str(db_path)
        self.is_memory = self.raw_path == ":memory:"
        self.db_path = Path(db_path) if not self.is_memory else Path(":memory:")
        self.read_only = read_only
        self._conn: sqlite3.Connection | None = None
        self._vec_loaded = False

    def __enter__(self) -> SQLiteGraphStore:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def connect(self) -> sqlite3.Connection:
        """Open (or reuse) the SQLite connection."""
        if self._conn is not None:
            return self._conn

        if self.is_memory:
            conn = sqlite3.connect(":memory:")
        elif self.read_only:
            if not self.db_path.exists():
                raise FileNotFoundError(f"Database file not found: {self.db_path}")
            uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self.read_only and not self.is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")

        self._vec_loaded = self._try_load_vec(conn)
        self._conn = conn
        return conn

    @staticmethod
    def _try_load_vec(conn: sqlite3.Connection) -> bool:
        if sqlite_vec is None or not hasattr(conn, "enable_load_extension"):
            return False
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        return self.connect()

    def _has_table(self, name: str) -> bool:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (name,),
        ).fetchone()
        return row is not None

    def _event_columns(self) -> set[str]:
        conn = self._require_conn()
        return {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}

    def _edge_columns(self) -> set[str]:
        conn = self._require_conn()
        return {row[1] for row in conn.execute("PRAGMA table_info(causal_edges)").fetchall()}

    def init_db(self) -> None:
        """Create or upgrade schema, indexes, and FTS5 table idempotently."""
        if self.read_only:
            raise RuntimeError("Cannot call init_db() on a read-only store")
        conn = self._require_conn()
        if self._has_table("events"):
            existing = self._event_columns()
            if "corpus" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN corpus TEXT")
            if "reason" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN reason TEXT")
        if self._has_table("causal_edges"):
            edge_cols = self._edge_columns()
            if "strength" not in edge_cols:
                conn.execute("ALTER TABLE causal_edges ADD COLUMN strength TEXT")
        conn.executescript(_CORE_SCHEMA)
        conn.commit()

    def ensure_vec_table(self, dimensions: int) -> bool:
        """Ensure sqlite-vec virtual table exists with matching embedding dimensions."""
        if self.read_only or not self._vec_loaded:
            return False
        conn = self._require_conn()
        if self._has_table("events_vec"):
            row = conn.execute(
                "SELECT value FROM jvs_meta WHERE key = 'vec_dimensions'"
            ).fetchone()
            if row is not None and int(row["value"]) != dimensions:
                raise ValueError(
                    f"Existing events_vec dimensions={row['value']} != requested {dimensions}"
                )
            return True
        conn.execute(
            f"CREATE VIRTUAL TABLE events_vec USING vec0("
            f"event_id TEXT PRIMARY KEY, embedding float[{int(dimensions)}])"
        )
        conn.execute(
            "INSERT OR REPLACE INTO jvs_meta(key, value) VALUES('vec_dimensions', ?)",
            (str(dimensions),),
        )
        conn.commit()
        return True

    def rebuild_fts_index(self) -> int:
        """Rebuild the FTS5 trigram index from all events currently in the events table."""
        if self.read_only:
            raise RuntimeError("Cannot rebuild FTS index on a read-only store")
        self.init_db()
        conn = self._require_conn()
        events = self.list_events()
        conn.execute("DELETE FROM events_fts")
        conn.executemany(
            "INSERT INTO events_fts(event_id, content) VALUES (?, ?)",
            [(ev.id, ev.search_text()) for ev in events],
        )
        conn.commit()
        return len(events)

    def ingest_evidence_docs(self, docs: Iterable[EvidenceDoc]) -> int:
        if self.read_only:
            raise RuntimeError("Cannot write to a read-only store")
        conn = self._require_conn()
        inserted = 0
        with conn:
            for doc in docs:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO evid_docs(hash, path, content, ingested_at) "
                    "VALUES (?, ?, ?, ?)",
                    (doc.hash, doc.path, doc.content, doc.ingested_at),
                )
                inserted += cur.rowcount
        return inserted

    def ingest_events(self, events: Iterable[Event]) -> dict[str, int]:
        """Batch-insert events into SQLite and sync FTS5 trigram index atomically."""
        if self.read_only:
            raise RuntimeError("Cannot ingest events into a read-only store")
        conn = self._require_conn()
        stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_no_id": 0}
        has_fts = self._has_table("events_fts")
        cols = self._event_columns()
        has_reason = "reason" in cols
        has_corpus = "corpus" in cols

        with conn:
            for ev in events:
                if not ev.id or not ev.id.strip():
                    stats["skipped_no_id"] += 1
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM events WHERE id = ?", (ev.id,)
                ).fetchone()
                if exists is not None:
                    stats["skipped_duplicate"] += 1
                    continue

                ts = ev.ts.strip() if ev.ts and ev.ts.strip() else "unknown"
                ev_type = str(ev.type) if ev.type is not None else None
                trust = str(ev.trust_level) if ev.trust_level is not None else None

                field_names = [
                    "id",
                    "ts",
                    "type",
                    "subject",
                    "action",
                    "object",
                    "before",
                    "after",
                    "confidence",
                    "source_hash",
                    "trust_level",
                    "evidence_ref",
                ]
                values: list[Any] = [
                    ev.id,
                    ts,
                    ev_type,
                    ev.subject,
                    ev.action,
                    ev.object,
                    ev.before,
                    ev.after,
                    ev.confidence,
                    ev.source_hash,
                    trust,
                    ev.evidence_ref,
                ]
                if has_corpus:
                    field_names.append("corpus")
                    values.append(ev.corpus)
                if has_reason:
                    field_names.append("reason")
                    values.append(ev.reason)

                placeholders = ",".join("?" for _ in field_names)
                conn.execute(
                    f"INSERT INTO events({','.join(field_names)}) VALUES({placeholders})",
                    values,
                )
                if has_fts:
                    conn.execute(
                        "INSERT INTO events_fts(event_id, content) VALUES (?, ?)",
                        (ev.id, ev.search_text()),
                    )
                stats["inserted"] += 1
        return stats

    def ingest_edges(
        self,
        edges: Iterable[CausalEdge],
        *,
        conf_gate: float = 0.6,
        apply_gate: bool = True,
    ) -> dict[str, int]:
        """Batch-insert causal edges with endpoint checks and confidence-gate normalization."""
        if self.read_only:
            raise RuntimeError("Cannot ingest edges into a read-only store")
        conn = self._require_conn()
        stats = {
            "inserted": 0,
            "skipped_duplicate": 0,
            "skipped_missing_endpoint": 0,
            "skipped_no_id": 0,
            "relabeled_pending": 0,
            "gated_conf_none": 0,
            "gated_strength_conflict": 0,
            "gated_strength_missing": 0,
        }
        warnings: list[str] = []

        with conn:
            for edge in edges:
                if not edge.id or not edge.id.strip():
                    stats["skipped_no_id"] += 1
                    continue
                if conn.execute(
                    "SELECT 1 FROM causal_edges WHERE id = ?", (edge.id,)
                ).fetchone():
                    stats["skipped_duplicate"] += 1
                    continue
                fe_ok = conn.execute(
                    "SELECT 1 FROM events WHERE id = ?", (edge.from_event,)
                ).fetchone()
                te_ok = conn.execute(
                    "SELECT 1 FROM events WHERE id = ?", (edge.to_event,)
                ).fetchone()
                if fe_ok is None or te_ok is None:
                    stats["skipped_missing_endpoint"] += 1
                    continue

                if apply_gate:
                    norm_strength, delta, warning = normalize_edge_strength(edge, conf_gate)
                    for k, v in delta.items():
                        stats[k] += v
                    if warning:
                        warnings.append(warning)
                    strength_val: str | None = norm_strength.value
                else:
                    strength_val = str(edge.strength) if edge.strength is not None else None

                edge_type = str(edge.type) if edge.type is not None else None
                conn.execute(
                    "INSERT INTO causal_edges(id, from_event, to_event, type, rationale, "
                    "confidence, source_hash, evidence, strength) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        edge.id,
                        edge.from_event,
                        edge.to_event,
                        edge_type,
                        edge.rationale,
                        edge.confidence,
                        edge.source_hash,
                        edge.evidence,
                        strength_val,
                    ),
                )
                stats["inserted"] += 1
        return stats

    def _event_select_sql(self) -> str:
        cols = self._event_columns()
        base = [
            "id",
            "ts",
            "type",
            "subject",
            "action",
            "object",
            "before",
            "after",
            "confidence",
            "source_hash",
            "trust_level",
            "evidence_ref",
        ]
        if "corpus" in cols:
            base.append("corpus")
        if "reason" in cols:
            base.append("reason")
        return ", ".join(base)

    def _edge_select_sql(self) -> str:
        cols = self._edge_columns()
        strength_col = "strength" if "strength" in cols else "NULL AS strength"
        return (
            "id, from_event, to_event, type, rationale, confidence, "
            f"source_hash, evidence, {strength_col}"
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        data = dict(row)
        if not data.get("ts"):
            data["ts"] = "unknown"
        return Event.model_validate(data)

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> CausalEdge:
        data = dict(row)
        if not data.get("id"):
            data["id"] = f"legacy:{data.get('from_event')}->{data.get('to_event')}"
        return CausalEdge.model_validate(data)

    def get_event(self, event_id: str) -> Event | None:
        conn = self._require_conn()
        row = conn.execute(
            f"SELECT {self._event_select_sql()} FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return self._event_from_row(row) if row is not None else None

    def get_events_by_ids(self, event_ids: Sequence[str]) -> dict[str, Event]:
        if not event_ids:
            return {}
        conn = self._require_conn()
        placeholders = ",".join("?" for _ in event_ids)
        rows = conn.execute(
            f"SELECT {self._event_select_sql()} FROM events WHERE id IN ({placeholders})",
            list(event_ids),
        ).fetchall()
        return {row["id"]: self._event_from_row(row) for row in rows}

    def list_events(self) -> list[Event]:
        conn = self._require_conn()
        rows = conn.execute(
            f"SELECT {self._event_select_sql()} FROM events"
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_edges(self) -> list[CausalEdge]:
        conn = self._require_conn()
        rows = conn.execute(
            f"SELECT {self._edge_select_sql()} FROM causal_edges"
        ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def get_incident_edges(self, event_id: str) -> list[CausalEdge]:
        """Indexed lookup for all outgoing and incoming edges attached to `event_id`."""
        conn = self._require_conn()
        sql = self._edge_select_sql()
        rows = conn.execute(
            f"SELECT {sql} FROM causal_edges WHERE from_event = ? "
            f"UNION "
            f"SELECT {sql} FROM causal_edges WHERE to_event = ?",
            (event_id, event_id),
        ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def timeline(
        self,
        *,
        subject: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> TimelineResult:
        """Query chronological event timeline with exact subject and edge annotations."""
        if subject is not None and not subject.strip():
            raise ValueError("subject cannot be empty or whitespace; pass None to disable filter")

        conn = self._require_conn()
        clauses: list[str] = []
        params: list[Any] = []
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if from_ts is not None:
            clauses.append("(ts = 'unknown' OR ts >= ?)")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("(ts = 'unknown' OR ts <= ?)")
            params.append(to_ts)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        ev_rows = conn.execute(
            f"SELECT {self._event_select_sql()} FROM events{where}",
            params,
        ).fetchall()
        events = [self._event_from_row(r) for r in ev_rows]
        if not events:
            return TimelineResult(
                ok=True, subject=subject, from_ts=from_ts, to_ts=to_ts, events=[]
            )

        ev_ids = [ev.id for ev in events]
        placeholders = ",".join("?" for _ in ev_ids)
        edge_rows = conn.execute(
            f"SELECT {self._edge_select_sql()} FROM causal_edges "
            f"WHERE from_event IN ({placeholders}) OR to_event IN ({placeholders})",
            ev_ids + ev_ids,
        ).fetchall()
        by_node: dict[str, list[TimelineEdge]] = {eid: [] for eid in ev_ids}
        for r in edge_rows:
            edge = self._edge_from_row(r)
            if edge.from_event in by_node:
                by_node[edge.from_event].append(
                    TimelineEdge(
                        edge_id=edge.id,
                        direction="out",
                        from_event=edge.from_event,
                        to_event=edge.to_event,
                        type=edge.type,
                        rationale=edge.rationale,
                        confidence=edge.confidence,
                    )
                )
            if edge.to_event in by_node:
                by_node[edge.to_event].append(
                    TimelineEdge(
                        edge_id=edge.id,
                        direction="in",
                        from_event=edge.from_event,
                        to_event=edge.to_event,
                        type=edge.type,
                        rationale=edge.rationale,
                        confidence=edge.confidence,
                    )
                )

        events.sort(key=lambda ev: (ev.ts == "unknown", ev.ts, ev.id))
        items = [TimelineEvent(event=ev, edges=by_node.get(ev.id, [])) for ev in events]
        return TimelineResult(
            ok=True,
            subject=subject,
            from_ts=from_ts,
            to_ts=to_ts,
            events=items,
        )

    def search_fts(self, query: str, *, limit: int = 20) -> list[tuple[Event, float]]:
        """Search events using FTS5 trigram BM25 if available on this database."""
        if not query.strip() or not self._has_table("events_fts"):
            return []
        terms = _extract_trigram_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join(_escape_fts5_term(t) for t in terms[:24])
        conn = self._require_conn()
        try:
            rows = conn.execute(
                "SELECT event_id, bm25(events_fts) AS rank "
                "FROM events_fts WHERE events_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        if not rows:
            return []
        events_map = self.get_events_by_ids([r["event_id"] for r in rows])
        hits: list[tuple[Event, float]] = []
        for row in rows:
            ev = events_map.get(row["event_id"])
            if ev is not None:
                # FTS5 bm25() returns negative scores where more negative = better match.
                hits.append((ev, float(-row["rank"])))
        return hits

    def upsert_embeddings(self, pairs: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Persist vector embeddings into sqlite-vec `events_vec`."""
        if not pairs:
            return 0
        if self.read_only:
            raise RuntimeError("Cannot store embeddings in a read-only store")
        if not self._vec_loaded:
            raise RuntimeError("sqlite-vec extension is not available")
        dim = len(pairs[0][1])
        self.ensure_vec_table(dim)
        conn = self._require_conn()
        with conn:
            for event_id, vec in pairs:
                conn.execute("DELETE FROM events_vec WHERE event_id = ?", (event_id,))
                conn.execute(
                    "INSERT INTO events_vec(event_id, embedding) VALUES (?, ?)",
                    (event_id, _serialize_f32(vec)),
                )
        return len(pairs)

    def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 20,
    ) -> list[tuple[Event, float]]:
        """Nearest-neighbor vector search over `events_vec` via sqlite-vec."""
        if not self._vec_loaded or not self._has_table("events_vec"):
            return []
        conn = self._require_conn()
        try:
            rows = conn.execute(
                "SELECT event_id, distance FROM events_vec "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_serialize_f32(query_vector), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        if not rows:
            return []
        events_map = self.get_events_by_ids([r["event_id"] for r in rows])
        hits: list[tuple[Event, float]] = []
        for row in rows:
            ev = events_map.get(row["event_id"])
            if ev is not None:
                hits.append((ev, float(1.0 / (1.0 + max(0.0, float(row["distance"]))))))
        return hits

    def to_networkx(self) -> nx.DiGraph:
        """Export current graph into a NetworkX DiGraph for analytics and path finding."""
        return build_networkx_graph(self.list_events(), self.list_edges())

    def stats(self) -> dict[str, Any]:
        """Return database and graph summary counts without mutating read-only DBs."""
        conn = self._require_conn()
        n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        n_edges = conn.execute("SELECT COUNT(*) FROM causal_edges").fetchone()[0]
        n_docs = (
            conn.execute("SELECT COUNT(*) FROM evid_docs").fetchone()[0]
            if self._has_table("evid_docs")
            else 0
        )
        strength_rows = conn.execute(
            "SELECT COALESCE(strength, 'unknown') AS s, COUNT(*) AS c "
            "FROM causal_edges GROUP BY COALESCE(strength, 'unknown')"
        ).fetchall()
        return {
            "db_path": str(self.db_path),
            "read_only": self.read_only,
            "events": int(n_events),
            "edges": int(n_edges),
            "evidence_docs": int(n_docs),
            "strength_breakdown": {r["s"]: int(r["c"]) for r in strength_rows},
            "fts5_indexed": self._has_table("events_fts"),
            "vec_indexed": self._has_table("events_vec"),
            "vec_extension_loaded": self._vec_loaded,
        }
