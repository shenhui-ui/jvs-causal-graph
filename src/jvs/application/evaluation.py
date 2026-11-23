"""Evaluation suite: rule evaluator, structured LLM judge with file lock, and readout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jvs.domain.models import (
    EvalItemResult,
    EvalSummary,
    GoldAnswer,
    JudgeVerdict,
    QAAnswer,
    Question,
    ReadoutBatchStats,
    ReadoutSummary,
    WilsonCI,
)
from jvs.domain.ports import LLMClientPort

try:
    import msvcrt
except ImportError:  # pragma: no cover — POSIX
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

NEGATION_RULE = (
    "- cause（**否定优先**）：当被评答案**显式否定**标准答案的任一核心断言"
    "（如 gold 说「属于 A」，答案说「属于 B **而非 A**」）时，**不得**因其他子原因命中而判 true ⇒ "
    "该要素判 false。\n"
    "  仅当被否定的是非核心细节（修饰、举例、粒度）时，本条不适用。\n"
)

CAUSE_RELAX_RULE = (
    "- cause(宽松补充)：当标准答案的原因本身是笼统背景"
    "（如'来自某群聊/某类调整'、无明显实质动因）时，"
    "被评答案给出的原因若是一个真实存在、时间早于结果事件、"
    "且与该结果同属一条事实链的实质前置事件/决策"
    "（即便与标准答案措辞完全不同），也判 true。\n"
)

_RUN_FILE_RE = re.compile(r"^judge-result\.([A-Za-z]+)(\d+)\.md$")
_TABLE_ROW_RE = re.compile(
    r"^\|\s*(Q[A-Za-z0-9_-]+)\s*\|\s*(true|false)\s*\|\s*(true|false)\s*\|\s*(true|false)\s*\|"
)


class OutputBusyError(RuntimeError):
    """Raised when another judge process holds the exclusive output lock."""


def wilson_ci(k: int, n: int, z: float = 1.959963984540054) -> WilsonCI:
    """Compute Wilson score 95% confidence interval for a binomial proportion."""
    if n <= 0:
        return WilsonCI(lower=0.0, upper=0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2.0 * n)
    margin = z * ((p * (1.0 - p) / n + z * z / (4.0 * n * n)) ** 0.5)
    return WilsonCI(
        lower=round(max(0.0, (center - margin) / denom), 6),
        upper=round(min(1.0, (center + margin) / denom), 6),
    )


def rubric_text(*, negation_rule: bool = True, cause_relax: bool = False) -> str:
    """Return the exact evaluation rubric text defining the judge's scoring contract."""
    parts = [
        "你是评测判分器。根据【标准答案】对【被评答案】做三要素语义判定。\n判定规则：\n",
        "- cause：标准答案可能含多个子原因——被评答案命中其中任何一个实质性原因"
        "即判 true（允许措辞不同）。\n",
    ]
    if negation_rule:
        parts.append(NEGATION_RULE)
    if cause_relax:
        parts.append(CAUSE_RELAX_RULE)
    parts.append(
        "- effect：命中核心结果/后续动作即 true（允许措辞不同、粒度略粗，不要求覆盖全部细节）。\n"
        "- evidence_hint：答案引用了与标准答案指向同一事件、或同一事实链中的相关真实事件"
        "（时间+主体+对象对得上）即 true；完全没引用证据、或引用了无关事件才判 false。\n"
        "- 编造事实（标准答案中没有的实质性内容）才算 false；表达保守/信息量少不扣 true。\n"
        "- 无法确认时判 false。\n"
    )
    return "".join(parts)


def rubric_sha256(*, negation_rule: bool = True, cause_relax: bool = False) -> str:
    text = rubric_text(negation_rule=negation_rule, cause_relax=cause_relax)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_for_match(text: str) -> str:
    return re.sub(r"[\s，。！？、；：（）()【】「」『』.,;:!?\"'/-]+", "", text or "").casefold()


