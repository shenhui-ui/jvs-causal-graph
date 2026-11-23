"""Causal QA preparation, prompt rendering, batch runner, and promptset diffing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jvs.config import JVSSettings
from jvs.domain.models import (
    DegradationAssessment,
    DegradationClass,
    Event,
    FallbackPolicy,
    PreparedQA,
    QAAnswer,
    Question,
    RunMeta,
    TraversalConfig,
    TraversalResult,
    TraversedEdge,
)
from jvs.domain.ports import LLMClientPort
from jvs.domain.rules import assess_degradation, load_fallback_policy
from jvs.domain.traversal import trace_causal_chain
from jvs.infrastructure.privacy.gate import PrivacyBlocked, PrivacyGate
from jvs.infrastructure.retrieval.hybrid import HybridRetriever
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT_OR_REFUSED = 2
EXIT_PARTIAL_OR_DIFF = 3
EXIT_RESUME_CONFLICT = 4


class InputValidationError(ValueError):
    """Raised on malformed question sets, missing DBs, or untrusted promptset baselines."""


class ResumeConflictError(RuntimeError):
    """Raised when existing run outputs do not match the current batch identity."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def load_questions_jsonl(path: Path) -> list[Question]:
    """Read and strictly validate a JSONL question file."""
    if not path.is_file():
        raise InputValidationError(f"题集不存在: {path}")
    questions: list[Question] = []
    seen_qids: dict[str, int] = {}
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise InputValidationError(f"题集第 {lineno} 行不是合法 JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise InputValidationError(f"题集第 {lineno} 行不是 JSON 对象")
            qid = obj.get("qid")
            if not qid:
                raise InputValidationError(f"题集第 {lineno} 行缺 qid")
            if not isinstance(qid, str):
                raise InputValidationError(f"题集第 {lineno} 行 qid 必须是字符串: {qid!r}")
            if qid in seen_qids:
                raise InputValidationError(
                    f"题集第 {lineno} 行 qid 重复: {qid} (已在第 {seen_qids[qid]} 行出现)"
                )
            if not isinstance(obj.get("question"), str) or not obj["question"].strip():
                raise InputValidationError(f"题集第 {lineno} 行缺有效 question 文本")
            seen_qids[qid] = lineno
            questions.append(Question.model_validate(obj))
    if not questions:
        raise InputValidationError(f"题集为空: {path}")
    return questions


def render_qa_prompt(
    question: Question | str,
    anchor: Event | None,
    chain: Sequence[TraversedEdge],
    events_by_id: Mapping[str, Event],
    *,
    extra_anchors: Sequence[Event] = (),
    neighbors: Sequence[tuple[int, Event]] = (),
    settings: JVSSettings | None = None,
) -> str:
    """Pure, deterministic prompt renderer for causal QA."""
    cfg = settings or JVSSettings()
    q_obj = (
        question
        if isinstance(question, Question)
        else Question(qid="Q000", question=str(question))
    )
    lines = [f"问题：{q_obj.question}"]

    def _fmt_event(ev: Event | None, prefix: str = "锚点事件") -> str:
        if ev is None:
            return f"{prefix}：(unknown) None|None|None|None"
        before_s = f" before:{ev.before}" if ev.before is not None else ""
        after_s = f" after:{ev.after}" if ev.after else ""
        reason_s = f" reason:{ev.reason}" if ev.reason else ""
        return (
            f"{prefix}：[{ev.id}] ({ev.ts}) {ev.type}|{ev.subject}|{ev.action}|{ev.object}"
            f"{before_s}{after_s}{reason_s}"
        )

    lines.append(_fmt_event(anchor))
    for extra in extra_anchors:
        if anchor is not None and extra.id == anchor.id:
            continue
        lines.append(_fmt_event(extra, "相关锚点"))

    if q_obj.type == "证据定位" or "定位" in q_obj.question:
        srcs: set[str] = set()
        for ev in ([anchor] if anchor else []) + list(extra_anchors):
            if ev.source_hash:
                srcs.add(f"[src: {ev.corpus or '?'}#{str(ev.source_hash)[:12]}]")
        if srcs:
            lines.append("该变更的出处引用：" + "、".join(sorted(srcs)))
        lines.append(
            "回答提示：本题为「证据定位」，证据行请输出上方给出的出处引用"
            "（形如 [src: 群聊4#xxxxxxxxxxxx]），不要只写图库事件；"
            "若出处引用不可用，再退化到图库事件。"
        )

    if chain:
        lines.append(
            "证据链（含 strong/weak/pending 边；weak、pending 均为弱关联、"
            "仅供参考，不参与多跳）："
        )
        anchor_ids = {ev.id for ev in ([anchor] if anchor else []) + list(extra_anchors)}
        seen_after: set[str] = set()

        def _after_suffix(eid: str) -> str:
            if not cfg.render_chain_after or eid in anchor_ids or eid in seen_after:
                return ""
            ev = events_by_id.get(eid)
            if ev and (ev.after or ev.reason):
                seen_after.add(eid)
                af = f" after:{ev.after}" if ev.after else ""
                rs = f" reason:{ev.reason}" if ev.reason else ""
                return f"{af}{rs}"
            return ""

        for edge in chain:
            fe = events_by_id.get(edge.from_event)
            te = events_by_id.get(edge.to_event)
            strength = edge.strength or "?"
            tag = "/弱" if strength in ("weak", "pending") else ""
            if strength == "pending":
                tag += "（低置信,仅线索）"
            direct = ""
            if cfg.prioritize_direct_edges and (
                edge.from_event in anchor_ids or edge.to_event in anchor_ids
            ):
                direct = "【直接连锚点】"
            f_txt = (
                f"({fe.ts}){fe.subject}{fe.action}{fe.object}"
                if fe
                else f"({edge.from_event})"
            )
            t_txt = (
                f"({te.ts}){te.subject}{te.action}{te.object}"
                if te
                else f"({edge.to_event})"
            )
            lines.append(
                f"- {edge.type}/{strength}{tag}{direct}: "
                f"{f_txt}{_after_suffix(edge.from_event)} → "
                f"{t_txt}{_after_suffix(edge.to_event)}"
                f"｜理由:{edge.rationale or ''}"
            )
    else:
        lines.append("证据链：空（仅锚点事件可用）")

    if neighbors:
        lines.append("时间邻近事件（同语料±12天内，仅背景参考，未必有因果关系）：")
        for delta, ev in neighbors:
            sign = "+" if delta > 0 else ""
            af = f" after:{ev.after}" if ev.after else ""
            rs = f" reason:{ev.reason}" if ev.reason else ""
            lines.append(
                f"- [{sign}{delta}天] [{ev.id}] ({ev.ts}) {ev.type}|{ev.subject}|"
                f"{ev.action}|{ev.object}{af}{rs}"
            )

    if cfg.prompt_cause_guidance:
        lines.append(
            "回答提示：「原因」优先回答直接触发因素（已发生的具体事实/问题/数据），"
            "而非「为了什么」的目的性动机；两者都要说时，直接触发因素放最前。"
        )
    if cfg.prompt_origin_guidance:
        lines.append(
            "回答提示：当问题在问某变更的「出处/来源/为什么这样调整」时，"
            "「原因」除了直接触发因素，还应覆盖该变更所在的背景语境"
            "（该变更属于哪类体系/主题的调整、出自哪个群聊/哪条工作线的讨论、针对哪个上游改动），"
            "并在「证据」中优先引用最能定位该变更来源的事件。"
        )
    if cfg.prompt_evidence_first:
        supplied: dict[str, Event] = {}
        for ev in ([anchor] if anchor else []) + list(extra_anchors):
            supplied[ev.id] = ev
        for edge in chain:
            for eid in (edge.from_event, edge.to_event):
                ev = events_by_id.get(eid)
                if ev is not None:
                    supplied[eid] = ev
        for _, ev in neighbors:
            supplied[ev.id] = ev
        lines.append("已提供事件的编号与出处对照（只定位上述材料，不增加因果关系）：")
        for eid, ev in supplied.items():
            src = (
                f" [src: workspace/events #{str(ev.source_hash)[:12]}]"
                if ev.source_hash
                else ""
            )
            lines.append(f"- {eid} ({ev.ts}) {ev.subject}|{ev.action}|{ev.object}{src}")
        lines.append(
            "先在已提供证据中定位支持结论的真实事件编号和出处，再组织答案。"
            "最终仅输出原因、结果、证据三行。编号不得猜测；不足时说明证据不足。"
        )

    lines.append("请只依据以上信息回答，不编造。严格按以下三行格式输出（每行尽量简短、给出来源时间与编号）：")
    lines.append("原因：<为什么>")
    lines.append("结果：<产生了什么结果/后续动作>")
    lines.append("证据：<引用最关键的事件(时间+主体+对象)，如证据不足请明说>")
    return "\n".join(lines)


def _offline_extractive_answer(
    anchor: Event | None,
    chain: Sequence[TraversedEdge],
    events_by_id: Mapping[str, Event],
    degradation: DegradationAssessment | None,
) -> tuple[str, list[str]]:
    """Build deterministic extractive answer and [src:] citations when LLM is disabled."""
    if anchor is None:
        return "(未找到相关事件，无法回答)", []

    ev_chain: list[str] = []
    if anchor.source_hash:
        ev_chain.append(f"[src: workspace/events #{str(anchor.source_hash)[:12]}]")
    for edge in chain:
        for eid in (edge.from_event, edge.to_event):
            ev = events_by_id.get(eid)
            if ev and ev.source_hash:
                ref = f"[src: workspace/events #{str(ev.source_hash)[:12]}]"
                if ref not in ev_chain:
                    ev_chain.append(ref)

    cause_edge = next((e for e in chain if e.type in ("cause", "trigger")), None)
    if cause_edge and cause_edge.rationale:
        cause_line = cause_edge.rationale
    elif degradation and degradation.degraded and degradation.statement:
        cause_line = degradation.statement
    elif anchor.reason:
        cause_line = anchor.reason
    else:
        cause_line = "证据不足（图谱未发现明确上游原因边）"

    effect_line = (
        f"({anchor.ts}) {anchor.subject or anchor.object}: "
        f"{anchor.action or ''} {anchor.after or anchor.object or ''}"
    ).strip()
    src_text = " ".join(ev_chain[:3]) if ev_chain else f"{anchor.id} ({anchor.ts})"
    if (
        degradation
        and degradation.degraded
        and degradation.degradation_class != DegradationClass.NONE
    ):
        note = f" [降级:{degradation.degradation_class.value}]"
    else:
        note = ""
    text = f"原因：{cause_line}{note}\n结果：{effect_line}\n证据：{src_text}"
    return text, ev_chain


class QAService:
    """Application service orchestrating causal retrieval, prompt rendering, QA, and batch runs."""

    def __init__(
        self,
        store: SQLiteGraphStore,
        retriever: HybridRetriever,
        *,
        settings: JVSSettings | None = None,
        llm: LLMClientPort | None = None,
        privacy_gate: PrivacyGate | None = None,
        fallback_policy: FallbackPolicy | None = None,
    ) -> None:
        self.store = store
        self.retriever = retriever
        self.settings = settings or JVSSettings()
        self.llm = llm
        self.privacy_gate = privacy_gate or PrivacyGate(
            names_path=self.settings.resolve_privacy_names_path(),
            require_names=self.settings.privacy_require_names,
            enabled=self.settings.privacy_enabled,
        )
        if fallback_policy is not None:
            self.fallback_policy = fallback_policy
        else:
            policy_path = self.settings.resolve_assets_dir() / "fallback.yaml"
            self.fallback_policy = load_fallback_policy(policy_path)

    def _traversal_config(self) -> TraversalConfig:
        return TraversalConfig(
            max_hops=self.settings.max_hops,
            conf_gate=self.settings.conf_gate,
            allow_weak_expand=self.settings.allow_weak_expand,
            reverse_enabled=self.settings.reverse_enabled,
            allowed_reverse_types=self.settings.qa_reverse_types,
        )

    def prepare(self, question: Question | str) -> PreparedQA:
        """Retrieve anchors, traverse causal chains, assess fallback, and render prompt."""
        q_obj = (
            question
            if isinstance(question, Question)
            else Question(qid="Q000", question=str(question))
        )
        hits = self.retriever.retrieve(q_obj.question, k=self.settings.anchor_k)
        anchors = [h.event for h in hits]
        primary = anchors[0] if anchors else None

        events_by_id: dict[str, Event] = {ev.id: ev for ev in anchors}
        chain: list[TraversedEdge] = []
        seen_pairs: set[tuple[str, str]] = set()
        tcfg = self._traversal_config()

        primary_traversal: TraversalResult | None = None
        for idx, anchor in enumerate(anchors):
            res = trace_causal_chain(
                anchor.id,
                self.store.get_event,
                self.store.get_incident_edges,
                tcfg,
            )
            if idx == 0:
                primary_traversal = res
            for ev in res.events:
                events_by_id[ev.id] = ev
            for edge in res.edges:
                pair = (edge.from_event, edge.to_event)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    chain.append(edge)

        if self.settings.prioritize_direct_edges and anchors:
            anchor_ids = {a.id for a in anchors}
            chain.sort(
                key=lambda ed: (
                    0 if (ed.from_event in anchor_ids or ed.to_event in anchor_ids) else 1,
                    0 if ed.strength == "strong" else 1,
                    -(ed.confidence or 0.0),
                )
            )

        degradation: DegradationAssessment | None = None
        if primary_traversal is not None and primary_traversal.ok:
            degradation = assess_degradation(primary_traversal, self.fallback_policy)

        neighbors: list[tuple[int, Event]] = []
        if primary is not None and self.settings.include_temporal_neighbors:
            anchor_ids = {a.id for a in anchors}
            neighbors = [
                (d, ev)
                for d, ev in self.retriever.temporal_neighbors(
                    primary, days=self.settings.neighbor_days, limit=5
                )
                if ev.id not in anchor_ids
            ][:4]

        prompt = render_qa_prompt(
            q_obj,
            primary,
            chain,
            events_by_id,
            extra_anchors=anchors[1:],
            neighbors=neighbors,
            settings=self.settings,
        )
        prompt_sha = _sha256_bytes(prompt.encode("utf-8"))
        return PreparedQA(
            qid=q_obj.qid,
            question=q_obj.question,
            prompt=prompt,
            prompt_sha256=prompt_sha,
            anchors=anchors,
            chain=chain,
            degradation=degradation,
            neighbors=neighbors,
        )

    def ask(self, question: Question | str, *, use_llm: bool = True) -> QAAnswer:
        """Answer a single question using the causal graph (LLM or offline extractive mode)."""
        prep = self.prepare(question)
        if not prep.anchors:
            return QAAnswer(
                qid=prep.qid,
                question=prep.question,
                answer_text="(未找到相关事件，无法回答)",
                pipeline_ok=False,
                prompt_sha256=prep.prompt_sha256,
                trace="no_anchors",
            )

        events_by_id: dict[str, Event] = {ev.id: ev for ev in prep.anchors}
        for eid in {
            *(e.from_event for e in prep.chain),
            *(e.to_event for e in prep.chain),
        }:
            if eid not in events_by_id:
                fetched = self.store.get_event(eid)
                if fetched is not None:
                    events_by_id[eid] = fetched

        ev_chain: list[str] = []
        for ev in prep.anchors:
            if ev.source_hash:
                ref = f"[src: workspace/events #{str(ev.source_hash)[:12]}]"
                if ref not in ev_chain:
                    ev_chain.append(ref)
        for edge in prep.chain:
            for eid in (edge.from_event, edge.to_event):
                ev = events_by_id.get(eid)
                if ev and ev.source_hash:
                    ref = f"[src: workspace/events #{str(ev.source_hash)[:12]}]"
                    if ref not in ev_chain:
                        ev_chain.append(ref)

        if not use_llm:
            ans_text, offline_chain = _offline_extractive_answer(
                prep.anchors[0], prep.chain, events_by_id, prep.degradation
            )
            return QAAnswer(
                qid=prep.qid,
                question=prep.question,
                answer_text=ans_text,
                evidence_chain=offline_chain,
                anchors=[a.id for a in prep.anchors],
                degradation=prep.degradation,
                pipeline_ok=True,
                prompt_sha256=prep.prompt_sha256,
                trace=f"mode=offline anchors={len(prep.anchors)} edges={len(prep.chain)}",
            )

        if self.llm is None:
            raise RuntimeError("use_llm=True requires a configured LLM client")

        # Fail-closed privacy check before calling LLM
        self.privacy_gate.assert_outbound(prep.prompt, where=f"qa/ask/{prep.qid}")
        t0 = datetime.now(UTC)
        answer_text = self.llm.complete_text(
            [{"role": "user", "content": prep.prompt}]
        )
        latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        deg_tag = (
            prep.degradation.degradation_class.value
            if prep.degradation
            else DegradationClass.NONE.value
        )
        return QAAnswer(
            qid=prep.qid,
            question=prep.question,
            answer_text=answer_text,
            evidence_chain=ev_chain,
            anchors=[a.id for a in prep.anchors],
            degradation=prep.degradation,
            latency_ms=latency_ms,
            pipeline_ok=bool(answer_text.strip()),
            prompt_sha256=prep.prompt_sha256,
            trace=(
                f"mode=llm model={self.llm.model_name} "
                f"anchors={len(prep.anchors)} edges={len(prep.chain)} deg={deg_tag}"
            ),
        )

    def _preflight(
        self, questions: Sequence[Question]
    ) -> tuple[list[PreparedQA], list[dict[str, Any]]]:
        prepared: list[PreparedQA] = []
        blocked: list[dict[str, Any]] = []
        for q in questions:
            prep = self.prepare(q)
            prepared.append(prep)
            try:
                self.privacy_gate.assert_outbound(prep.prompt, where=f"preflight/{q.qid}")
            except PrivacyBlocked as exc:
                blocked.append(
                    {
                        "qid": q.qid,
                        "reason": str(exc),
                        "prompt_sha256": prep.prompt_sha256,
                    }
                )
        return prepared, blocked

    @staticmethod
    def _inspect_resume(
        answers_path: Path,
        meta_path: Path,
        *,
        expected_prompts: Mapping[str, str],
        expected_model: str,
        expected_max_tokens: int,
    ) -> tuple[dict[str, QAAnswer], int, int, list[dict[str, str]]]:
        reused: dict[str, QAAnswer] = {}
        dropped_failed = 0
        broken_lines = 0
        conflicts: list[dict[str, str]] = []

        if not answers_path.is_file():
            return reused, dropped_failed, broken_lines, conflicts

        prev_prompts: dict[str, str] = {}
        prev_model: str | None = None
        prev_max_tokens: int | None = None
        has_prev_meta = False

        if meta_path.is_file():
            try:
                raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                has_prev_meta = isinstance(raw_meta, dict)
                for p in (raw_meta.get("prompts") or []):
                    if isinstance(p, dict) and p.get("qid") and p.get("prompt_sha256"):
                        prev_prompts[str(p["qid"])] = str(p["prompt_sha256"])
                prev_model = (raw_meta.get("provider") or {}).get("model")
                prev_max_tokens = (raw_meta.get("settings") or {}).get("llm_max_tokens")
            except (OSError, ValueError):
                has_prev_meta = False

        with answers_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                try:
                    raw = json.loads(s)
                    ans = QAAnswer.model_validate(raw)
                except (ValueError, Exception):
                    broken_lines += 1
                    continue
                if not ans.pipeline_ok:
                    dropped_failed += 1
                    continue

                reason: str | None = None
                if not has_prev_meta:
                    reason = "no_previous_meta"
                elif ans.qid not in expected_prompts:
                    reason = "qid_not_in_current_set"
                elif ans.qid not in prev_prompts:
                    reason = "prompt_sha_missing_in_previous_meta"
                elif prev_prompts[ans.qid] != expected_prompts[ans.qid]:
                    reason = "prompt_changed"
                elif prev_model != expected_model:
                    reason = "model_changed"
                elif prev_max_tokens != expected_max_tokens:
                    reason = "max_tokens_changed"

                if reason is not None:
                    conflicts.append({"qid": ans.qid, "reason": reason})
                else:
                    reused[ans.qid] = ans

        return reused, dropped_failed, broken_lines, conflicts

    def run_batch(
        self,
        questions_path: Path,
        out_dir: Path,
        *,
        use_llm: bool = True,
        allow_blocked: bool = False,
        discard_incompatible: bool = False,
        dry_run: bool = False,
    ) -> tuple[RunMeta, int]:
        """Run a batch QA evaluation with preflight privacy gate and identity-checked resume."""
        questions = load_questions_jsonl(questions_path)
        prepared, blocked = self._preflight(questions)
        blocked_qids = {b["qid"] for b in blocked}

        model_id = self.llm.model_name if (use_llm and self.llm) else "offline-extractive"
        expected_prompts = {p.qid: p.prompt_sha256 for p in prepared}

        answers_path = out_dir / "answers.jsonl"
        gold_path = out_dir / "gold.jsonl"
        meta_path = out_dir / "run-meta.json"

        reused, dropped_failed, broken_lines, conflicts = self._inspect_resume(
            answers_path,
            meta_path,
            expected_prompts=expected_prompts,
            expected_model=model_id,
            expected_max_tokens=self.settings.llm_max_tokens,
        )

        prompt_entries = [
            {
                "qid": p.qid,
                "prompt_sha256": p.prompt_sha256,
                "prompt_bytes": len(p.prompt.encode("utf-8")),
                "anchors": len(p.anchors),
                "chain": len(p.chain),
                "gate": "blocked" if p.qid in blocked_qids else "sendable",
            }
            for p in prepared
        ]

        meta = RunMeta(
            started_at=_now_iso(),
            db_path=str(self.store.db_path),
            db_sha256=_sha256_file(self.store.db_path) if not self.store.is_memory else None,
            questions_path=str(questions_path),
            questions_sha256=_sha256_file(questions_path),
            provider={"model": model_id, "mode": "llm" if use_llm else "offline"},
            settings=self.settings.safe_summary(),
            requested=len(questions),
            sendable=len(questions) - len(blocked),
            blocked=blocked,
            prompts=prompt_entries,
            resume={
                "reused": len(reused),
                "discarded_failed": dropped_failed,
                "broken_lines_skipped": broken_lines,
                "discarded_incompatible": len(conflicts) if discard_incompatible else 0,
                "conflict_count": len(conflicts),
                "conflicts": conflicts[:20],
            },
        )

        if blocked and not allow_blocked:
            meta.status = "refused_privacy"
            meta.finished_at = _now_iso()
            return meta, EXIT_INPUT_OR_REFUSED

        if conflicts and not discard_incompatible:
            meta.status = "refused_resume_conflict"
            meta.finished_at = _now_iso()
            return meta, EXIT_RESUME_CONFLICT

        if dry_run:
            meta.status = "dry_run_ok"
            meta.finished_at = _now_iso()
            return meta, EXIT_OK

        out_dir.mkdir(parents=True, exist_ok=True)
        final_answers: list[QAAnswer] = []
        written_this_run = 0
        failed_qids: list[str] = []

        for q in questions:
            if q.qid in blocked_qids:
                failed_qids.append(q.qid)
                continue
            if q.qid in reused and not conflicts:
                final_answers.append(reused[q.qid])
                continue
            if q.qid in reused and discard_incompatible:
                final_answers.append(reused[q.qid])
                continue
            ans = self.ask(q, use_llm=use_llm)
            final_answers.append(ans)
            written_this_run += 1
            if not ans.pipeline_ok:
                failed_qids.append(q.qid)

        with answers_path.open("w", encoding="utf-8") as fh:
            for ans in final_answers:
                fh.write(ans.model_dump_json() + "\n")

        with gold_path.open("w", encoding="utf-8") as fh:
            for q in questions:
                gold_row = {
                    "qid": q.qid,
                    "cause": q.gold_answer.cause,
                    "evidence_hint": q.gold_answer.evidence_hint,
                    "effect": q.gold_answer.effect,
                }
                fh.write(json.dumps(gold_row, ensure_ascii=False) + "\n")

        meta.records_ok = sum(1 for a in final_answers if a.pipeline_ok)
        meta.written_this_run = written_this_run
        meta.failed_qids = failed_qids
        meta.status = "completed" if not failed_qids else "partial"
        meta.finished_at = _now_iso()
        _write_json_atomic(meta_path, meta.model_dump())

        rc = EXIT_OK if not failed_qids else EXIT_PARTIAL_OR_DIFF
        return meta, rc

    def export_promptset(
        self,
        questions_path: Path,
        out_dir: Path,
        *,
        baseline_dir: Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Export offline per-question production prompts and verify/diff optional baseline."""
        questions = load_questions_jsonl(questions_path)
        prompts_dir = out_dir / "prompts"
        meta_path = out_dir / "prompt-meta.json"

        if (prompts_dir.exists() or meta_path.exists()) and not force:
            raise InputValidationError(
                f"输出目录已存在提示词产物（使用 --force 覆盖）: {out_dir}"
            )

        if baseline_dir is not None:
            self._validate_baseline_dir(baseline_dir)

        if force:
            if prompts_dir.exists():
                shutil.rmtree(prompts_dir)
            if meta_path.exists():
                meta_path.unlink()

        prompts_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        total_chars = 0
        rendered_by_qid: dict[str, str] = {}

        for q in questions:
            prep = self.prepare(q)
            rendered_by_qid[q.qid] = prep.prompt
            out_file = prompts_dir / f"{q.qid}.md"
            raw_bytes = prep.prompt.encode("utf-8")
            out_file.write_bytes(raw_bytes)
            total_chars += len(prep.prompt)
            items.append(
                {
                    "qid": q.qid,
                    "file": f"prompts/{q.qid}.md",
                    "sha256": prep.prompt_sha256,
                    "bytes": len(raw_bytes),
                    "chars": len(prep.prompt),
                }
            )

        stats = self.store.stats()
        graph_sha = (
            _sha256_file(self.store.db_path) if not self.store.is_memory else None
        )
        qs_sha = _sha256_file(questions_path)
        meta_payload = {
            "schema": "jvs-promptset/2",
            "generated_at": _now_iso(),
            "total": len(questions),
            "total_chars": total_chars,
            "graph": {
                "path": str(self.store.db_path),
                "sha256": graph_sha,
                "events": stats["events"],
                "edges": stats["edges"],
            },
            "questions": {
                "path": str(questions_path),
                "sha256": qs_sha,
            },
            "settings": self.settings.safe_summary(),
            "prompts": items,
        }
        _write_json_atomic(meta_path, meta_payload)

        diff_report: dict[str, Any] | None = None
        if baseline_dir is not None:
            diff_report = self._diff_promptset(rendered_by_qid, baseline_dir)

        return {
            "total": len(questions),
            "total_chars": total_chars,
            "prompts_dir": str(prompts_dir),
            "meta_path": str(meta_path),
            "graph": meta_payload["graph"],
            "questions": meta_payload["questions"],
            "diff": diff_report,
        }

    @staticmethod
    def _validate_baseline_dir(baseline_dir: Path) -> dict[str, str]:
        """Recompute hashes from baseline prompt files on disk; reject tampered baselines."""
        base_meta_path = baseline_dir / "prompt-meta.json"
        base_prompts_dir = baseline_dir / "prompts"
        if not base_meta_path.is_file() or not base_prompts_dir.is_dir():
            raise InputValidationError(f"基线目录缺少 prompt-meta.json 或 prompts/: {baseline_dir}")
        try:
            base_meta = json.loads(base_meta_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise InputValidationError(f"基线 prompt-meta.json 损坏: {exc}") from exc

        entries = base_meta.get("prompts")
        if not isinstance(entries, list) or not entries:
            raise InputValidationError(f"基线 prompt-meta.json 缺少 prompts 列表: {baseline_dir}")

        baseline_texts: dict[str, str] = {}
        for entry in entries:
            qid = entry.get("qid")
            expected_sha = entry.get("sha256")
            if not qid or not expected_sha:
                raise InputValidationError("基线 prompt-meta.json 条目缺少 qid 或 sha256")
            prompt_file = base_prompts_dir / f"{qid}.md"
            if not prompt_file.is_file():
                raise InputValidationError(f"基线提示词文件缺失: {prompt_file}")
            raw_bytes = prompt_file.read_bytes()
            actual_sha = _sha256_bytes(raw_bytes)
            if actual_sha != expected_sha:
                raise InputValidationError(
                    f"基线文件哈希校验失败（可能被篡改）: {prompt_file}"
                )
            baseline_texts[qid] = raw_bytes.decode("utf-8")
        return baseline_texts

    def _diff_promptset(
        self,
        current_by_qid: Mapping[str, str],
        baseline_dir: Path,
    ) -> dict[str, Any]:
        baseline_texts = self._validate_baseline_dir(baseline_dir)
        cur_qids = set(current_by_qid.keys())
        base_qids = set(baseline_texts.keys())

        same = 0
        changed: list[dict[str, Any]] = []
        for qid in sorted(cur_qids & base_qids):
            if current_by_qid[qid] == baseline_texts[qid]:
                same += 1
            else:
                b_len = len(baseline_texts[qid])
                a_len = len(current_by_qid[qid])
                changed.append(
                    {
                        "qid": qid,
                        "before_chars": b_len,
                        "after_chars": a_len,
                        "delta": a_len - b_len,
                    }
                )
        only_new = sorted(cur_qids - base_qids)
        only_base = sorted(base_qids - cur_qids)
        chars_before = sum(len(v) for v in baseline_texts.values())
        chars_after = sum(len(v) for v in current_by_qid.values())
        different = bool(changed or only_new or only_base)
        return {
            "same": same,
            "changed": changed,
            "only_new": only_new,
            "only_base": only_base,
            "chars_before": chars_before,
            "chars_after": chars_after,
            "different": different,
        }
