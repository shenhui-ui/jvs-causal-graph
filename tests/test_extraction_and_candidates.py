"""Tests for event extraction, candidate pairing, edge judging, and OpenAILLMClient."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.conftest import FakeLLMClient

from jvs.application.engine import CausalEngine
from jvs.application.extraction import (
    extract_events_from_text,
    generate_candidate_pairs,
    judge_candidate_edges,
)
from jvs.config import JVSSettings, ResolvedLLMConfig
from jvs.domain.models import (
    EdgeStrength,
    EdgeType,
    ExtractedEventItem,
    ExtractedEventsBatch,
    JudgedEdgeItem,
    JudgedEdgesBatch,
)
from jvs.infrastructure.llm.client import OpenAILLMClient
from jvs.infrastructure.privacy.gate import PrivacyBlocked, PrivacyGate


def test_extract_events_and_judge_edges_end_to_end(tmp_path: Path, assets_dir: Path):
    text = "2026-08-20 压测超时；2026-08-21 因为压测超时，决定扩容连接池。"
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    fake_llm = FakeLLMClient(
        structured_responses=[
            ExtractedEventsBatch(
                events=[
                    ExtractedEventItem(
                        ts="2026-08-20",
                        type="meeting",
                        subject="连接池",
                        action="发现",
                        object="压测超时",
                        confidence=0.95,
                    ),
                    ExtractedEventItem(
                        ts="2026-08-21",
                        type="decision",
                        subject="连接池",
                        action="决定",
                        object="扩容连接池",
                        reason="压测超时",
                        confidence=0.95,
                    ),
                ]
            ),
            JudgedEdgesBatch(
                edges=[
                    JudgedEdgeItem(
                        from_event="E001",
                        to_event="E002",
                        type=EdgeType.CAUSE,
                        strength=EdgeStrength.STRONG,
                        rationale="压测超时导致扩容连接池",
                        evidence="因为压测超时，决定扩容连接池",
                        confidence=0.92,
                    ),
                    JudgedEdgeItem(
                        # Reversed order (E002 -> E001) must be filtered out!
                        from_event="E002",
                        to_event="E001",
                        type=EdgeType.CAUSE,
                        strength=EdgeStrength.STRONG,
                        rationale="无效时间逆序",
                        evidence="invalid",
                        confidence=0.99,
                    ),
                ]
            ),
        ]
    )

    events = extract_events_from_text(
        text,
        fake_llm,
        prompt_path=assets_dir / "prompts" / "event_extract_v2.md",
        corpus="压测讨论",
    )
    assert len(events) == 2
    assert all(ev.source_hash == expected_hash for ev in events)
    assert events[0].id == "E001"

    pairs = generate_candidate_pairs(events, window_days=7)
    assert len(pairs) == 1
    assert pairs[0].from_event == "E001" and pairs[0].to_event == "E002"
    assert "adjacent" in pairs[0].channels and "coref" in pairs[0].channels

    edges = judge_candidate_edges(
        pairs,
        {e.id: e for e in events},
        fake_llm,
        prompt_path=assets_dir / "prompts" / "causal_edges_v2.1.md",
    )
    assert len(edges) == 1
    assert edges[0].from_event == "E001"
    assert edges[0].to_event == "E002"
    assert edges[0].id == "A001"

    # Full engine ingest_text round-trip
    db = tmp_path / "ingest.db"
    fake_llm2 = FakeLLMClient(
        structured_responses=[
            ExtractedEventsBatch(
                events=[
                    ExtractedEventItem(
                        ts="2026-08-22",
                        type="change",
                        subject="缓存策略",
                        action="调整",
                        object="TTL",
                        before="60s",
                        after="300s",
                        confidence=0.9,
                    )
                ]
            ),
        ]
    )
    with CausalEngine(
        db,
        settings=JVSSettings(db_path=db, assets_dir=assets_dir),
        llm_client=fake_llm2,
    ) as engine:
        summary = engine.ingest_text("2026-08-22 缓存 TTL 从 60s 改成 300s", corpus="群3")
        assert summary["extracted_events"] == 1
        assert engine.stats()["events"] == 1


def test_openai_llm_client_privacy_gate_and_json_repair(sample_names_file: Path):
    calls: list[dict] = []

    class _MockCompletions:
        def __init__(self, responses):
            self._responses = responses

        def create(self, **kwargs):
            calls.append(kwargs)
            content = self._responses.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    gate = PrivacyGate(names_path=sample_names_file, require_names=True)
    mock_sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_MockCompletions(
                [
                    "not valid json",
                    '```json\n{"events": []}\n```',
                ]
            )
        )
    )
    conf = ResolvedLLMConfig(
        provider="openai",
        base_url="https://api.example.invalid/v1",
        api_key="dummy-key",
        model="gpt-4o-mini",
        max_tokens=256,
        temperature=0.0,
        timeout_s=10.0,
        max_retries=0,
    )
    client = OpenAILLMClient(conf, privacy_gate=gate, openai_client=mock_sdk)

    # Privacy violation blocks BEFORE SDK is ever called
    with pytest.raises(PrivacyBlocked):
        client.complete_text([{"role": "user", "content": "张三丰说了什么"}])
    assert len(calls) == 0

    # Malformed first response triggers clean JSON repair call
    parsed = client.complete_structured(
        [{"role": "user", "content": "extract events"}],
        ExtractedEventsBatch,
    )
    assert parsed.events == []
    assert len(calls) == 2
