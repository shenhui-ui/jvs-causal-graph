"""Tests for HybridRetriever: FTS5 trigram, short-query fallback, RRF, and sqlite-vec."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeEmbeddingClient

from jvs.domain.models import Event
from jvs.infrastructure.retrieval.hybrid import HybridRetriever, extract_question_date
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore


def test_fts5_and_short_chinese_retrieval(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        retriever = HybridRetriever(store)
        hits = retriever.retrieve("为什么在2026-08-21扩容连接池？", k=3)
        assert hits
        top_ids = [h.event.id for h in hits]
        assert "E2" in top_ids
        assert hits[0].fts_rank is not None

        # Short 2-char Chinese query (shorter than trigram 3-char minimum) still works via lexical!
        short_hits = retriever.retrieve("查单", k=3)
        assert short_hits
        assert short_hits[0].event.id == "E6"


def test_question_date_extraction():
    assert extract_question_date("2026-08-21 为什么扩容") == "2026-08-21"
    assert extract_question_date("2026-08 的变更原因") == "2026-08-15"
    assert extract_question_date("8月26日 改了什么") == "2026-08-26"
    assert extract_question_date("为什么这样改") is None


def test_sqlite_vec_hybrid_retrieval(tmp_path: Path):
    db = tmp_path / "vec.db"
    emb = FakeEmbeddingClient(dims=8)
    events = [
        Event(id="E1", ts="2026-08-01", subject="数据库迁移", action="切换", object="连接池"),
        Event(id="E2", ts="2026-08-02", subject="前端样式", action="调整", object="字体大小"),
    ]
    with SQLiteGraphStore(db) as store:
        store.init_db()
        store.ingest_events(events)
        if store._vec_loaded:
            vecs = emb.embed([e.search_text() for e in events])
            store.upsert_embeddings(list(zip([e.id for e in events], vecs, strict=True)))
            retriever = HybridRetriever(store, embedding_client=emb)
            hits = retriever.retrieve("连接池迁移", k=2)
            assert hits[0].event.id == "E1"
            assert hits[0].vec_rank is not None


def test_temporal_neighbors_isolated_by_corpus(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        retriever = HybridRetriever(store)
        e2 = store.get_event("E2")
        assert e2 is not None
        neighbors = retriever.temporal_neighbors(e2, days=12, limit=10)
        neighbor_ids = {ev.id for _, ev in neighbors}
        # E5 is in corpus "群聊2" within +-12 days -> must NOT leak into "群聊1" neighbors!
        assert "E5" not in neighbor_ids
        assert "E1" in neighbor_ids
