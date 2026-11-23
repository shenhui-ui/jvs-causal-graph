"""Pure Pydantic v2 domain models for JVS."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class EventType(StrEnum):
    DECISION = "decision"
    CHANGE = "change"
    MEETING = "meeting"
    COMMIT = "commit"
    BUGFIX = "bugfix"
    SCHEDULE = "schedule"
    ASSIGN = "assign"
    OTHER = "other"


class EdgeType(StrEnum):
    CAUSE = "cause"
    SUPERSEDE = "supersede"
    EVOLVE = "evolve"
    RESOLVE = "resolve"
    TRIGGER = "trigger"


class EdgeStrength(StrEnum):
    STRONG = "strong"
    WEAK = "weak"
    PENDING = "pending"


class TrustLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DegradationClass(StrEnum):
    NONE = "none"
    NO_CHAIN = "no_chain"
    LOW_CONFIDENCE = "low_confidence"
    WEAK_ONLY = "weak_only"


class Event(BaseModel):
    """Atomic temporal event node in the causal graph."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(validation_alias=AliasChoices("id", "event_id"))
    ts: str = "unknown"
    type: EventType | str | None = None
    subject: str | None = None
    action: str | None = None
    object: str | None = None
    before: str | None = None
    after: str | None = None
    reason: str | None = None
    confidence: float | None = None
    source_hash: str | None = None
    trust_level: TrustLevel | str | None = None
    evidence_ref: str | None = None
    corpus: str | None = None

    def search_text(self) -> str:
        """Concatenate searchable fields for lexical indexing and fallback scoring."""
        parts = [self.subject, self.object, self.after, self.before, self.reason, self.action]
        return " ".join(str(p) for p in parts if p)


