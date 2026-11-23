"""Shared offline pytest fixtures for JVS v2.0."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from jvs.config import JVSSettings
from jvs.domain.models import CausalEdge, Event
from jvs.infrastructure.privacy.gate import PrivacyGate
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore

TModel = TypeVar("TModel", bound=BaseModel)


class FakeLLMClient:
    """Offline mock implementing LLMClientPort with PrivacyGate enforcement."""

    def __init__(
        self,
        *,
        text_responses: list[str] | None = None,
        structured_responses: list[BaseModel | dict[str, Any]] | None = None,
        privacy_gate: PrivacyGate | None = None,
        model_name: str = "fake-model",
    ) -> None:
        self._text_responses = list(text_responses or [])
        self._structured_responses = list(structured_responses or [])
        self.privacy_gate = privacy_gate
        self._model_name = model_name
        self.calls: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def _check_privacy(self, messages: list[dict[str, str]], where: str) -> None:
        if self.privacy_gate is not None:
            self.privacy_gate.assert_outbound(
                json.dumps(messages, ensure_ascii=False), where=where
            )

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        self._check_privacy(messages, "fake/text")
        self.calls.append({"kind": "text", "messages": messages})
        if self._text_responses:
            return self._text_responses.pop(0)
        return "原因：压测吞吐不足。\n结果：连接池从20调整为60。\n证据：E_DEFAULT"

    def complete_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> TModel:
        self._check_privacy(messages, "fake/structured")
        self.calls.append({"kind": "structured", "messages": messages, "model": response_model})
        if self._structured_responses:
            item = self._structured_responses.pop(0)
            if isinstance(item, response_model):
                return item
            return response_model.model_validate(item)
        return response_model.model_validate({})


class FakeEmbeddingClient:
    """Deterministic 8-D character-hash embedding generator for offline sqlite-vec tests."""

    def __init__(self, dims: int = 8) -> None:
        self.dims = dims

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for txt in texts:
            vec = [0.0] * self.dims
            for ch in txt:
                idx = int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16) % self.dims
                vec[idx] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


@pytest.fixture()
def assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets"


@pytest.fixture()
def sample_names_file(tmp_path: Path) -> Path:
    payload = {
        "identities": [
            {"canonical_name": "张三丰", "aliases": ["三丰哥"], "note": "待人物归一确认"},
            {"canonical_name": "AliceTester", "aliases": ["AlTest"]},
            {"canonical_name": "SystemBot", "is_person": False},
            {"canonical_name": "未映射Z99", "aliases": []},
            {"canonical_name": "HelperService", "entity_kind": "bot"},
            {"canonical_name": "GhostID", "note": "排除出人物归一"},
        ]
    }
    p = tmp_path / "names.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture()
def sample_graph_db(tmp_path: Path, assets_dir: Path) -> Path:
    """Create a rich synthetic causal graph database testing traversal, gating, and search."""
    db = tmp_path / "causal.db"
    events = [
        Event(
            id="E1",
            ts="2026-08-20",
            type="meeting",
            subject="连接池配置",
            action="压测发现",
            object="连接池吞吐不足",
            after="P99 超时",
            confidence=0.95,
            source_hash="a" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#aaaaaaaaaaaa",
        ),
        Event(
            id="E2",
            ts="2026-08-21",
            type="decision",
            subject="连接池配置",
            action="拍板",
            object="扩容连接池",
            before="20",
            after="60",
            confidence=0.92,
            source_hash="b" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#bbbbbbbbbbbb",
        ),
        Event(
            id="E3",
            ts="2026-08-20",
            type="change",
            subject="连接池配置",
            action="提交",
            object="压测复核报告",
            after="确认连接等待",
            confidence=0.90,
            source_hash="c" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#cccccccccccc",
        ),
        Event(
            id="E4",
            ts="2026-08-22",
            type="change",
            subject="连接池配置",
            action="上线",
            object="连接池参数生效",
            before="20",
            after="60",
            confidence=0.95,
            source_hash="d" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#dddddddddddd",
        ),
        Event(
            id="E5",
            ts="2026-08-25",
            type="assign",
            subject="工位安排",
            action="指派",
            object="新工位",
            confidence=0.90,
            source_hash="e" * 64,
            corpus="群聊2",
            evidence_ref="群聊2#eeeeeeeeeeee",
        ),
        Event(
            id="E6",
            ts="2026-08-26",
            type="bugfix",
            subject="支付超时重试",
            action="修复",
            object="主动查单补偿",
            before="失败重试3次",
            after="主动查单补偿",
            confidence=0.91,
            source_hash="f" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#ffffffffffff",
        ),
        Event(
            id="E7",
            ts="2026-08-27",
            type="schedule",
            subject="支付超时重试",
            action="排期",
            object="灰度发布",
            confidence=0.85,
            source_hash="1" * 64,
            corpus="群聊1",
            evidence_ref="群聊1#111111111111",
        ),
    ]
    # Note: E1--weak-->E2 plus E1--strong-->E3--strong-->E2--strong-->E4
    # tests the two-state BFS invariant (weak clue does not block strong expansion of E2).
    edges = [
        CausalEdge(
            id="A1",
            from_event="E1",
            to_event="E2",
            type="evolve",
            strength="weak",
            rationale="弱线索",
            confidence=0.75,
        ),
        CausalEdge(
            id="A2",
            from_event="E1",
            to_event="E3",
            type="trigger",
            strength="strong",
            rationale="压测触发复核",
            confidence=0.90,
        ),
        CausalEdge(
            id="A3",
            from_event="E3",
            to_event="E2",
            type="cause",
            strength="strong",
            rationale="因为确认连接等待所以拍板扩容",
            confidence=0.92,
        ),
        CausalEdge(
            id="A4",
            from_event="E2",
            to_event="E4",
            type="supersede",
            strength="strong",
            rationale="上线落实扩容决定",
            confidence=0.95,
        ),
        CausalEdge(
            id="A5",
            from_event="E6",
            to_event="E7",
            type="trigger",
            strength="strong",
            rationale="低置信测试边",
            confidence=0.50,  # gated to pending!
        ),
    ]
    with SQLiteGraphStore(db) as store:
        store.init_db()
        store.ingest_events(events)
        store.ingest_edges(edges, conf_gate=0.6, apply_gate=True)
    return db


@pytest.fixture()
def sample_settings(sample_graph_db: Path, assets_dir: Path) -> JVSSettings:
    return JVSSettings(db_path=sample_graph_db, assets_dir=assets_dir)
