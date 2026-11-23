"""Tests for QA orchestration, preflight gating, resume identity, and promptset diffing."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import FakeLLMClient

from jvs.application.engine import CausalEngine
from jvs.application.qa import (
    EXIT_INPUT_OR_REFUSED,
    EXIT_OK,
    EXIT_PARTIAL_OR_DIFF,
    EXIT_RESUME_CONFLICT,
    InputValidationError,
    QAService,
    render_qa_prompt,
)
from jvs.config import JVSSettings
from jvs.domain.models import Event, Question, TraversedEdge
from jvs.infrastructure.privacy.gate import PrivacyGate
from jvs.infrastructure.retrieval.hybrid import HybridRetriever
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore


def test_render_qa_prompt_includes_pending_and_origin_guidance():
    e1 = Event(
        id="E1", ts="2026-08-20", type="change", subject="网关", action="改", object="超时"
    )
    e2 = Event(
        id="E2",
        ts="2026-08-21",
        type="decision",
        subject="网关",
        action="定",
        object="限流",
        reason="防止下游雪崩",
    )
    edge = TraversedEdge(
        edge_id="A1",
        from_event="E1",
        to_event="E2",
        type="trigger",
        strength="pending",
        rationale="低置信线索",
        confidence=0.5,
        direction="forward",
        expandable=False,
        hop_depth=1,
    )
    prompt = render_qa_prompt(
        Question(qid="Q1", question="为什么改限流", type="为什么改"),
        e2,
        [edge],
        {"E1": e1, "E2": e2},
    )
    assert "reason:防止下游雪崩" in prompt
    assert "[E2]" in prompt
    assert "pending/弱（低置信,仅线索）" in prompt
    assert "严格按以下三行格式输出" in prompt


def test_qa_batch_preflight_blocks_and_resume_identity(
    tmp_path: Path,
    sample_graph_db: Path,
    sample_names_file: Path,
    assets_dir: Path,
):
    q_file = tmp_path / "questions.jsonl"
    q_file.write_text(
        json.dumps(
            {
                "qid": "Q001",
                "question": "为什么扩容连接池？",
                "gold_answer": {"cause": "压测超时", "effect": "扩容", "evidence_hint": "E2"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "qid": "Q002",
                "question": "张三丰为什么调整配置？",
                "gold_answer": {"cause": "x", "effect": "y", "evidence_hint": "z"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    gate = PrivacyGate(names_path=sample_names_file, require_names=True)
    llm = FakeLLMClient(privacy_gate=gate)
    out_dir = tmp_path / "out"
    settings = JVSSettings(db_path=sample_graph_db, assets_dir=assets_dir)

    with CausalEngine(
        sample_graph_db, settings=settings, read_only=True, llm_client=llm, privacy_gate=gate
    ) as engine:
        meta, rc = engine.run_qa_batch(q_file, out_dir, use_llm=True)
        assert rc == EXIT_INPUT_OR_REFUSED
        assert meta.status == "refused_privacy"
        assert len(meta.blocked) == 1 and meta.blocked[0]["qid"] == "Q002"
        assert len(llm.calls) == 0  # preflight blocked BEFORE any LLM call!
        assert not (out_dir / "answers.jsonl").exists()

    # Now run clean single-question batch and verify resume conflict detection
    q_clean = tmp_path / "questions_clean.jsonl"
    q_clean.write_text(
        json.dumps(
            {
                "qid": "Q001",
                "question": "为什么扩容连接池？",
                "gold_answer": {"cause": "压测超时", "effect": "扩容", "evidence_hint": "E2"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with CausalEngine(
        sample_graph_db, settings=settings, read_only=True, llm_client=llm, privacy_gate=gate
    ) as engine:
        meta1, rc1 = engine.run_qa_batch(q_clean, out_dir, use_llm=True)
        assert rc1 == EXIT_OK
        assert meta1.written_this_run == 1

        # Re-run with same identity -> reuses without new LLM calls
        calls_before = len(llm.calls)
        meta2, rc2 = engine.run_qa_batch(q_clean, out_dir, use_llm=True)
        assert rc2 == EXIT_OK
        assert meta2.resume["reused"] == 1
        assert len(llm.calls) == calls_before

        # Corrupt trailing line in answers.jsonl -> recovers without crashing
        with (out_dir / "answers.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("{broken_json_truncated\n")
        meta_rec, rc_rec = engine.run_qa_batch(q_clean, out_dir, use_llm=True)
        assert rc_rec == EXIT_OK
        assert meta_rec.resume["broken_lines_skipped"] == 1

    # Different model identity -> refuses resume conflict without deleting answers.jsonl
    llm_other = FakeLLMClient(privacy_gate=gate, model_name="other-model")
    with CausalEngine(
        sample_graph_db, settings=settings, read_only=True, llm_client=llm_other, privacy_gate=gate
    ) as engine:
        meta3, rc3 = engine.run_qa_batch(q_clean, out_dir, use_llm=True)
        assert rc3 == EXIT_RESUME_CONFLICT
        assert meta3.status == "refused_resume_conflict"
        assert (out_dir / "answers.jsonl").is_file()


def test_promptset_export_and_tamper_detection(
    tmp_path: Path,
    sample_graph_db: Path,
    assets_dir: Path,
):
    q_file = tmp_path / "qs.jsonl"
    q_file.write_text(
        json.dumps({"qid": "Q001", "question": "为什么扩容连接池？"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    settings = JVSSettings(db_path=sample_graph_db, assets_dir=assets_dir)
    base_dir = tmp_path / "base"
    new_dir = tmp_path / "new"

    with SQLiteGraphStore(sample_graph_db, read_only=True) as store:
        svc = QAService(store, HybridRetriever(store), settings=settings)
        res1 = svc.export_promptset(q_file, base_dir)
        assert res1["total"] == 1
        assert (base_dir / "prompts" / "Q001.md").is_file()

        res2 = svc.export_promptset(q_file, new_dir, baseline_dir=base_dir)
        assert res2["diff"]["different"] is False
        assert res2["diff"]["same"] == 1

        # Same-length tamper in baseline prompt file must be caught via recomputed SHA256!
        p_file = base_dir / "prompts" / "Q001.md"
        orig = p_file.read_text(encoding="utf-8")
        p_file.write_text(orig.replace("问题", "题问", 1), encoding="utf-8")
        assert len(p_file.read_text(encoding="utf-8")) == len(orig)
        try:
            svc.export_promptset(q_file, tmp_path / "new2", baseline_dir=base_dir)
            raise AssertionError("Expected InputValidationError on tampered baseline")
        except InputValidationError:
            pass


def test_qa_offline_ask_and_exit_partial_on_missing_anchor(
    tmp_path: Path,
    sample_graph_db: Path,
    assets_dir: Path,
):
    q_file = tmp_path / "qs.jsonl"
    q_file.write_text(
        json.dumps({"qid": "Q001", "question": "为什么扩容连接池？"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"qid": "Q999", "question": "完全不存在的话题XYZ"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    settings = JVSSettings(db_path=sample_graph_db, assets_dir=assets_dir)
    with CausalEngine(sample_graph_db, settings=settings, read_only=True) as engine:
        meta, rc = engine.run_qa_batch(q_file, tmp_path / "off_out", use_llm=False)
        assert rc == EXIT_PARTIAL_OR_DIFF
        assert meta.records_ok == 1
        assert meta.failed_qids == ["Q999"]
