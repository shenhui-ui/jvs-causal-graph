# JVS v2.0 Architecture

## Layered Design

`src/jvs/` follows strict unidirectional dependencies:

```text
cli (Typer) / mcp (MCPServer)
          │
          ▼
   application (CausalEngine, QAService, Extraction, Evaluation)
          │
          ├──────────────────────────┐
          ▼                          ▼
  domain (pure Pydantic models,   infrastructure (SQLite+FTS5+sqlite-vec,
          rules, 2-state BFS,                     HybridRetriever, OpenAILLMClient,
          NetworkX analytics,                     PrivacyGate)
          Protocol ports)
```

- **`domain/`**: Zero SQLite, zero HTTP, zero `os.environ`, zero hardcoded host paths.
- **`infrastructure/`**: Implements storage, retrieval, LLM calls, and fail-closed privacy checks.
- **`application/`**: Orchestrates end-to-end workflows without duplicating domain rules or storage SQL.

---

## Core Mechanisms

### 1. Confidence Gate & Two-State Causal Traversal

1. **Ingestion Gate (`domain/rules.py`)**:
   - Every incoming `CausalEdge` is evaluated via `normalize_edge_strength(edge, conf_gate=0.6)`.
   - Unparseable, `NaN`, `Inf`, out-of-range, or `<= 0.6` edges are **relabeled `pending`** (not deleted) so they remain available as 1-hop clues while being excluded from multi-hop propagation.
2. **Two-State BFS (`domain/traversal.py`)**:
   - `trace_causal_chain()` replaces multiple duplicated BFS loops from v1 with a single indexed implementation.
   - `clue_seen` (nodes displayed as 1-hop clues) is separated from `expanded` (nodes queued for further hops), ensuring a weak/pending clue never blocks an alternate strong path to the same node.
3. **M1-11 Fallback Classifier**:
   - `assess_degradation()` evaluates `TraversalResult` against `assets/fallback.yaml` (`no_chain`, `low_confidence`, `weak_only`) and emits calibrated degradation statements.

### 2. Hybrid Retrieval (`infrastructure/retrieval/hybrid.py`)

`HybridRetriever` combines three signals using **Reciprocal Rank Fusion (RRF)**:
- **SQLite FTS5 Trigram BM25** (`events_fts`, `tokenize='trigram'`) for fast CJK and substring matching;
- **SQLite Vector Search** (`sqlite-vec` `vec0`) when embeddings are configured and indexed;
- **Lexical + 2-gram + Date Proximity Fallback** for short (`<3` char) Chinese queries and read-only legacy SQLite databases that predate `events_fts`.

### 3. Fail-Closed Outbound Privacy Gate (`infrastructure/privacy/gate.py`)

Every outbound call in `OpenAILLMClient`, `OpenAIEmbeddingClient`, and `QAService` preflight passes through `PrivacyGate.assert_outbound()`:
- Normalizes text with Unicode **NFKC + `casefold()`** to prevent full-width or casing bypasses;
- Blocks configured personal names/aliases (respecting structured non-person flags while refusing to strip humans via loose note substrings);
- Blocks API keys (`sk-`, `ghp_`, `AKIA`, `AIza`, `Bearer`, private keys), emails, URLs, Chinese mobile/ID numbers, and Feishu OpenIDs;
- Fails closed whenever `require_names=True` and the vocabulary file is missing or empty.

### 4. Reproducible QA & Evaluation (`application/qa.py`, `application/evaluation.py`)

- **Preflight Gate**: `QAService.run_batch()` scans all rendered prompts *before* issuing any LLM call.
- **Resume Identity**: `run-meta.json` (`jvs-run-meta/2`) tracks per-question prompt SHA-256, model ID, and `max_tokens`. Resumes reject mismatched identities rather than silently mixing incompatible measurements.
- **Locked Structured Judging**: `LLMJudge` acquires an OS-level lock (`msvcrt` / `fcntl`), ties scores to `rubric_sha256()`, and checkpoints atomically.
- **Strict Readout**: `compute_readout()` requires strict majority (`true * 2 > runs`), fails explicitly on tied questions, and reports Wilson 95% confidence intervals.