def _coverage_score(gold_text: str, answer_text: str) -> float:
    """Symmetric non-trivial coverage score (prevents reverse-substring false passes)."""
    g = _normalize_for_match(gold_text)
    a = _normalize_for_match(answer_text)
    if not g:
        return 1.0
    if not a or len(a) < 4:
        return 0.0
    if len(g) < 3:
        return 1.0 if g in a else 0.0
    g_grams = {g[i : i + 2] for i in range(len(g) - 1)}
    a_grams = {a[i : i + 2] for i in range(len(a) - 1)}
    if not g_grams:
        return 0.0
    return len(g_grams & a_grams) / len(g_grams)


def _is_available(ans: QAAnswer, timeout_ms: int) -> bool:
    if not ans.pipeline_ok:
        return False
    txt = (ans.answer_text or "").strip()
    if not txt or txt.startswith("(未找到"):
        return False
    if ans.latency_ms == -1:
        return True
    return not (ans.latency_ms < -1 or ans.latency_ms > timeout_ms)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


@contextmanager
def acquire_output_lock(out_path: Path):
    """Acquire an exclusive OS-level lock keyed by normalized output path."""
    norm_key = os.path.normcase(str(out_path.resolve()))
    digest = hashlib.sha256(norm_key.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"jvs-eval-{digest}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            if msvcrt is not None:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover
                raise OSError("No file locking primitive available")
        except OSError as exc:
            raise OutputBusyError(f"输出文件正被其他进程占用: {out_path}") from exc
        yield lock_path
    finally:
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


class RuleEvaluator:
    """Deterministic M1 rule evaluator computing availability, accuracy, and Wilson CI."""

    def evaluate(
        self,
        answers: Sequence[QAAnswer],
        gold_by_qid: Mapping[str, GoldAnswer],
        *,
        match_threshold: float = 0.45,
        timeout_ms: int = 30000,
    ) -> EvalSummary:
        items: list[EvalItemResult] = []
        for ans in answers:
            gold = gold_by_qid.get(ans.qid)
            if gold is None or not (gold.cause or gold.effect or gold.evidence_hint):
                continue

            avail = _is_available(ans, timeout_ms)
            ev_has_src = any("[src:" in item for item in ans.evidence_chain) or (
                "[src:" in ans.answer_text
            )
            evidence_score = 1.0 if ev_has_src else 0.0
            format_ok = (
                "原因：" in ans.answer_text
                and "结果：" in ans.answer_text
                and "证据：" in ans.answer_text
            )
            if not avail:
                items.append(
                    EvalItemResult(
                        qid=ans.qid,
                        available=False,
                        cause=False,
                        effect=False,
                        evidence_hint=False,
                        causal_all_correct=False,
                        evidence_score=evidence_score,
                        format_ok=format_ok,
                        qualified=False,
                        rationale="不可用（管线失败/空回答/超时）",
                    )
                )
                continue

            cause_ok = _coverage_score(gold.cause, ans.answer_text) >= match_threshold
            effect_ok = _coverage_score(gold.effect, ans.answer_text) >= match_threshold
            hint_ok = (
                _coverage_score(gold.evidence_hint, ans.answer_text) >= match_threshold
                or ev_has_src
            )
            all_ok = cause_ok and effect_ok and hint_ok
            qualified = all_ok and evidence_score >= 1.0 and format_ok

            items.append(
                EvalItemResult(
                    qid=ans.qid,
                    available=True,
                    cause=cause_ok,
                    effect=effect_ok,
                    evidence_hint=hint_ok,
                    causal_all_correct=all_ok,
                    evidence_score=evidence_score,
                    format_ok=format_ok,
                    qualified=qualified,
                )
            )

        return _summarize_items(
            items,
            judge_channel="rule",
            rubric_sha=None,
            negation_rule=True,
            cause_relax=False,
        )


class LLMJudge:
    """Structured LLM evaluator with single-source rubric, file lock, and atomic checkpoints."""

    def __init__(
        self,
        llm: LLMClientPort,
        *,
        negation_rule: bool = True,
        cause_relax: bool = False,
    ) -> None:
        self.llm = llm
        self.negation_rule = negation_rule
        self.cause_relax = cause_relax
        self.rubric = rubric_text(negation_rule=negation_rule, cause_relax=cause_relax)
        self.rubric_sha = rubric_sha256(
            negation_rule=negation_rule, cause_relax=cause_relax
        )

    def input_fingerprint(self, question: str, gold: GoldAnswer, answer_text: str) -> str:
        payload = json.dumps(
            {
                "rubric_sha256": self.rubric_sha,
                "question": question,
                "cause": gold.cause,
                "effect": gold.effect,
                "evidence_hint": gold.evidence_hint,
                "answer_text": answer_text,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def build_messages(
        self,
        question: str,
        gold: GoldAnswer,
        answer_text: str,
    ) -> list[dict[str, str]]:
        prompt = self.rubric + (
            f"问题：{question}\n"
            f"标准答案-cause：{gold.cause}\n"
            f"标准答案-effect：{gold.effect}\n"
            f"标准答案-evidence_hint：{gold.evidence_hint}\n"
            f"被评答案：\n{answer_text}\n"
            '只输出 JSON 对象：{"cause": true/false, "effect": true/false, '
            '"evidence_hint": true/false, "available": true/false, "rationale": "…"}\n'
            "available = 被评答案确实在回答该问题（非空话、非失败信息、非拒答）。\n"
            "rationale = 不超过200字说明判定依据，不得引入外部知识。"
        )
        return [{"role": "user", "content": prompt}]

    def evaluate(
        self,
        answers: Sequence[QAAnswer],
        gold_by_qid: Mapping[str, GoldAnswer],
        *,
        out_path: Path | None = None,
        timeout_ms: int = 30000,
    ) -> EvalSummary:
        """Judge answers via structured LLM calls, locking and checkpointing `out_path`."""

        def _run() -> EvalSummary:
            cached: dict[str, dict[str, Any]] = {}
            if out_path is not None and out_path.is_file():
                try:
                    raw = json.loads(out_path.read_text(encoding="utf-8"))
                    for item in raw.get("items") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("qid")
                            and not item.get("error")
                            and item.get("input_fingerprint")
                        ):
                            cached[str(item["qid"])] = item
                except (OSError, ValueError):
                    cached = {}

            results: list[EvalItemResult] = []
            checkpoint_items: list[dict[str, Any]] = []

            for ans in answers:
                gold = gold_by_qid.get(ans.qid)
                if gold is None or not (gold.cause or gold.effect or gold.evidence_hint):
                    continue

                ev_has_src = any("[src:" in x for x in ans.evidence_chain) or (
                    "[src:" in ans.answer_text
                )
                evidence_score = 1.0 if ev_has_src else 0.0
                format_ok = (
                    "原因：" in ans.answer_text
                    and "结果：" in ans.answer_text
                    and "证据：" in ans.answer_text
                )

                if not _is_available(ans, timeout_ms):
                    res = EvalItemResult(
                        qid=ans.qid,
                        available=False,
                        cause=False,
                        effect=False,
                        evidence_hint=False,
                        causal_all_correct=False,
                        evidence_score=evidence_score,
                        format_ok=format_ok,
                        qualified=False,
                        rationale="不可用（管线失败/空回答/超时）",
                    )
                    results.append(res)
                    checkpoint_items.append(res.model_dump())
                    continue

                fp = self.input_fingerprint(ans.question, gold, ans.answer_text)
                hit = cached.get(ans.qid)
                if hit is not None and hit.get("input_fingerprint") == fp:
                    res = EvalItemResult.model_validate(hit)
                    results.append(res)
                    checkpoint_items.append(hit)
                    continue

                verdict = self.llm.complete_structured(
                    self.build_messages(ans.question, gold, ans.answer_text),
                    JudgeVerdict,
                )
                all_ok = verdict.cause and verdict.effect and verdict.evidence_hint
                avail = verdict.available
                qualified = avail and all_ok and evidence_score >= 1.0 and format_ok
                res = EvalItemResult(
                    qid=ans.qid,
                    available=avail,
                    cause=verdict.cause,
                    effect=verdict.effect,
                    evidence_hint=verdict.evidence_hint,
                    causal_all_correct=all_ok,
                    evidence_score=evidence_score,
                    format_ok=format_ok,
                    qualified=qualified,
                    rationale=verdict.rationale[:600],
                )
                results.append(res)
                record = res.model_dump()
                record["input_fingerprint"] = fp
                checkpoint_items.append(record)

                if out_path is not None:
                    interim = _summarize_items(
                        results,
                        judge_channel="llm",
                        rubric_sha=self.rubric_sha,
                        negation_rule=self.negation_rule,
                        cause_relax=self.cause_relax,
                    )
                    payload = interim.model_dump()
                    payload["items"] = checkpoint_items
                    payload["updated_at"] = datetime.now(UTC).isoformat()
                    _write_json_atomic(out_path, payload)

            summary = _summarize_items(
                results,
                judge_channel="llm",
                rubric_sha=self.rubric_sha,
                negation_rule=self.negation_rule,
                cause_relax=self.cause_relax,
            )
            if out_path is not None:
                payload = summary.model_dump()
                payload["items"] = checkpoint_items
                payload["updated_at"] = datetime.now(UTC).isoformat()
                _write_json_atomic(out_path, payload)
            return summary

        if out_path is None:
            return _run()
        with acquire_output_lock(out_path):
            return _run()


def _summarize_items(
    items: Sequence[EvalItemResult],
    *,
    judge_channel: str,
    rubric_sha: str | None,
    negation_rule: bool,
    cause_relax: bool,
) -> EvalSummary:
    total = len(items)
    avail = sum(1 for x in items if x.available)
    causal_ok = sum(1 for x in items if x.available and x.causal_all_correct)
    ev_complete = sum(1 for x in items if x.available and x.evidence_score >= 1.0)
    qualified = sum(1 for x in items if x.qualified)
    return EvalSummary(
        judge_channel=judge_channel,  # type: ignore[arg-type]
        rubric_sha256=rubric_sha,
        negation_rule=negation_rule,
        cause_relax=cause_relax,
        total_questions=total,
        available_count=avail,
        causal_correct_count=causal_ok,
        evidence_complete_count=ev_complete,
        qualified_count=qualified,
        availability_rate=round(avail / total, 4) if total else 0.0,
        causal_accuracy=round(causal_ok / total, 4) if total else 0.0,
        evidence_completeness=round(ev_complete / total, 4) if total else 0.0,
        qualified_rate_available=round(qualified / avail, 4) if avail else 0.0,
        qualified_rate_all=round(qualified / total, 4) if total else 0.0,
        causal_accuracy_ci=wilson_ci(causal_ok, total),
        items=list(items),
    )


def load_answers_and_gold(
    answers_path: Path,
    questions_or_gold_path: Path,
) -> tuple[list[QAAnswer], dict[str, GoldAnswer]]:
    """Load answers JSONL and align gold answers from either gold.jsonl or questions.jsonl."""
    if not answers_path.is_file():
        raise FileNotFoundError(f"答案文件不存在: {answers_path}")
    if not questions_or_gold_path.is_file():
        raise FileNotFoundError(f"标准答案文件不存在: {questions_or_gold_path}")

    answers: list[QAAnswer] = []
    with answers_path.open("r", encoding="utf-8-sig") as fh:
        for line in fh:
            s = line.strip()
            if s:
                answers.append(QAAnswer.model_validate_json(s))

    gold_map: dict[str, GoldAnswer] = {}
    with questions_or_gold_path.open("r", encoding="utf-8-sig") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            qid = str(obj["qid"])
            if "gold_answer" in obj and isinstance(obj["gold_answer"], dict):
                q = Question.model_validate(obj)
                gold_map[qid] = q.gold_answer
            else:
                gold_map[qid] = GoldAnswer.model_validate(obj)

    return answers, gold_map


def _parse_judge_markdown_table(path: Path) -> tuple[dict[str, tuple[bool, bool, bool]], int]:
    rows: dict[str, tuple[bool, bool, bool]] = {}
    unparsed = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s.startswith("|"):
            continue
        if s.startswith("|---") or s.startswith("| ---") or "qid" in s.lower():
            continue
        m = _TABLE_ROW_RE.match(s)
        if not m:
            unparsed += 1
            continue
        qid = m.group(1)
        rows[qid] = (
            m.group(2) == "true",
            m.group(3) == "true",
            m.group(4) == "true",
        )
    return rows, unparsed


def compute_readout(
    result_files: Sequence[Path],
    *,
    expected_qids: Sequence[str] | None = None,
) -> ReadoutSummary:
    """Compute strict multi-run majority readout with tie rejection and Wilson CI."""
    errors: list[str] = []
    by_batch: dict[str, list[tuple[int, Path, dict[str, tuple[bool, bool, bool]]]]] = {}

    for path in sorted(result_files):
        m = _RUN_FILE_RE.match(path.name)
        if not m:
            errors.append(f"非法判分表文件名（须为 judge-result.<批次><跑号>.md）: {path.name}")
            continue
        batch, run_no = m.group(1), int(m.group(2))
        rows, unparsed = _parse_judge_markdown_table(path)
        if unparsed > 0:
            errors.append(f"{path.name}: 存在 {unparsed} 行无法解析的表格行")
        if not rows:
            errors.append(f"{path.name}: 未解析到任何有效题目行")
            continue
        by_batch.setdefault(batch, []).append((run_no, path, rows))

    if not by_batch and not errors:
        errors.append("未提供任何判分结果表")

    batch_stats: list[ReadoutBatchStats] = []
    expected_set = set(expected_qids) if expected_qids is not None else None

    for batch, runs in sorted(by_batch.items()):
        run_nums = [r[0] for r in runs]
        if len(run_nums) != len(set(run_nums)):
            errors.append(f"批次 {batch} 存在重复跑号: {run_nums}")
            continue

        qid_sets = [set(r[2].keys()) for r in runs]
        base_qids = qid_sets[0]
        if any(qs != base_qids for qs in qid_sets[1:]):
            errors.append(f"批次 {batch} 各跑题号集合不一致")
            continue

        if expected_set is not None and base_qids != expected_set:
            missing = sorted(expected_set - base_qids)
            extra = sorted(base_qids - expected_set)
            errors.append(f"批次 {batch} 题号与分母锚不符 (missing={missing}, extra={extra})")
            continue

        n_runs = len(runs)
        n_q = len(base_qids)
        maj_causal = 0
        maj_ev = 0
        all_true = 0
        all_false = 0
        flipped: list[str] = []
        tied: list[str] = []

        for qid in sorted(base_qids):
            causal_votes = [all(r[2][qid]) for r in runs]
            ev_votes = [r[2][qid][2] for r in runs]
            c_true = sum(1 for v in causal_votes if v)
            e_true = sum(1 for v in ev_votes if v)

            if c_true * 2 == n_runs:
                tied.append(qid)
            elif c_true * 2 > n_runs:
                maj_causal += 1

            if e_true * 2 > n_runs:
                maj_ev += 1

            if c_true == n_runs:
                all_true += 1
            elif c_true == 0:
                all_false += 1
            else:
                flipped.append(qid)

        if tied:
            errors.append(f"批次 {batch} 存在平票题目（无严格多数）: {tied}")

        batch_stats.append(
            ReadoutBatchStats(
                batch=batch,
                runs=n_runs,
                total_questions=n_q,
                majority_causal=maj_causal,
                majority_causal_rate=round(maj_causal / n_q, 4) if n_q else 0.0,
                majority_causal_ci=wilson_ci(maj_causal, n_q),
                majority_evidence=maj_ev,
                majority_evidence_rate=round(maj_ev / n_q, 4) if n_q else 0.0,
                consistent_count=all_true + all_false,
                all_true_count=all_true,
                all_false_count=all_false,
                flipped_qids=flipped,
                tied_qids=tied,
            )
        )

    return ReadoutSummary(ok=len(errors) == 0, batches=batch_stats, errors=errors)
