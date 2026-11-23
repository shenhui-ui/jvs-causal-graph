"""Hybrid retriever combining FTS5 trigram BM25, sqlite-vec cosine, and temporal proximity."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from jvs.domain.models import Event, RetrievalHit
from jvs.domain.ports import EmbeddingPort
from jvs.infrastructure.storage.sqlite_store import SQLiteGraphStore

_PREFERRED_TYPES = frozenset(("change", "decision", "bugfix"))
_STOPWORDS = frozenset(("为什么", "在", "把", "的", "处理", "方式", "从", "改成", "了", "改", "成"))


def _drop_digit_grams(items: Sequence[str]) -> list[str]:
    return [x for x in items if x and not str(x).isdigit()]


def extract_question_date(question: str) -> str | None:
    """Extract a baseline ISO date from question text without leaking corpus metadata."""
    q = question or ""
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", q)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(20\d{2})-(\d{2})", q)
    if m:
        return f"{m.group(1)}-{m.group(2)}-15"
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{1,2})\s*月", q)
    if m:
        return f"2026-{int(m.group(1)):02d}-15"
    return None


def date_distance_days(event: Event, qdate: str | None) -> int | None:
    """Return absolute day distance between event timestamp and question date."""
    if not qdate:
        return None
    ts = str(event.ts or "")[:10]
    if len(ts) != 10:
        return None
    try:
        d_ev = datetime.strptime(ts, "%Y-%m-%d").date()
        d_q = datetime.strptime(qdate, "%Y-%m-%d").date()
        return abs((d_ev - d_q).days)
    except ValueError:
        return None


def extract_keywords(question: str) -> list[str]:
    """Extract quoted phrases, sliding CJK/alnum n-grams, and clean tokens."""
    q = question or ""
    kws: list[str] = []
    for pat in (r"「([^」]{2,30})」", r"【([^】]{2,30})】", r"『([^』]{2,30})』"):
        kws.extend(re.findall(pat, q))

    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", q)
    n = len(compact)
    seen = set(kws)
    for window in (7, 6, 5, 4):
        for i in range(max(n - window + 1, 0)):
            gram = compact[i : i + window]
            if gram not in seen:
                seen.add(gram)
                kws.append(gram)
            if len(kws) >= 70:
                break
        if len(kws) >= 70:
            break

    body = re.sub(r"[「」【】『』（）()，。？！、:：；;，,]", " ", q)
    for tok in body.split():
        if 2 <= len(tok) <= 16 and tok not in _STOPWORDS and tok not in seen:
            seen.add(tok)
            kws.append(tok)
    return _drop_digit_grams(kws)[:60]


class HybridRetriever:
    """Multi-channel Reciprocal Rank Fusion (RRF) retriever over SQLiteGraphStore."""

    def __init__(
        self,
        store: SQLiteGraphStore,
        *,
        embedding_client: EmbeddingPort | None = None,
        min_bigram: int = 6,
        rrf_k: int = 60,
    ) -> None:
        self.store = store
        self.embedding_client = embedding_client
        self.min_bigram = min_bigram
        self.rrf_k = rrf_k

    def _lexical_rank(
        self,
        query: str,
        events: Sequence[Event],
        *,
        limit: int,
    ) -> list[tuple[Event, tuple[int, int, int, int]]]:
        """Score events using keyword hits + non-digit character 2-grams + type/date bias."""
        kws = extract_keywords(query)
        q_clean = query or ""
        qdate = extract_question_date(q_clean)
        grams = {
            q_clean[i : i + 2]
            for i in range(max(len(q_clean) - 1, 0))
            if q_clean[i] != " " and q_clean[i + 1] != " "
        }
        grams_list = _drop_digit_grams(sorted(grams))
        short_mode = len(q_clean.strip()) <= 4

        scored: list[tuple[tuple[int, int, int, int], Event]] = []
        for ev in events:
            txt = ev.search_text()
            kw_hits = sum(1 for kw in kws if kw and kw in txt)
            bg_hits = sum(1 for g in grams_list if g in txt)
            if kw_hits > 0 or bg_hits >= self.min_bigram or (short_mode and bg_hits >= 1):
                dist = date_distance_days(ev, qdate)
                pref = 1 if (ev.type or "") in _PREFERRED_TYPES else 0
                prox = -dist if dist is not None else -999999
                key = (kw_hits, 1 if dist is not None else 0, prox, bg_hits + pref)
                scored.append((key, ev))

        if not scored and q_clean.strip():
            # Last-resort relaxed 2-gram fallback (>= 2 hits)
            for ev in events:
                txt = ev.search_text()
                bg_hits = sum(1 for g in grams_list if g in txt)
                if bg_hits >= 2:
                    scored.append(((0, 0, 0, bg_hits), ev))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [(ev, key) for key, ev in scored[:limit]]

    def retrieve(self, query: str, k: int = 5) -> list[RetrievalHit]:
        """Retrieve top-k causal anchor events using RRF over FTS5, vector, and lexical channels."""
        if k <= 0 or not query.strip():
            return []

        pool_limit = max(k * 5, 25)
        fts_hits = self.store.search_fts(query, limit=pool_limit)

        vec_hits: list[tuple[Event, float]] = []
        if self.embedding_client is not None:
            stats = self.store.stats()
            if stats.get("vec_indexed"):
                vectors = self.embedding_client.embed([query])
                if vectors:
                    vec_hits = self.store.search_vector(vectors[0], limit=pool_limit)

        all_events = self.store.list_events()
        lex_hits = self._lexical_rank(query, all_events, limit=pool_limit)

        qdate = extract_question_date(query)
        events_by_id: dict[str, Event] = {}
        fts_rank_map: dict[str, int] = {}
        vec_rank_map: dict[str, int] = {}
        lex_rank_map: dict[str, int] = {}
        scores: dict[str, float] = {}

        for rank, (ev, _) in enumerate(fts_hits, start=1):
            events_by_id[ev.id] = ev
            fts_rank_map[ev.id] = rank
            scores[ev.id] = scores.get(ev.id, 0.0) + 1.2 / (self.rrf_k + rank)

        for rank, (ev, _) in enumerate(vec_hits, start=1):
            events_by_id[ev.id] = ev
            vec_rank_map[ev.id] = rank
            scores[ev.id] = scores.get(ev.id, 0.0) + 1.1 / (self.rrf_k + rank)

        for rank, (ev, _) in enumerate(lex_hits, start=1):
            events_by_id[ev.id] = ev
            lex_rank_map[ev.id] = rank
            scores[ev.id] = scores.get(ev.id, 0.0) + 1.0 / (self.rrf_k + rank)

        # Mild temporal proximity and causal-pivot type tie-breaker
        for eid, ev in events_by_id.items():
            dist = date_distance_days(ev, qdate)
            if dist is not None:
                scores[eid] += 0.005 / (1.0 + dist)
            if (ev.type or "") in _PREFERRED_TYPES:
                scores[eid] += 0.001

        ranked_ids = sorted(scores, key=lambda eid: scores[eid], reverse=True)[:k]
        results: list[RetrievalHit] = []
        for eid in ranked_ids:
            ev = events_by_id[eid]
            results.append(
                RetrievalHit(
                    event=ev,
                    score=round(scores[eid], 6),
                    fts_rank=fts_rank_map.get(eid),
                    vec_rank=vec_rank_map.get(eid),
                    lexical_rank=lex_rank_map.get(eid),
                    date_distance_days=date_distance_days(ev, qdate),
                )
            )
        return results

    def temporal_neighbors(
        self,
        anchor: Event,
        *,
        days: int = 12,
        limit: int = 5,
    ) -> list[tuple[int, Event]]:
        """Find events within +-days of `anchor` strictly inside the same corpus/source scope."""
        ts = str(anchor.ts or "")[:10]
        try:
            base = datetime.strptime(ts, "%Y-%m-%d").date()
        except ValueError:
            return []

        anchor_scope = anchor.corpus or anchor.source_hash
        if not anchor_scope:
            return []

        candidates: list[tuple[int, int, Event]] = []
        for ev in self.store.list_events():
            if ev.id == anchor.id:
                continue
            ev_scope = ev.corpus or ev.source_hash
            if not ev_scope or ev_scope != anchor_scope:
                continue
            try:
                ev_date = datetime.strptime(str(ev.ts or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            delta = (ev_date - base).days
            if abs(delta) <= days:
                candidates.append((abs(delta), delta, ev))

        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        return [(delta, ev) for _, delta, ev in candidates[:limit]]
