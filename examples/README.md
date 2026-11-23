# JVS 1-Minute Demo

All files in `examples/` are synthetic, sanitized sample data.

## Offline Walkthrough (No API Key Required)

```bash
# 1. Create and populate a local SQLite causal graph
jvs graph init --db demo.db
jvs graph ingest --db demo.db --events examples/events.jsonl --edges examples/edges.jsonl

# 2. Inspect stats (notice AX4 with confidence 0.50 was gated to pending)
jvs graph stats --db demo.db

# 3. Search and trace multi-hop causal chains
jvs graph search "连接池扩容" --db demo.db --k 3
jvs graph why EX1 --db demo.db --max-hops 3
jvs graph why EX6 --db demo.db   # triggers M1-11 no_chain fallback

# 4. Run offline QA & rule evaluation
jvs qa ask "为什么把数据库连接池从20扩容到60？" --db demo.db --offline
jvs qa run --db demo.db --questions examples/questions.jsonl --out demo_out --offline
jvs eval score --answers demo_out/answers.jsonl --questions demo_out/gold.jsonl
```

## With an LLM API Key

Set `JVS_LLM_PROVIDER` and `JVS_LLM_API_KEY` in `.env`, then ingest raw Markdown directly:

```bash
jvs graph ingest-text --db demo_llm.db --file examples/sample_notes.md --corpus demo_log
jvs qa ask "为什么把数据库连接池从20扩容到60？" --db demo_llm.db
```
