"""Tests for SQLiteGraphStore including legacy read-only schema compatibility."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jvs.domain.models import CausalEdge, Event
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore


def test_legacy_readonly_schema_compatibility(tmp_path: Path):
    """Ensure read-only mode works against legacy DBs lacking corpus, reason, or FTS5."""
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE events(
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
            evidence_ref TEXT
        );
        CREATE TABLE causal_edges(
            id TEXT PRIMARY KEY,
            from_event TEXT,
            to_event TEXT,
            type TEXT,
            rationale TEXT,
            confidence REAL,
            source_hash TEXT,
            evidence TEXT,
            strength TEXT
        );
        CREATE TABLE evid_docs(
            hash TEXT PRIMARY KEY,
            path TEXT,
            content TEXT,
            ingested_at TEXT
        );
        INSERT INTO events VALUES(
            'E1', '2026-08-01', 'change', '质检规则', '修改', '采样率',
            '10%', '20%', 0.9, 'abc123', 'high', '群1#abc123'
        );
        INSERT INTO events VALUES(
            'E2', NULL, 'decision', '质检规则', '批准', '上线',
            NULL, '全量', 0.95, 'def456', 'high', '群1#def456'
        );
        INSERT INTO causal_edges VALUES(
            NULL, 'E1', 'E2', 'trigger', '采样调整触发上线', 0.9, 'def456', 'ev', 'strong'
        );
        """
    )
    conn.commit()
    conn.close()

    before_bytes = legacy_db.read_bytes()
    with SQLiteGraphStore(legacy_db, read_only=True) as store:
        st = store.stats()
        assert st["events"] == 2
        assert st["edges"] == 1
        assert st["fts5_indexed"] is False
        assert store.search_fts("质检规则") == []
        ev = store.get_event("E1")
        assert ev is not None and ev.subject == "质检规则"
        ev2 = store.get_event("E2")
        assert ev2 is not None and ev2.ts == "unknown"
        edges = store.get_incident_edges("E1")
        assert len(edges) == 1 and edges[0].id == "legacy:E1->E2"
        tl = store.timeline(subject="质检规则")
        assert len(tl.events) == 2

    assert legacy_db.read_bytes() == before_bytes


def test_timeline_rejects_empty_subject_and_handles_unknown_ts(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        with pytest.raises(ValueError):
            store.timeline(subject="   ")
        res = store.timeline(subject="连接池配置")
        assert len(res.events) == 4
        assert [item.event.ts for item in res.events] == [
            "2026-08-20",
            "2026-08-20",
            "2026-08-21",
            "2026-08-22",
        ]


def test_sqlite_store_duplicate_and_missing_endpoint_handling(tmp_path: Path):
    db = tmp_path / "g.db"
    with SQLiteGraphStore(db) as store:
        store.init_db()
        ev_stats = store.ingest_events(
            [
                Event(id="E1", subject="A"),
                Event(id="E1", subject="Dup"),
                Event(id="  ", subject="Blank"),
            ]
        )
        assert ev_stats == {"inserted": 1, "skipped_duplicate": 1, "skipped_no_id": 1}

        ed_stats = store.ingest_edges(
            [
                CausalEdge(id="A1", from_event="E1", to_event="MISSING", confidence=0.9),
            ]
        )
        assert ed_stats["skipped_missing_endpoint"] == 1
        assert ed_stats["inserted"] == 0
