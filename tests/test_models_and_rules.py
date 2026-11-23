"""Tests for Pydantic domain models, confidence normalization, and M1-11 fallback policy."""

from __future__ import annotations

from pathlib import Path

from jvs.domain.models import (
    CausalEdge,
    DegradationClass,
    EdgeStrength,
    Event,
    TraversalResult,
    TraversedEdge,
)
from jvs.domain.rules import (
    assess_degradation,
    is_edge_expandable,
    is_valid_time_order,
    load_fallback_policy,
    normalize_edge_strength,
    parse_confidence,
)


def test_event_and_edge_alias_validation():
    ev = Event.model_validate({"event_id": "E100", "subject": "缓存", "action": "修改"})
    assert ev.id == "E100"
    assert ev.ts == "unknown"
    assert "缓存" in ev.search_text()

    edge = CausalEdge.model_validate(
        {"edge_id": "A100", "from_event": "E1", "to_event": "E2", "strength": "strong"}
    )
    assert edge.id == "A100"


def test_parse_confidence_rejects_bool_nan_inf_and_out_of_range():
    assert parse_confidence(True) is None
    assert parse_confidence(False) is None
    assert parse_confidence(float("nan")) is None
    assert parse_confidence(float("inf")) is None
    assert parse_confidence(-0.1) is None
    assert parse_confidence(1.5) is None
    assert parse_confidence("0.85") == 0.85
    assert parse_confidence(0.6) == 0.6


def test_normalize_and_expandable_gate():
    low_strong = CausalEdge(
        id="A1", from_event="E1", to_event="E2", strength="strong", confidence=0.6
    )
    norm, stats, warn = normalize_edge_strength(low_strong, conf_gate=0.6)
    assert norm == EdgeStrength.PENDING
    assert stats["relabeled_pending"] == 1
    assert stats["gated_strength_conflict"] == 1
    assert warn is not None
    assert is_edge_expandable(low_strong, conf_gate=0.6) is False

    high_weak = CausalEdge(
        id="A2", from_event="E1", to_event="E2", strength="weak", confidence=0.95
    )
    norm2, _, warn2 = normalize_edge_strength(high_weak, conf_gate=0.6)
    assert norm2 == EdgeStrength.WEAK
    assert warn2 is None
    assert is_edge_expandable(high_weak, conf_gate=0.6, allow_weak_expand=False) is False
    assert is_edge_expandable(high_weak, conf_gate=0.6, allow_weak_expand=True) is True


def test_time_order_validation():
    assert is_valid_time_order("2026-08-20", "2026-08-21") is True
    assert is_valid_time_order("2026-08-21", "2026-08-20") is False
    assert is_valid_time_order("unknown", "2026-08-20") is True


def test_fallback_policy_assessment(assets_dir: Path):
    policy = load_fallback_policy(assets_dir / "fallback.yaml")

    no_chain_res = TraversalResult(ok=True, start_event="E1", max_hops=3, events=[], edges=[])
    deg1 = assess_degradation(no_chain_res, policy)
    assert deg1.degraded is True
    assert deg1.degradation_class == DegradationClass.NO_CHAIN

    low_conf_res = TraversalResult(
        ok=True,
        start_event="E1",
        max_hops=3,
        events=[],
        edges=[
            TraversedEdge(
                edge_id="A1",
                from_event="E1",
                to_event="E2",
                strength="pending",
                confidence=0.4,
                direction="forward",
                expandable=False,
                hop_depth=1,
            )
        ],
    )
    deg2 = assess_degradation(low_conf_res, policy)
    assert deg2.degradation_class == DegradationClass.LOW_CONFIDENCE

    weak_res = TraversalResult(
        ok=True,
        start_event="E1",
        max_hops=3,
        events=[],
        edges=[
            TraversedEdge(
                edge_id="A1",
                from_event="E1",
                to_event="E2",
                strength="weak",
                confidence=0.8,
                direction="forward",
                expandable=True,
                hop_depth=1,
            ),
            TraversedEdge(
                edge_id="A2",
                from_event="E2",
                to_event="E3",
                strength="weak",
                confidence=0.8,
                direction="forward",
                expandable=True,
                hop_depth=2,
            ),
        ],
    )
    deg3 = assess_degradation(weak_res, policy)
    assert deg3.degradation_class == DegradationClass.WEAK_ONLY
