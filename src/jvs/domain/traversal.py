"""Single-source-of-truth causal traversal and NetworkX graph analytics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import networkx as nx

from jvs.domain.models import (
    CausalEdge,
    Event,
    TraversalConfig,
    TraversalResult,
    TraversedEdge,
)
from jvs.domain.rules import is_edge_expandable


def trace_causal_chain(
    start_event: str,
    get_event: Callable[[str], Event | None],
    get_incident_edges: Callable[[str], Iterable[CausalEdge]],
    config: TraversalConfig,
) -> TraversalResult:
    """Two-state bidirectional BFS over causal edges.

    Maintains separate `clue_seen` and `expanded` sets so a non-expandable
    (weak/pending) 1-hop clue never blocks a subsequent expandable strong path
    from reaching and expanding the same target node.
    """
    root = get_event(start_event)
    if root is None:
        return TraversalResult(
            ok=False,
            start_event=start_event,
            max_hops=config.max_hops,
            error=f"event not found: {start_event}",
        )

    events_by_id: dict[str, Event] = {start_event: root}
    order: list[str] = [start_event]
    clue_seen: set[str] = {start_event}
    expanded: set[str] = {start_event}
    used_edges: list[TraversedEdge] = []
    skipped_type_reverse = 0
    allowed_rev = set(config.allowed_reverse_types)
    queue: list[tuple[str, int]] = [(start_event, 0)]
    head = 0

    while head < len(queue):
        node, depth = queue[head]
        head += 1
        if depth >= config.max_hops:
            continue

        incident = list(get_incident_edges(node))
        candidates: list[tuple[CausalEdge, str, str]] = []
        # Forward edges first, then allowed reverse edges.
        for edge in incident:
            if edge.from_event == node:
                candidates.append((edge, edge.to_event, "forward"))
        if config.reverse_enabled:
            for edge in incident:
                if edge.to_event == node and edge.from_event != node:
                    if (edge.type or "") in allowed_rev:
                        candidates.append((edge, edge.from_event, "backward"))
                    else:
                        skipped_type_reverse += 1

        for edge, nxt, direction in candidates:
            can_expand = is_edge_expandable(
                edge,
                conf_gate=config.conf_gate,
                allow_weak_expand=config.allow_weak_expand,
            )
            if can_expand:
                if nxt in expanded:
                    continue
            elif nxt in clue_seen:
                continue

            if nxt not in events_by_id:
                nxt_ev = get_event(nxt)
                if nxt_ev is not None:
                    events_by_id[nxt] = nxt_ev
            if nxt not in clue_seen:
                clue_seen.add(nxt)
                if nxt in events_by_id:
                    order.append(nxt)

            from_ev = events_by_id.get(edge.from_event) or get_event(edge.from_event)
            to_ev = events_by_id.get(edge.to_event) or get_event(edge.to_event)
            if from_ev is not None:
                events_by_id[edge.from_event] = from_ev
            if to_ev is not None:
                events_by_id[edge.to_event] = to_ev

            used_edges.append(
                TraversedEdge(
                    edge_id=edge.id,
                    from_event=edge.from_event,
                    to_event=edge.to_event,
                    type=edge.type,
                    strength=str(edge.strength) if edge.strength is not None else None,
                    rationale=edge.rationale,
                    confidence=edge.confidence,
                    source_hash=edge.source_hash,
                    evidence=edge.evidence,
                    direction=direction,  # type: ignore[arg-type]
                    expandable=can_expand,
                    hop_depth=depth + 1,
                    from_evidence_ref=from_ev.evidence_ref if from_ev else None,
                    to_evidence_ref=to_ev.evidence_ref if to_ev else None,
                )
            )
            if can_expand:
                expanded.add(nxt)
                queue.append((nxt, depth + 1))

    return TraversalResult(
        ok=True,
        start_event=start_event,
        max_hops=config.max_hops,
        events=[events_by_id[eid] for eid in order if eid in events_by_id],
        edges=used_edges,
        skipped_type_reverse=skipped_type_reverse,
    )


def build_networkx_graph(
    events: Iterable[Event],
    edges: Iterable[CausalEdge],
) -> nx.DiGraph:
    """Build a NetworkX directed graph from JVS events and causal edges."""
    graph = nx.DiGraph()
    for ev in events:
        graph.add_node(ev.id, event=ev, ts=ev.ts, type=ev.type, subject=ev.subject)
    for edge in edges:
        if edge.from_event in graph and edge.to_event in graph:
            graph.add_edge(
                edge.from_event,
                edge.to_event,
                edge=edge,
                id=edge.id,
                type=edge.type,
                strength=str(edge.strength) if edge.strength is not None else None,
                confidence=edge.confidence,
            )
    return graph


def shortest_causal_path(
    graph: nx.DiGraph,
    source_id: str,
    target_id: str,
    *,
    strong_only: bool = True,
    conf_gate: float = 0.6,
    allow_weak_expand: bool = False,
) -> list[CausalEdge] | None:
    """Find shortest directed causal path between two events using NetworkX."""
    if source_id not in graph or target_id not in graph:
        return None
    if strong_only:
        sub_edges = [
            (u, v)
            for u, v, data in graph.edges(data=True)
            if is_edge_expandable(
                data["edge"],
                conf_gate=conf_gate,
                allow_weak_expand=allow_weak_expand,
            )
        ]
        view = graph.edge_subgraph(sub_edges)
    else:
        view = graph

    try:
        node_path = nx.shortest_path(view, source=source_id, target=target_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return [view[u][v]["edge"] for u, v in zip(node_path, node_path[1:], strict=False)]


def graph_analytics(graph: nx.DiGraph) -> dict[str, Any]:
    """Compute structural diagnostics over the causal graph via NetworkX."""
    strength_counts: dict[str, int] = {"strong": 0, "weak": 0, "pending": 0, "other": 0}
    edge_type_counts: dict[str, int] = {}
    expandable_count = 0

    for _, _, data in graph.edges(data=True):
        edge: CausalEdge = data["edge"]
        s = str(edge.strength).lower() if edge.strength else "other"
        strength_counts[s if s in strength_counts else "other"] += 1
        t = str(edge.type or "unknown")
        edge_type_counts[t] = edge_type_counts.get(t, 0) + 1
        if is_edge_expandable(edge):
            expandable_count += 1

    undirected = graph.to_undirected()
    components = nx.number_connected_components(undirected) if graph.number_of_nodes() else 0
    isolated = nx.number_of_isolates(graph)
    top_hubs = sorted(
        ((n, graph.degree(n), graph.nodes[n].get("subject")) for n in graph.nodes),
        key=lambda x: x[1],
        reverse=True,
    )[:5]

    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "expandable_edges": expandable_count,
        "strength_counts": strength_counts,
        "edge_type_counts": edge_type_counts,
        "connected_components": components,
        "isolated_nodes": isolated,
        "is_dag": nx.is_directed_acyclic_graph(graph),
        "top_hubs": [
            {"id": nid, "degree": deg, "subject": subj}
            for nid, deg, subj in top_hubs
            if deg > 0
        ],
    }
