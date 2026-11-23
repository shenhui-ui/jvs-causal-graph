"""High-level Python SDK facade (`CausalEngine`) unifying storage, retrieval, traversal, and QA."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jvs.application.extraction import (
    extract_events_from_text,
    generate_candidate_pairs,
    judge_candidate_edges,
)
from jvs.application.qa import QAService
from jvs.config import JVSSettings
from jvs.domain.models import (
    CandidatePair,
    CausalEdge,
    DegradationAssessment,
    Event,
    PreparedQA,
    QAAnswer,
    Question,
    RetrievalHit,
    RunMeta,
    TimelineResult,
    TraversalConfig,
    TraversalResult,
)
from jvs.domain.ports import EmbeddingPort, LLMClientPort
from jvs.domain.rules import assess_degradation, load_fallback_policy
from jvs.domain.traversal import (
    graph_analytics,
    shortest_causal_path,
    trace_causal_chain,
)
from jvs.infrastructure.llm.client import OpenAIEmbeddingClient, OpenAILLMClient
from jvs.infrastructure.privacy.gate import PrivacyGate
from jvs.infrastructure.retrieval.hybrid import HybridRetriever
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore


class CausalEngine:
    """Unified entry point for JVS Temporal-Causal Knowledge Graph & Cross-Session QA."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        settings: JVSSettings | None = None,
        read_only: bool = False,
        llm_client: LLMClientPort | None = None,
        embedding_client: EmbeddingPort | None = None,
        privacy_gate: PrivacyGate | None = None,
    ) -> None:
        self.settings = settings or JVSSettings()
        self.db_path = Path(db_path) if db_path is not None else self.settings.db_path
        self.read_only = read_only
        self.store = SQLiteGraphStore(self.db_path, read_only=read_only)

        self.privacy_gate = privacy_gate or PrivacyGate(
            names_path=self.settings.resolve_privacy_names_path(),
            require_names=self.settings.privacy_require_names,
            enabled=self.settings.privacy_enabled,
        )
        self._llm_override = llm_client
        self._embedding_override = embedding_client
        self._llm_cached: LLMClientPort | None = None
        self._emb_cached: EmbeddingPort | None = None

        policy_path = self.settings.resolve_assets_dir() / "fallback.yaml"
        self.fallback_policy = load_fallback_policy(policy_path)

    def __enter__(self) -> CausalEngine:
        self.store.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        self.store.close()

    def get_llm(self) -> LLMClientPort:
        if self._llm_override is not None:
            return self._llm_override
        if self._llm_cached is None:
            conf = self.settings.resolve_llm_config()
            self._llm_cached = OpenAILLMClient(conf, privacy_gate=self.privacy_gate)
        return self._llm_cached

    def get_embedding_client(self) -> EmbeddingPort | None:
        if self._embedding_override is not None:
            return self._embedding_override
        if self._emb_cached is None:
            emb_conf = self.settings.resolve_embedding_config()
            if emb_conf is not None:
                self._emb_cached = OpenAIEmbeddingClient(
                    emb_conf, privacy_gate=self.privacy_gate
                )
        return self._emb_cached

    def _retriever(self) -> HybridRetriever:
        return HybridRetriever(self.store, embedding_client=self.get_embedding_client())

    def _qa_service(self, *, need_llm: bool) -> QAService:
        llm = self.get_llm() if need_llm else self._llm_override
        return QAService(
            self.store,
            self._retriever(),
            settings=self.settings,
            llm=llm,
            privacy_gate=self.privacy_gate,
            fallback_policy=self.fallback_policy,
        )

    def init(self) -> None:
        self.store.init_db()

    def ingest_events(
        self,
        events: Sequence[Event],
        *,
        embed: bool = False,
    ) -> dict[str, int]:
        stats = self.store.ingest_events(events)
        if embed and events:
            emb = self.get_embedding_client()
            if emb is not None:
                texts = [ev.search_text() for ev in events]
                vectors = emb.embed(texts)
                self.store.upsert_embeddings(
                    [(ev.id, vec) for ev, vec in zip(events, vectors, strict=True)]
                )
        return stats

    def ingest_edges(
        self,
        edges: Sequence[CausalEdge],
        *,
        apply_gate: bool = True,
    ) -> dict[str, int]:
        return self.store.ingest_edges(
            edges,
            conf_gate=self.settings.conf_gate,
            apply_gate=apply_gate,
        )

    def ingest_text(
        self,
        text: str,
        *,
        corpus: str | None = None,
        judge_edges: bool = True,
        window_days: int = 7,
        max_candidates: int = 600,
    ) -> dict[str, Any]:
        """Full ingestion pipeline: extract events -> candidate pairs -> judge edges -> persist."""
        self.store.init_db()
        llm = self.get_llm()
        assets = self.settings.resolve_assets_dir()
        existing = self.store.list_events()
        start_idx = len(existing) + 1

        events = extract_events_from_text(
            text,
            llm,
            prompt_path=assets / "prompts" / "event_extract_v2.md",
            corpus=corpus,
            start_index=start_idx,
        )
        ev_stats = self.ingest_events(events, embed=self.get_embedding_client() is not None)

        candidates: list[CandidatePair] = []
        edges: list[CausalEdge] = []
        edge_stats: dict[str, int] = {}

        if judge_edges and events:
            all_events = self.store.list_events()
            candidates = generate_candidate_pairs(
                all_events,
                window_days=window_days,
                max_candidates=max_candidates,
            )
            if candidates:
                events_map = {e.id: e for e in all_events}
                edges = judge_candidate_edges(
                    candidates,
                    events_map,
                    llm,
                    prompt_path=assets / "prompts" / "causal_edges_v2.1.md",
                    conf_gate=self.settings.conf_gate,
                    apply_gate=True,
                )
                if edges:
                    edge_stats = self.ingest_edges(edges, apply_gate=True)

        return {
            "extracted_events": len(events),
            "event_stats": ev_stats,
            "candidate_pairs": len(candidates),
            "judged_edges": len(edges),
            "edge_stats": edge_stats,
        }

    def search(self, query: str, *, k: int | None = None) -> list[RetrievalHit]:
        return self._retriever().retrieve(query, k=k or self.settings.anchor_k)

    def why(
        self,
        event_id: str,
        *,
        config: TraversalConfig | None = None,
    ) -> tuple[TraversalResult, DegradationAssessment]:
        tcfg = config or TraversalConfig(
            max_hops=self.settings.max_hops,
            conf_gate=self.settings.conf_gate,
            allow_weak_expand=self.settings.allow_weak_expand,
            reverse_enabled=self.settings.reverse_enabled,
            allowed_reverse_types=self.settings.reverse_types,
        )
        res = trace_causal_chain(
            event_id,
            self.store.get_event,
            self.store.get_incident_edges,
            tcfg,
        )
        if res.ok:
            assessment = assess_degradation(res, self.fallback_policy)
        else:
            from jvs.domain.models import DegradationClass, DegradationMetrics

            assessment = DegradationAssessment(
                degraded=True,
                degradation_class=DegradationClass.NO_CHAIN,
                matched_classes=[DegradationClass.NO_CHAIN],
                statement=res.error or "event not found",
                metrics=DegradationMetrics(n_edges=0, n_events=0, n_expandable=0),
            )
        return res, assessment

    def timeline(
        self,
        *,
        subject: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> TimelineResult:
        return self.store.timeline(subject=subject, from_ts=from_ts, to_ts=to_ts)

    def shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        strong_only: bool = True,
    ) -> list[CausalEdge] | None:
        graph = self.store.to_networkx()
        return shortest_causal_path(
            graph,
            source_id,
            target_id,
            strong_only=strong_only,
            conf_gate=self.settings.conf_gate,
            allow_weak_expand=self.settings.allow_weak_expand,
        )

    def analytics(self) -> dict[str, Any]:
        return graph_analytics(self.store.to_networkx())

    def prepare_qa(self, question: Question | str) -> PreparedQA:
        return self._qa_service(need_llm=False).prepare(question)

    def ask(self, question: Question | str, *, use_llm: bool = True) -> QAAnswer:
        return self._qa_service(need_llm=use_llm).ask(question, use_llm=use_llm)

    def run_qa_batch(
        self,
        questions_path: Path,
        out_dir: Path,
        *,
        use_llm: bool = True,
        allow_blocked: bool = False,
        discard_incompatible: bool = False,
        dry_run: bool = False,
    ) -> tuple[RunMeta, int]:
        return self._qa_service(need_llm=use_llm).run_batch(
            questions_path,
            out_dir,
            use_llm=use_llm,
            allow_blocked=allow_blocked,
            discard_incompatible=discard_incompatible,
            dry_run=dry_run,
        )

    def export_promptset(
        self,
        questions_path: Path,
        out_dir: Path,
        *,
        baseline_dir: Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return self._qa_service(need_llm=False).export_promptset(
            questions_path,
            out_dir,
            baseline_dir=baseline_dir,
            force=force,
        )

    def stats(self) -> dict[str, Any]:
        return self.store.stats()

    def rebuild_index(self) -> int:
        return self.store.rebuild_fts_index()
