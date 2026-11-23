"""Tests for unified two-state causal BFS traversal and NetworkX analytics."""

from __future__ import annotations

from pathlib import Path

from jvs.domain.models import TraversalConfig
from jvs.domain.traversal import graph_analytics, shortest_causal_path, trace_causal_chain
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore


def test_two_state_bfs_does_not_block_strong_path_after_weak_clue(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        cfg = TraversalConfig(max_hops=3, conf_gate=0.6, allow_weak_expand=False)
        res = trace_causal_chain("E1", store.get_event, store.get_incident_edges, cfg)

    assert res.ok is True
    visited_ids = [ev.id for ev in res.events]
    # E1--weak-->E2 (clue only), E1--strong-->E3--strong-->E2--strong-->E4!
    assert "E4" in visited_ids
    edge_ids = [e.edge_id for e in res.edges]
    assert edge_ids == ["A1", "A2", "A3", "A4"]
    a1 = next(e for e in res.edges if e.edge_id == "A1")
    a4 = next(e for e in res.edges if e.edge_id == "A4")
    assert a1.expandable is False
    assert a4.expandable is True
    assert a4.hop_depth == 3


def test_reverse_traversal_respects_allowed_types(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        # Restrict reverse traversal to only `cause` (excluding `supersede`)
        cfg = TraversalConfig(
            max_hops=3,
            conf_gate=0.6,
            reverse_enabled=True,
            allowed_reverse_types=("cause",),
        )
        res = trace_causal_chain("E4", store.get_event, store.get_incident_edges, cfg)
        assert res.ok is True
        assert res.edges == []
        assert res.skipped_type_reverse == 1

        cfg_all = TraversalConfig(
            max_hops=3,
            conf_gate=0.6,
            reverse_enabled=True,
            allowed_reverse_types=("cause", "trigger", "supersede"),
        )
        res_all = trace_causal_chain("E4", store.get_event, store.get_incident_edges, cfg_all)
        assert [e.edge_id for e in res_all.edges] == ["A4", "A3", "A2"]


def test_networkx_shortest_path_and_analytics(sample_graph_db: Path):
    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        graph = store.to_networkx()

    path = shortest_causal_path(graph, "E1", "E4", strong_only=True)
    assert path is not None
    assert [e.id for e in path] == ["A2", "A3", "A4"]

    # E6->E7 has confidence 0.50 -> gated to pending -> unreachable under strong_only=True
    assert shortest_causal_path(graph, "E6", "E7", strong_only=True) is None
    assert shortest_causal_path(graph, "E6", "E7", strong_only=False) is not None

    stats = graph_analytics(graph)
    assert stats["nodes"] == 7
    assert stats["edges"] == 5
    assert stats["expandable_edges"] == 3
    assert stats["strength_counts"]["pending"] == 1
    assert stats["isolated_nodes"] == 1
    assert stats["is_dag"] is True
