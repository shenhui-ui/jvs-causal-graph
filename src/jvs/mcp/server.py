"""MCPServer integration for OpenClaw, Claude Code, Cursor, and Antigravity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from jvs.application.engine import CausalEngine
from jvs.config import JVSSettings
from jvs.domain.models import CausalEdge, Event, TraversalConfig
from jvs.domain.ports import LLMClientPort


def create_mcp_server(
    db_path: str | Path,
    *,
    settings: JVSSettings | None = None,
    read_only: bool = False,
    llm_client: LLMClientPort | None = None,
) -> MCPServer:
    """Create a configured JVS MCPServer exposing causal tools and graph summary resource."""
    cfg = settings or JVSSettings(db_path=Path(db_path))
    target_db = Path(db_path)

    server = MCPServer(
        name="jvs-causal-graph",
        instructions=(
            "Temporal-causal knowledge graph & cross-session causal QA server. "
            "Use causal_search, causal_why, causal_timeline, and causal_ask to inspect "
            "grounded causal chains with source citations."
        ),
    )

    def _open_engine(*, writable: bool = False) -> CausalEngine:
        ro = read_only or (not writable and target_db.exists())
        return CausalEngine(
            target_db,
            settings=cfg,
            read_only=ro,
            llm_client=llm_client,
        )

    @server.tool()
    def causal_search(query: str, k: int = 5) -> dict[str, Any]:
        """Search temporal-causal events via hybrid FTS5 trigram, vector, and date-proximity RRF."""
        with _open_engine(writable=False) as engine:
            hits = engine.search(query, k=k)
            return {
                "query": query,
                "count": len(hits),
                "hits": [
                    {
                        "score": hit.score,
                        "event": hit.event.model_dump(),
                        "fts_rank": hit.fts_rank,
                        "vec_rank": hit.vec_rank,
                        "lexical_rank": hit.lexical_rank,
                        "date_distance_days": hit.date_distance_days,
                    }
                    for hit in hits
                ],
            }

    @server.tool()
    def causal_why(
        event_id: str,
        max_hops: int = 3,
        allow_weak: bool = False,
    ) -> dict[str, Any]:
        """Trace bidirectional causal chains and M1-11 fallback assessment for an event."""
        with _open_engine(writable=False) as engine:
            tcfg = TraversalConfig(
                max_hops=max_hops,
                conf_gate=cfg.conf_gate,
                allow_weak_expand=allow_weak or cfg.allow_weak_expand,
                reverse_enabled=cfg.reverse_enabled,
                allowed_reverse_types=cfg.reverse_types,
            )
            res, assessment = engine.why(event_id, config=tcfg)
            return {
                "traversal": res.model_dump(),
                "degradation": assessment.model_dump(),
            }

    @server.tool()
    def causal_timeline(
        subject: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> dict[str, Any]:
        """Query chronological event timeline with exact subject and date range filters."""
        with _open_engine(writable=False) as engine:
            return engine.timeline(subject=subject, from_ts=from_ts, to_ts=to_ts).model_dump()

    @server.tool()
    def causal_ask(question: str, use_llm: bool = False) -> dict[str, Any]:
        """Answer a causal question grounded in retrieved anchors, chains, and citations."""
        with _open_engine(writable=False) as engine:
            return engine.ask(question, use_llm=use_llm).model_dump()

    @server.tool()
    def causal_ingest_event(
        event_id: str,
        subject: str,
        action: str,
        object_name: str,
        ts: str = "unknown",
        event_type: str = "change",
        before: str | None = None,
        after: str | None = None,
        reason: str | None = None,
        confidence: float = 0.9,
        corpus: str | None = None,
    ) -> dict[str, Any]:
        """Insert a structured temporal event into the causal graph (fails if read-only)."""
        if read_only:
            return {"ok": False, "error": "Server is configured in read-only mode"}
        with _open_engine(writable=True) as engine:
            engine.init()
            ev = Event(
                id=event_id,
                ts=ts,
                type=event_type,
                subject=subject,
                action=action,
                object=object_name,
                before=before,
                after=after,
                reason=reason,
                confidence=confidence,
                corpus=corpus,
            )
            stats = engine.ingest_events([ev])
            return {"ok": True, "stats": stats}

    @server.tool()
    def causal_link_events(
        edge_id: str,
        from_event: str,
        to_event: str,
        edge_type: str = "cause",
        strength: str = "strong",
        rationale: str = "",
        confidence: float = 0.9,
        evidence: str = "",
    ) -> dict[str, Any]:
        """Add a directed causal edge normalized through the confidence gate."""
        if read_only:
            return {"ok": False, "error": "Server is configured in read-only mode"}
        with _open_engine(writable=True) as engine:
            engine.init()
            edge = CausalEdge(
                id=edge_id,
                from_event=from_event,
                to_event=to_event,
                type=edge_type,
                strength=strength,
                rationale=rationale,
                confidence=confidence,
                evidence=evidence,
            )
            stats = engine.ingest_edges([edge], apply_gate=True)
            return {"ok": True, "stats": stats}

    @server.tool()
    def causal_graph_stats() -> dict[str, Any]:
        """Return graph storage statistics and NetworkX structural diagnostics."""
        with _open_engine(writable=False) as engine:
            return {
                "storage": engine.stats(),
                "analytics": engine.analytics(),
            }

    @server.resource("jvs://graph/summary")
    def graph_summary() -> str:
        """JSON summary of current causal graph statistics and structure."""
        with _open_engine(writable=False) as engine:
            payload = {
                "storage": engine.stats(),
                "analytics": engine.analytics(),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

    return server
