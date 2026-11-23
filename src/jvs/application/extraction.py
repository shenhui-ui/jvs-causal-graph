"""Event extraction, multi-channel candidate pairing, and structured causal edge judgment."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

from jvs.domain.models import (
    CandidatePair,
    CausalEdge,
    Event,
    ExtractedEventsBatch,
    JudgedEdgesBatch,
)
from jvs.domain.ports import LLMClientPort
from jvs.domain.rules import is_valid_time_order, normalize_edge_strength


def load_prompt_template(path: Path) -> str:
    """Load the prompt body from a versioned Markdown prompt asset."""
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    raw = path.read_text(encoding="utf-8")
    marker = "## 二、提示词正文"
    idx = raw.find(marker)
    section = raw[idx:] if idx != -1 else raw
    fence = re.search(r"```\s*\n([\s\S]*?)\n```", section)
    if fence:
        return fence.group(1).strip()
    return raw.strip()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_compact(text: str | None) -> str:
    return re.sub(r"[\s（）()【】\[\],。·、:：\-—/\\]", "", text or "").casefold()


def _char_ngrams(text: str | None, n: int = 2) -> set[str]:
    cleaned = _norm_compact(text)
    if not cleaned:
        return set()
    if len(cleaned) < n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _parse_date(ts: str | None) -> date | None:
    if not ts or ts == "unknown":
        return None
    try:
        return datetime.strptime(ts[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_events_from_text(
    text: str,
    llm: LLMClientPort,
    *,
    prompt_path: Path,
    corpus: str | None = None,
    source_hash: str | None = None,
    id_prefix: str = "E",
    start_index: int = 1,
) -> list[Event]:
    """Extract structured temporal events from raw text with deterministic source hashing."""
    if not text.strip():
        return []
    digest = source_hash or sha256_hex(text)
    template = load_prompt_template(prompt_path)
    prompt = template.replace("{text}", text).replace("{text_hash}", digest)
    messages = [
        {
            "role": "system",
            "content": (
                "你是结构化因果事件抽取引擎。请严格遵循给定规则，并只返回一个 JSON 对象："
                '{"events": [...]}。'
            ),
        },
        {"role": "user", "content": prompt},
    ]
    batch = llm.complete_structured(messages, ExtractedEventsBatch)
    evidence_ref = f"{corpus or 'doc'}#{digest[:12]}"

    events: list[Event] = []
    for offset, item in enumerate(batch.events):
        ev_id = f"{id_prefix}{start_index + offset:03d}"
        events.append(
            Event(
                id=ev_id,
                ts=item.ts or "unknown",
                type=item.type,
                subject=item.subject,
                action=item.action,
                object=item.object,
                before=item.before,
                after=item.after,
                reason=item.reason,
                confidence=item.confidence,
                source_hash=digest,
                trust_level=item.trust_level,
                evidence_ref=evidence_ref,
                corpus=corpus,
            )
        )
    return events


def generate_candidate_pairs(
    events: Sequence[Event],
    *,
    window_days: int = 7,
    max_candidates: int = 600,
    min_semantic_jaccard: float = 0.20,
    embeddings: Mapping[str, Sequence[float]] | None = None,
    emb_threshold: float = 0.72,
) -> list[CandidatePair]:
    """Generate time-ordered candidate event pairs across temporal, coref, and semantic channels."""
    ordered = sorted(events, key=lambda ev: (ev.ts == "unknown", ev.ts, ev.id))
    parsed_dates = {ev.id: _parse_date(ev.ts) for ev in ordered}
    subj_grams = {ev.id: _char_ngrams(ev.subject) for ev in ordered}
    obj_grams = {ev.id: _char_ngrams(ev.object) for ev in ordered}
    sem_grams = {
        ev.id: _char_ngrams(f"{ev.after or ''} {ev.object or ''} {ev.reason or ''}")
        for ev in ordered
    }

    pair_channels: dict[tuple[str, str], set[str]] = {}
    pair_scores: dict[tuple[str, str], float] = {}

    n = len(ordered)
    for i in range(n):
        e1 = ordered[i]
        d1 = parsed_dates[e1.id]
        s1_norm = _norm_compact(e1.subject)
        o1_norm = _norm_compact(e1.object)

        for j in range(i + 1, n):
            e2 = ordered[j]
            if e1.id == e2.id:
                continue
            if not is_valid_time_order(e1.ts, e2.ts):
                continue

            d2 = parsed_dates[e2.id]
            key = (e1.id, e2.id)
            channels: set[str] = set()
            score = 0.0

            # 1. Temporal adjacency within the same corpus (or un-scoped)
            same_corpus = (e1.corpus == e2.corpus) if (e1.corpus and e2.corpus) else True
            if same_corpus and d1 is not None and d2 is not None:
                delta_days = (d2 - d1).days
                if 0 <= delta_days <= window_days:
                    channels.add("adjacent")
                    score += 1.0 + (window_days - delta_days) * 0.05

            # 2. Coreference on normalized subject / object
            s2_norm = _norm_compact(e2.subject)
            o2_norm = _norm_compact(e2.object)
            subj_sim = _jaccard(subj_grams[e1.id], subj_grams[e2.id])
            obj_sim = _jaccard(obj_grams[e1.id], obj_grams[e2.id])
            exact_shared = bool(
                (s1_norm and s2_norm and (s1_norm in s2_norm or s2_norm in s1_norm))
                or (o1_norm and o2_norm and (o1_norm in o2_norm or o2_norm in o1_norm))
            )
            if exact_shared or max(subj_sim, obj_sim) >= 0.40:
                channels.add("coref")
                score += 1.5 + max(subj_sim, obj_sim)

            # 3. Lexical semantic overlap or vector cosine similarity
            sem_sim = _jaccard(sem_grams[e1.id], sem_grams[e2.id])
            if sem_sim >= min_semantic_jaccard:
                channels.add("semantic")
                score += 1.0 + sem_sim

            if embeddings and e1.id in embeddings and e2.id in embeddings:
                cos = _cosine_similarity(embeddings[e1.id], embeddings[e2.id])
                if cos >= emb_threshold:
                    channels.add("embedding")
                    score += 1.5 + cos

            if channels:
                pair_channels[key] = channels
                pair_scores[key] = round(score, 4)

    ranked_keys = sorted(
        pair_channels.keys(),
        key=lambda k: (len(pair_channels[k]), pair_scores[k], k[0], k[1]),
        reverse=True,
    )[:max_candidates]

    return [
        CandidatePair(
            pair_no=idx,
            from_event=fe,
            to_event=te,
            channels=sorted(pair_channels[(fe, te)]),
            score=pair_scores[(fe, te)],
        )
        for idx, (fe, te) in enumerate(ranked_keys, start=1)
    ]


def judge_candidate_edges(
    candidates: Sequence[CandidatePair],
    events_by_id: Mapping[str, Event],
    llm: LLMClientPort,
    *,
    prompt_path: Path,
    batch_size: int = 60,
    conf_gate: float = 0.6,
    apply_gate: bool = True,
    id_prefix: str = "A",
) -> list[CausalEdge]:
    """Batch-judge candidate event pairs into validated CausalEdge objects."""
    if not candidates:
        return []
    template = load_prompt_template(prompt_path)
    dedup: dict[tuple[str, str], CausalEdge] = {}

    for start in range(0, len(candidates), batch_size):
        chunk = candidates[start : start + batch_size]
        involved_ids: list[str] = []
        seen_ids: set[str] = set()
        for pair in chunk:
            for eid in (pair.from_event, pair.to_event):
                if eid in events_by_id and eid not in seen_ids:
                    seen_ids.add(eid)
                    involved_ids.append(eid)

        event_payload = [
            {
                "event_id": eid,
                "ts": events_by_id[eid].ts,
                "type": events_by_id[eid].type,
                "subject": events_by_id[eid].subject,
                "original": (
                    f"{events_by_id[eid].action or ''} {events_by_id[eid].object or ''} "
                    f"{events_by_id[eid].after or ''} {events_by_id[eid].reason or ''}"
                ).strip(),
                "source_hash": events_by_id[eid].source_hash,
            }
            for eid in involved_ids
        ]
        pair_hints = [
            {"pair_no": p.pair_no, "from_event": p.from_event, "to_event": p.to_event}
            for p in chunk
        ]
        events_json = json.dumps(
            {"events": event_payload, "candidate_pairs": pair_hints},
            ensure_ascii=False,
            indent=2,
        )
        batch_hash = sha256_hex(events_json)
        prompt = template.replace("{events}", events_json).replace("{text_hash}", batch_hash)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是结构化因果边判定引擎。只判定有证据支持的因果或关联边，并只输出 JSON 对象："
                    '{"edges": [...]}。'
                ),
            },
            {"role": "user", "content": prompt},
        ]
        judged = llm.complete_structured(messages, JudgedEdgesBatch)

        for item in judged.edges:
            if item.from_event == item.to_event:
                continue
            fe = events_by_id.get(item.from_event)
            te = events_by_id.get(item.to_event)
            if fe is None or te is None:
                continue
            if not is_valid_time_order(fe.ts, te.ts):
                continue

            edge = CausalEdge(
                id="tmp",
                from_event=item.from_event,
                to_event=item.to_event,
                type=item.type,
                strength=item.strength,
                rationale=item.rationale,
                confidence=item.confidence,
                source_hash=te.source_hash or fe.source_hash or batch_hash,
                evidence=item.evidence,
            )
            if apply_gate:
                norm_strength, _, _ = normalize_edge_strength(edge, conf_gate=conf_gate)
                edge.strength = norm_strength

            key = (edge.from_event, edge.to_event)
            prev = dedup.get(key)
            if prev is None or (edge.confidence or 0.0) > (prev.confidence or 0.0):
                dedup[key] = edge

    edges: list[CausalEdge] = []
    for idx, edge in enumerate(dedup.values(), start=1):
        edge.id = f"{id_prefix}{idx:03d}"
        edges.append(edge)
    return edges
