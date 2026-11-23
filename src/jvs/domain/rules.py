"""Pure domain rules: edge confidence gating, time ordering, and M1-11 fallback assessment."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from jvs.domain.models import (
    CausalEdge,
    DegradationAssessment,
    DegradationClass,
    DegradationMetrics,
    EdgeStrength,
    FallbackPolicy,
    TraversalResult,
)


def parse_confidence(value: Any) -> float | None:
    """Parse confidence into a finite float in [0.0, 1.0]; return None if invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def normalize_edge_strength(
    edge: CausalEdge,
    conf_gate: float = 0.6,
) -> tuple[EdgeStrength, dict[str, int], str | None]:
    """Normalize an edge's strength against the confidence gate.

    Low-confidence or unparseable edges are relabeled to `pending` (kept as 1-hop clues,
    excluded from multi-hop traversal) rather than dropped.
    """
    counters = {
        "relabeled_pending": 0,
        "gated_conf_none": 0,
        "gated_strength_conflict": 0,
        "gated_strength_missing": 0,
    }
    conf = parse_confidence(edge.confidence)
    cur = str(edge.strength).lower() if edge.strength is not None else None
    warning: str | None = None

    if conf is None:
        new = EdgeStrength.PENDING
        counters["gated_conf_none"] += 1
    elif conf <= conf_gate:
        new = EdgeStrength.PENDING
    else:
        if cur == EdgeStrength.WEAK.value:
            new = EdgeStrength.WEAK
        elif cur == EdgeStrength.PENDING.value:
            new = EdgeStrength.PENDING
        else:
            new = EdgeStrength.STRONG
        if cur is None:
            counters["gated_strength_missing"] += 1

    if new == EdgeStrength.PENDING and cur != EdgeStrength.PENDING.value:
        counters["relabeled_pending"] += 1
        if cur not in (None, EdgeStrength.WEAK.value):
            counters["gated_strength_conflict"] += 1
            warning = (
                f"WARN: edge {edge.id} has strength={cur} with confidence={conf}; "
                "relabeled to pending"
            )
    return new, counters, warning


def is_edge_expandable(
    edge: CausalEdge,
    conf_gate: float = 0.6,
    allow_weak_expand: bool = False,
) -> bool:
    """Two-gate conjunction deciding whether multi-hop BFS may continue through `edge`."""
    strength = str(edge.strength).lower() if edge.strength is not None else None
    if strength == EdgeStrength.PENDING.value:
        return False
    if strength == EdgeStrength.WEAK.value and not allow_weak_expand:
        return False
    conf = parse_confidence(edge.confidence)
    if conf is None:
        return False
    return conf > conf_gate


def is_valid_time_order(from_ts: str | None, to_ts: str | None) -> bool:
    """Verify `from_ts <= to_ts` when both timestamps contain parseable YYYY-MM-DD prefixes."""
    if not from_ts or not to_ts or from_ts == "unknown" or to_ts == "unknown":
        return True
    try:
        d1 = datetime.strptime(from_ts[:10], "%Y-%m-%d").date()
        d2 = datetime.strptime(to_ts[:10], "%Y-%m-%d").date()
        return d1 <= d2
    except ValueError:
        return True


def load_fallback_policy(path: Path) -> FallbackPolicy:
    """Load and validate M1-11 fallback policy YAML cleanly."""
    if not path.is_file():
        raise FileNotFoundError(f"Fallback policy not found: {path}")
    yaml = YAML(typ="safe")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Fallback policy root must be a mapping: {path}")
    policy = FallbackPolicy.model_validate(raw)
    if set(policy.precedence) != {"no_chain", "low_confidence", "weak_only"}:
        raise ValueError("Fallback policy precedence must cover all three degradation classes")
    return policy


def measure_traversal(result: TraversalResult) -> DegradationMetrics:
    """Extract raw numeric measures from a TraversalResult."""
    if not result.ok:
        raise ValueError(f"Cannot assess failed traversal: {result.error}")

    edges = result.edges
    confs = [parse_confidence(e.confidence) for e in edges]
    known = [c for c in confs if c is not None]
    strengths = [e.strength for e in edges]
    max_hops_reached = max((e.hop_depth for e in edges if e.expandable), default=0)
    return DegradationMetrics(
        n_edges=len(edges),
        n_events=len(result.events),
        n_expandable=sum(1 for e in edges if e.expandable),
        max_confidence=max(known) if known else None,
        min_confidence=min(known) if known else None,
        n_unknown_confidence=len(confs) - len(known),
        all_weak=bool(edges) and all(s in ("weak", "pending") for s in strengths),
        hops=max_hops_reached,
    )


def assess_degradation(
    result: TraversalResult,
    policy: FallbackPolicy,
) -> DegradationAssessment:
    """Classify causal chain quality into M1-11 degradation classes with actionable text."""
    metrics = measure_traversal(result)
    matched: list[DegradationClass] = []
    warnings: list[str] = []

    no_chain_spec = policy.classes["no_chain"]
    if no_chain_spec.enabled and metrics.n_edges == 0:
        matched.append(DegradationClass.NO_CHAIN)

    low_conf_spec = policy.classes["low_confidence"]
    threshold = low_conf_spec.threshold if low_conf_spec.threshold is not None else 0.6
    if low_conf_spec.enabled and metrics.n_edges > 0:
        val = (
            metrics.min_confidence
            if low_conf_spec.metric == "min_confidence"
            else metrics.max_confidence
        )
        if val is None or val < threshold:
            matched.append(DegradationClass.LOW_CONFIDENCE)
            if val is None:
                warnings.append("All visited edges lack valid numeric confidence")

    weak_spec = policy.classes["weak_only"]
    if (
        weak_spec.enabled
        and metrics.n_edges > 0
        and metrics.all_weak
        and metrics.hops >= weak_spec.min_hops
    ):
        matched.append(DegradationClass.WEAK_ONLY)

    winner = DegradationClass.NONE
    for cls_name in policy.precedence:
        candidate = DegradationClass(cls_name)
        if candidate in matched:
            winner = candidate
            break

    statement = ""
    if winner != DegradationClass.NONE:
        spec = policy.classes[winner.value]  # type: ignore[index]
        tpl = spec.statement
        if winner == DegradationClass.LOW_CONFIDENCE and metrics.max_confidence is None:
            tpl = spec.statement_unknown or spec.statement
        statement = tpl.format(
            n_edges=metrics.n_edges,
            n_events=metrics.n_events,
            max_confidence=metrics.max_confidence,
            min_confidence=metrics.min_confidence,
            threshold=threshold,
            hops=metrics.hops,
        )

    return DegradationAssessment(
        degraded=winner != DegradationClass.NONE,
        degradation_class=winner,
        matched_classes=matched,
        statement=statement,
        metrics=metrics,
        warnings=warnings,
    )