class CausalEdge(BaseModel):
    """Directed causal or associative edge from an earlier event to a later event."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(validation_alias=AliasChoices("id", "edge_id"))
    from_event: str
    to_event: str
    type: EdgeType | str | None = None
    strength: EdgeStrength | str | None = None
    rationale: str | None = None
    confidence: float | None = None
    source_hash: str | None = None
    evidence: str | None = None


class EvidenceDoc(BaseModel):
    """Source text document anchored by SHA-256 hash."""

    model_config = ConfigDict(extra="ignore")

    hash: str
    path: str | None = None
    content: str | None = None
    ingested_at: str | None = None


class TraversalConfig(BaseModel):
    """Explicit parameters controlling causal graph traversal."""

    max_hops: int = Field(default=3, ge=0)
    conf_gate: float = Field(default=0.6, ge=0.0, le=1.0)
    allow_weak_expand: bool = False
    reverse_enabled: bool = True
    allowed_reverse_types: tuple[str, ...] = (
        "cause",
        "trigger",
        "supersede",
        "evolve",
        "resolve",
    )


class TraversedEdge(BaseModel):
    """Edge visited during causal BFS traversal."""

    edge_id: str
    from_event: str
    to_event: str
    type: str | None = None
    strength: str | None = None
    rationale: str | None = None
    confidence: float | None = None
    source_hash: str | None = None
    evidence: str | None = None
    direction: Literal["forward", "backward"]
    expandable: bool
    hop_depth: int = Field(ge=1)
    from_evidence_ref: str | None = None
    to_evidence_ref: str | None = None


class TraversalResult(BaseModel):
    """Result of causal BFS from a starting event."""

    ok: bool
    start_event: str
    max_hops: int
    events: list[Event] = Field(default_factory=list)
    edges: list[TraversedEdge] = Field(default_factory=list)
    skipped_type_reverse: int = 0
    error: str | None = None


class TimelineEdge(BaseModel):
    edge_id: str
    direction: Literal["in", "out"]
    from_event: str
    to_event: str
    type: str | None = None
    rationale: str | None = None
    confidence: float | None = None


class TimelineEvent(BaseModel):
    event: Event
    edges: list[TimelineEdge] = Field(default_factory=list)


class TimelineResult(BaseModel):
    ok: bool = True
    subject: str | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    events: list[TimelineEvent] = Field(default_factory=list)


class FallbackClassSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    definition: str = ""
    statement: str = ""
    statement_unknown: str | None = None
    threshold: float | None = None
    metric: Literal["max_confidence", "min_confidence"] = "max_confidence"
    strengths: list[str] = Field(default_factory=lambda: ["weak", "pending"])
    min_hops: int = 2
    requires_env: str | None = None


class FallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 2
    locked_at: str | None = None
    status: str = "locked"
    precedence: list[Literal["no_chain", "low_confidence", "weak_only"]]
    classes: dict[Literal["no_chain", "low_confidence", "weak_only"], FallbackClassSpec]


class DegradationMetrics(BaseModel):
    n_edges: int
    n_events: int
    n_expandable: int
    max_confidence: float | None = None
    min_confidence: float | None = None
    n_unknown_confidence: int = 0
    all_weak: bool = False
    hops: int = 0


class DegradationAssessment(BaseModel):
    degraded: bool
    degradation_class: DegradationClass
    matched_classes: list[DegradationClass] = Field(default_factory=list)
    statement: str = ""
    metrics: DegradationMetrics
    warnings: list[str] = Field(default_factory=list)


class RetrievalHit(BaseModel):
    event: Event
    score: float
    fts_rank: int | None = None
    vec_rank: int | None = None
    lexical_rank: int | None = None
    date_distance_days: int | None = None


class ExtractedEventItem(BaseModel):
    """Structured event output requested from an extraction LLM."""

    event_id: str | None = None
    ts: str = "unknown"
    type: EventType
    subject: str
    action: str
    object: str
    reason: str | None = None
    before: str | None = None
    after: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    trust_level: TrustLevel = TrustLevel.HIGH


class ExtractedEventsBatch(BaseModel):
    events: list[ExtractedEventItem] = Field(default_factory=list)


class CandidatePair(BaseModel):
    pair_no: int
    from_event: str
    to_event: str
    channels: list[str] = Field(default_factory=list)
    score: float = 0.0


class JudgedEdgeItem(BaseModel):
    """Structured causal edge judgment output from an LLM."""

    edge_id: str | None = None
    from_event: str
    to_event: str
    type: EdgeType
    strength: EdgeStrength
    rationale: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class JudgedEdgesBatch(BaseModel):
    edges: list[JudgedEdgeItem] = Field(default_factory=list)


class GoldAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cause: str = ""
    effect: str = ""
    evidence_hint: str = ""


class Question(BaseModel):
    model_config = ConfigDict(extra="ignore")

    qid: str
    question: str
    type: str | None = None
    gold_answer: GoldAnswer = Field(default_factory=GoldAnswer)


class QAAnswer(BaseModel):
    qid: str
    question: str
    answer_text: str
    evidence_chain: list[str] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    degradation: DegradationAssessment | None = None
    latency_ms: int = -1
    pipeline_ok: bool = True
    prompt_sha256: str = ""
    trace: str = ""


class PreparedQA(BaseModel):
    qid: str
    question: str
    prompt: str
    prompt_sha256: str
    anchors: list[Event] = Field(default_factory=list)
    chain: list[TraversedEdge] = Field(default_factory=list)
    degradation: DegradationAssessment | None = None
    neighbors: list[tuple[int, Event]] = Field(default_factory=list)


class RunMeta(BaseModel):
    schema_version: str = "jvs-run-meta/2"
    kind: str = "qa-run"
    status: str = "completed"
    started_at: str
    finished_at: str | None = None
    db_path: str
    db_sha256: str | None = None
    questions_path: str
    questions_sha256: str | None = None
    provider: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    requested: int = 0
    sendable: int = 0
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    records_ok: int = 0
    written_this_run: int = 0
    failed_qids: list[str] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)
    resume: dict[str, Any] = Field(default_factory=dict)


class JudgeVerdict(BaseModel):
    cause: bool
    effect: bool
    evidence_hint: bool
    available: bool
    rationale: str = ""


class WilsonCI(BaseModel):
    lower: float
    upper: float


class EvalItemResult(BaseModel):
    qid: str
    available: bool
    cause: bool
    effect: bool
    evidence_hint: bool
    causal_all_correct: bool
    evidence_score: float = 0.0
    format_ok: bool = True
    qualified: bool = False
    rationale: str = ""
    error: str | None = None


class EvalSummary(BaseModel):
    judge_channel: Literal["rule", "llm"]
    rubric_sha256: str | None = None
    negation_rule: bool = True
    cause_relax: bool = False
    total_questions: int
    available_count: int
    causal_correct_count: int
    evidence_complete_count: int
    qualified_count: int
    availability_rate: float
    causal_accuracy: float
    evidence_completeness: float
    qualified_rate_available: float
    qualified_rate_all: float
    causal_accuracy_ci: WilsonCI
    items: list[EvalItemResult] = Field(default_factory=list)


class ReadoutBatchStats(BaseModel):
    batch: str
    runs: int
    total_questions: int
    majority_causal: int
    majority_causal_rate: float
    majority_causal_ci: WilsonCI
    majority_evidence: int
    majority_evidence_rate: float
    consistent_count: int
    all_true_count: int
    all_false_count: int
    flipped_qids: list[str] = Field(default_factory=list)
    tied_qids: list[str] = Field(default_factory=list)


class ReadoutSummary(BaseModel):
    ok: bool
    batches: list[ReadoutBatchStats] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
