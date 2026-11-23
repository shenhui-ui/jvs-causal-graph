"""Domain port protocols decoupling application logic from storage, LLM, and privacy."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from jvs.domain.models import (
    CausalEdge,
    DegradationAssessment,
    Event,
    QAAnswer,
    RetrievalHit,
    TimelineResult,
    TraversalConfig,
    TraversalResult,
)

TModel = TypeVar("TModel", bound=BaseModel)


class PrivacyGatePort(Protocol):
    def assert_outbound(self, text: str, where: str = "llm") -> dict[str, int]: ...


class LLMClientPort(Protocol):
    @property
    def model_name(self) -> str: ...

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str: ...

    def complete_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> TModel: ...


class EmbeddingPort(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class GraphStorePort(Protocol):
    def init_db(self) -> None: ...

    def ingest_events(self, events: Iterable[Event]) -> dict[str, int]: ...

    def ingest_edges(
        self,
        edges: Iterable[CausalEdge],
        *,
        conf_gate: float = 0.6,
        apply_gate: bool = True,
    ) -> dict[str, int]: ...

    def get_event(self, event_id: str) -> Event | None: ...

    def get_incident_edges(self, event_id: str) -> list[CausalEdge]: ...

    def list_events(self) -> list[Event]: ...

    def list_edges(self) -> list[CausalEdge]: ...

    def stats(self) -> dict[str, Any]: ...


class RetrieverPort(Protocol):
    def retrieve(self, query: str, k: int = 5) -> list[RetrievalHit]: ...

    def temporal_neighbors(
        self,
        anchor: Event,
        *,
        days: int = 12,
        limit: int = 5,
    ) -> list[tuple[int, Event]]: ...


class CausalPort(Protocol):
    def why(
        self,
        event_id: str,
        *,
        config: TraversalConfig | None = None,
    ) -> tuple[TraversalResult, DegradationAssessment]: ...

    def timeline(
        self,
        *,
        subject: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> TimelineResult: ...

    def ask(self, question: str, *, use_llm: bool = True) -> QAAnswer: ...
