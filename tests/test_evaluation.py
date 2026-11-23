"""Tests for RuleEvaluator, LLMJudge locking/rubric identity, and strict Readout."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import FakeLLMClient

from jvs.application.evaluation import (
    LLMJudge,
    OutputBusyError,
    RuleEvaluator,
    acquire_output_lock,
    compute_readout,
    rubric_sha256,
    wilson_ci,
)
from jvs.domain.models import GoldAnswer, JudgeVerdict, QAAnswer


def test_rule_evaluator_and_wilson_ci():
    ci = wilson_ci(28, 30)
    assert 0.77 < ci.lower < 0.80
    assert 0.97 < ci.upper < 0.99

    gold = {
        "Q1": GoldAnswer(cause="压测吞吐不足", effect="扩容连接池到60", evidence_hint="E2"),
        "Q2": GoldAnswer(cause="超时问题", effect="主动查单补偿", evidence_hint="E6"),
        "Q3": GoldAnswer(cause="原因A", effect="结果B", evidence_hint="E9"),
    }
    answers = [
        QAAnswer(
            qid="Q1",
            question="为什么扩容",
            answer_text=(
                "原因：压测吞吐不足。\n结果：扩容连接池到60。\n"
                "证据：E2 [src: g#1234567890ab]"
            ),
            evidence_chain=["[src: g#1234567890ab]"],
            pipeline_ok=True,
        ),
        # Reverse-substring guard: tiny answer "因" must NOT pass!
        QAAnswer(
            qid="Q2",
            question="为什么改",
            answer_text="因",
            pipeline_ok=True,
        ),
        # Timeout latency > 30000ms must mark unavailable!
        QAAnswer(
            qid="Q3",
            question="为什么",
            answer_text="原因：原因A\n结果：结果B\n证据：E9",
            latency_ms=45000,
            pipeline_ok=True,
        ),
    ]
    summary = RuleEvaluator().evaluate(answers, gold)
    assert summary.total_questions == 3
    assert summary.available_count == 2
    assert summary.causal_correct_count == 1
    assert summary.qualified_count == 1


def test_llm_judge_rubric_identity_checkpoint_and_lock(tmp_path: Path):
    sha_default = rubric_sha256(negation_rule=True, cause_relax=False)
    sha_no_neg = rubric_sha256(negation_rule=False, cause_relax=False)
    sha_relax = rubric_sha256(negation_rule=True, cause_relax=True)
    assert len({sha_default, sha_no_neg, sha_relax}) == 3

    out_path = tmp_path / "scores.json"
    llm = FakeLLMClient(
        structured_responses=[
            JudgeVerdict(
                cause=True,
                effect=True,
                evidence_hint=True,
                available=True,
                rationale="三要素全部命中",
            )
        ]
    )
    judge = LLMJudge(llm, negation_rule=True, cause_relax=False)
    gold = {"Q1": GoldAnswer(cause="压测超时", effect="扩容", evidence_hint="E2")}
    answers = [
        QAAnswer(
            qid="Q1",
            question="为什么扩容",
            answer_text="原因：压测超时\n结果：扩容\n证据：[src: g#1234567890ab]",
            evidence_chain=["[src: g#1234567890ab]"],
            pipeline_ok=True,
        )
    ]
    summary = judge.evaluate(answers, gold, out_path=out_path)
    assert summary.causal_correct_count == 1
    assert summary.rubric_sha256 == sha_default
    assert out_path.is_file()

    # Re-evaluating with same rubric & input reuses checkpoint (0 new LLM calls)
    summary2 = judge.evaluate(answers, gold, out_path=out_path)
    assert summary2.causal_correct_count == 1
    assert len(llm.calls) == 1

    # Output lock blocks concurrent writer on the same path
    with acquire_output_lock(out_path), pytest.raises(OutputBusyError):
        judge.evaluate(answers, gold, out_path=out_path)


def test_compute_readout_majority_and_tie_rejection(tmp_path: Path):
    r1 = tmp_path / "judge-result.e1.md"
    r2 = tmp_path / "judge-result.e2.md"
    r3 = tmp_path / "judge-result.e3.md"
    r4 = tmp_path / "judge-result.e4.md"

    r1.write_text(
        "| qid | cause | effect | evidence |\n|---|---|---|---|\n"
        "| Q001 | true | true | true |\n| Q002 | false | false | false |\n",
        encoding="utf-8",
    )
    r2.write_text(
        "| qid | cause | effect | evidence |\n|---|---|---|---|\n"
        "| Q001 | true | true | true |\n| Q002 | true | true | true |\n",
        encoding="utf-8",
    )
    r3.write_text(
        "| qid | cause | effect | evidence |\n|---|---|---|---|\n"
        "| Q001 | true | true | true |\n| Q002 | false | false | false |\n",
        encoding="utf-8",
    )
    summary = compute_readout([r1, r2, r3], expected_qids=["Q001", "Q002"])
    assert summary.ok is True
    assert summary.batches[0].majority_causal == 1
    assert summary.batches[0].all_true_count == 1
    assert summary.batches[0].flipped_qids == ["Q002"]

    # Adding a 4th run that creates a 2:2 tie on Q002 MUST fail readout!
    r4.write_text(
        "| qid | cause | effect | evidence |\n|---|---|---|---|\n"
        "| Q001 | true | true | true |\n| Q002 | true | true | true |\n",
        encoding="utf-8",
    )
    tied_summary = compute_readout([r1, r2, r3, r4], expected_qids=["Q001", "Q002"])
    assert tied_summary.ok is False
    assert tied_summary.batches[0].tied_qids == ["Q002"]
