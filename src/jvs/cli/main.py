"""Unified Typer CLI entrypoint (`jvs`) covering graph, pipeline, QA, eval, privacy, and MCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from jvs.application.engine import CausalEngine
from jvs.application.evaluation import (
    LLMJudge,
    OutputBusyError,
    RuleEvaluator,
    compute_readout,
    load_answers_and_gold,
)
from jvs.application.extraction import (
    extract_events_from_text,
    generate_candidate_pairs,
    judge_candidate_edges,
)
from jvs.application.qa import (
    EXIT_FAILED,
    EXIT_INPUT_OR_REFUSED,
    EXIT_OK,
    EXIT_PARTIAL_OR_DIFF,
    InputValidationError,
    load_questions_jsonl,
)
from jvs.config import JVSSettings
from jvs.domain.models import CandidatePair, CausalEdge, Event, TraversalConfig
from jvs.infrastructure.llm.client import OpenAILLMClient
from jvs.infrastructure.privacy.gate import PrivacyBlocked, PrivacyGate

app = typer.Typer(
    name="jvs",
    help="JVS — Temporal-Causal Knowledge Graph & Cross-Session Causal QA",
    no_args_is_help=True,
)
graph_app = typer.Typer(help="Causal graph database & traversal commands", no_args_is_help=True)
pipeline_app = typer.Typer(
    help="Extraction, candidate pairing & edge judgment", no_args_is_help=True
)
qa_app = typer.Typer(help="Causal QA & offline promptset commands", no_args_is_help=True)
eval_app = typer.Typer(help="Rule evaluation, LLM judging & readout commands", no_args_is_help=True)
privacy_app = typer.Typer(help="Fail-closed privacy & credential scanning", no_args_is_help=True)
mcp_app = typer.Typer(help="Model Context Protocol (MCP) server", no_args_is_help=True)

app.add_typer(graph_app, name="graph")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(qa_app, name="qa")
app.add_typer(eval_app, name="eval")
app.add_typer(privacy_app, name="privacy")
app.add_typer(mcp_app, name="mcp")

console = Console()
err_console = Console(stderr=True)


def _read_jsonl_models(path: Path, model_cls):
    if not path.is_file():
        raise InputValidationError(f"文件不存在: {path}")
    items = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                items.append(model_cls.model_validate_json(s))
            except Exception as exc:
                raise InputValidationError(f"{path} 第 {lineno} 行解析失败: {exc}") from exc
    return items


def _ensure_db_exists(db: Path) -> None:
    if not db.is_file():
        err_console.print(f"[red]jvs: 图谱不存在: {db}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)


@graph_app.command("init")
def graph_init(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
) -> None:
    """Initialize a JVS causal graph SQLite database with indexes and FTS5."""
    with CausalEngine(db) as engine:
        engine.init()
    console.print(f"[green]Initialized database:[/green] {db}")


@graph_app.command("ingest")
def graph_ingest(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    events: Annotated[Path, typer.Option("--events", help="Events JSONL path")],
    edges: Annotated[Path | None, typer.Option("--edges", help="Optional edges JSONL path")] = None,
    apply_gate: Annotated[
        bool, typer.Option("--gate/--no-gate", help="Apply confidence gate on edges")
    ] = True,
    embed: Annotated[
        bool, typer.Option("--embed", help="Compute embeddings via configured provider")
    ] = False,
) -> None:
    """Ingest events and optional causal edges JSONL files into SQLite."""
    try:
        ev_list = _read_jsonl_models(events, Event)
        ed_list = _read_jsonl_models(edges, CausalEdge) if edges is not None else []
    except InputValidationError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    with CausalEngine(db) as engine:
        engine.init()
        ev_stats = engine.ingest_events(ev_list, embed=embed)
        ed_stats = (
            engine.ingest_edges(ed_list, apply_gate=apply_gate) if ed_list else {}
        )
    console.print({"events": ev_stats, "edges": ed_stats})


@graph_app.command("ingest-text")
def graph_ingest_text(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    file: Annotated[Path, typer.Option("--file", help="Raw text/markdown file path")],
    corpus: Annotated[str | None, typer.Option("--corpus", help="Corpus tag")] = None,
    judge_edges: Annotated[
        bool, typer.Option("--judge-edges/--no-judge-edges", help="Infer causal edges")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Extract events from text, infer causal edges via LLM, and store them in SQLite."""
    if not file.is_file():
        err_console.print(f"[red]jvs: 输入文件不存在: {file}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)
    text = file.read_text(encoding="utf-8")
    try:
        with CausalEngine(db) as engine:
            res = engine.ingest_text(text, corpus=corpus, judge_edges=judge_edges)
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy blocked: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc
    if as_json:
        typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        console.print(res)


@graph_app.command("stats")
def graph_stats(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON output")] = False,
) -> None:
    """Display graph storage counts and structural diagnostics."""
    _ensure_db_exists(db)
    with CausalEngine(db, read_only=True) as engine:
        payload = {"storage": engine.stats(), "analytics": engine.analytics()}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    st = payload["storage"]
    an = payload["analytics"]
    table = Table(title=f"JVS Graph Summary ({db.name})")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Events", str(st["events"]))
    table.add_row("Edges", str(st["edges"]))
    table.add_row("Expandable Edges", str(an["expandable_edges"]))
    table.add_row("Connected Components", str(an["connected_components"]))
    table.add_row("Isolated Nodes", str(an["isolated_nodes"]))
    table.add_row("FTS5 Indexed", str(st["fts5_indexed"]))
    table.add_row("Vec Indexed", str(st["vec_indexed"]))
    console.print(table)


@graph_app.command("rebuild-index")
def graph_rebuild_index(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
) -> None:
    """Upgrade schema (if needed) and rebuild the FTS5 trigram search index."""
    _ensure_db_exists(db)
    with CausalEngine(db, read_only=False) as engine:
        count = engine.rebuild_index()
    console.print(f"[green]Rebuilt FTS5 index for {count} events[/green]")


@graph_app.command("search")
def graph_search(
    query: Annotated[str, typer.Argument(help="Search query")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    k: Annotated[int, typer.Option("--k", help="Top K results")] = 5,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON output")] = False,
) -> None:
    """Search events via hybrid FTS5 trigram, vector, and temporal RRF."""
    _ensure_db_exists(db)
    with CausalEngine(db, read_only=True) as engine:
        hits = engine.search(query, k=k)
    if as_json:
        typer.echo(
            json.dumps([h.model_dump() for h in hits], ensure_ascii=False, indent=2)
        )
        return
    table = Table(title=f"Search: {query}")
    table.add_column("ID")
    table.add_column("TS")
    table.add_column("Score", justify="right")
    table.add_column("Summary")
    for h in hits:
        ev = h.event
        summary = f"{ev.type or ''} | {ev.subject or ''} {ev.action or ''} {ev.object or ''}"
        table.add_row(ev.id, ev.ts, f"{h.score:.4f}", summary.strip())
    console.print(table)


@graph_app.command("why")
def graph_why(
    event_id: Annotated[str, typer.Argument(help="Starting event ID")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    max_hops: Annotated[int, typer.Option("--max-hops", help="Max BFS hops")] = 3,
    allow_weak: Annotated[
        bool, typer.Option("--allow-weak", help="Allow weak edges to expand multi-hop")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON output")] = False,
) -> None:
    """Trace causal chains and M1-11 degradation assessment starting from `event_id`."""
    _ensure_db_exists(db)
    settings = JVSSettings(db_path=db, allow_weak_expand=allow_weak)
    with CausalEngine(db, settings=settings, read_only=True) as engine:
        cfg = TraversalConfig(
            max_hops=max_hops,
            conf_gate=settings.conf_gate,
            allow_weak_expand=allow_weak,
            reverse_enabled=settings.reverse_enabled,
            allowed_reverse_types=settings.reverse_types,
        )
        res, assessment = engine.why(event_id, config=cfg)
    if as_json:
        typer.echo(
            json.dumps(
                {"traversal": res.model_dump(), "degradation": assessment.model_dump()},
                ensure_ascii=False,
                indent=2,
            )
        )
        if not res.ok:
            raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)
        return

    if not res.ok:
        err_console.print(f"[red]{res.error}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)

    console.print(
        f"[bold]Why({res.start_event})[/bold] hops={res.max_hops} "
        f"events={len(res.events)} edges={len(res.edges)} "
        f"degradation={assessment.degradation_class.value}"
    )
    if assessment.degraded:
        console.print(f"[yellow]{assessment.statement}[/yellow]")
    for edge in res.edges:
        tag = "expand" if edge.expandable else "clue-only"
        console.print(
            f"  {edge.edge_id}: {edge.from_event} --{edge.type}/{edge.strength}--> "
            f"{edge.to_event} ({tag}, conf={edge.confidence}) | {edge.rationale or ''}"
        )


@graph_app.command("timeline")
def graph_timeline(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    subject: Annotated[str | None, typer.Option("--subject", help="Exact subject filter")] = None,
    from_ts: Annotated[str | None, typer.Option("--from", help="Lower ISO date bound")] = None,
    to_ts: Annotated[str | None, typer.Option("--to", help="Upper ISO date bound")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON output")] = False,
) -> None:
    """List events chronologically with incident edge annotations."""
    _ensure_db_exists(db)
    try:
        with CausalEngine(db, read_only=True) as engine:
            res = engine.timeline(subject=subject, from_ts=from_ts, to_ts=to_ts)
    except ValueError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    console.print(f"[bold]Timeline[/bold]: {len(res.events)} event(s)")
    for item in res.events:
        ev = item.event
        console.print(f"  {ev.ts}  {ev.id}  [{ev.type}]  {ev.subject}: {ev.action} {ev.object}")


@pipeline_app.command("extract")
def pipeline_extract(
    file: Annotated[Path, typer.Option("--file", help="Input text file")],
    out: Annotated[Path, typer.Option("--out", help="Output events JSONL path")],
    corpus: Annotated[str | None, typer.Option("--corpus", help="Corpus tag")] = None,
) -> None:
    """Extract structured events from a text file into JSONL using the configured LLM."""
    if not file.is_file():
        err_console.print(f"[red]jvs: 文件不存在: {file}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)
    settings = JVSSettings()
    gate = PrivacyGate(
        names_path=settings.resolve_privacy_names_path(),
        require_names=settings.privacy_require_names,
        enabled=settings.privacy_enabled,
    )
    llm = OpenAILLMClient(settings.resolve_llm_config(), privacy_gate=gate)
    prompt_path = settings.resolve_assets_dir() / "prompts" / "event_extract_v2.md"
    try:
        events = extract_events_from_text(
            file.read_text(encoding="utf-8"),
            llm,
            prompt_path=prompt_path,
            corpus=corpus,
        )
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy blocked: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")
    console.print(f"[green]Extracted {len(events)} events -> {out}[/green]")


@pipeline_app.command("candidates")
def pipeline_candidates(
    events: Annotated[Path, typer.Option("--events", help="Events JSONL path")],
    out: Annotated[Path, typer.Option("--out", help="Output candidate pairs JSONL path")],
    window_days: Annotated[int, typer.Option("--window-days")] = 7,
    max_candidates: Annotated[int, typer.Option("--max-candidates")] = 600,
) -> None:
    """Generate deterministic time-ordered candidate event pairs (no LLM)."""
    try:
        ev_list = _read_jsonl_models(events, Event)
    except InputValidationError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc
    pairs = generate_candidate_pairs(
        ev_list, window_days=window_days, max_candidates=max_candidates
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(pair.model_dump_json() + "\n")
    console.print(f"[green]Generated {len(pairs)} candidate pairs -> {out}[/green]")


@pipeline_app.command("judge-edges")
def pipeline_judge_edges(
    events: Annotated[Path, typer.Option("--events", help="Events JSONL path")],
    candidates: Annotated[Path, typer.Option("--candidates", help="Candidate pairs JSONL path")],
    out: Annotated[Path, typer.Option("--out", help="Output edges JSONL path")],
    batch_size: Annotated[int, typer.Option("--batch-size")] = 60,
) -> None:
    """Judge candidate pairs into confidence-gated causal edges using the configured LLM."""
    try:
        ev_list = _read_jsonl_models(events, Event)
        cand_list = _read_jsonl_models(candidates, CandidatePair)
    except InputValidationError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    settings = JVSSettings()
    gate = PrivacyGate(
        names_path=settings.resolve_privacy_names_path(),
        require_names=settings.privacy_require_names,
        enabled=settings.privacy_enabled,
    )
    llm = OpenAILLMClient(settings.resolve_llm_config(), privacy_gate=gate)
    prompt_path = settings.resolve_assets_dir() / "prompts" / "causal_edges_v2.1.md"
    try:
        edges = judge_candidate_edges(
            cand_list,
            {e.id: e for e in ev_list},
            llm,
            prompt_path=prompt_path,
            batch_size=batch_size,
            conf_gate=settings.conf_gate,
            apply_gate=True,
        )
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy blocked: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for edge in edges:
            fh.write(edge.model_dump_json() + "\n")
    console.print(f"[green]Judged {len(edges)} causal edges -> {out}[/green]")


@qa_app.command("ask")
def qa_ask(
    question: Annotated[str, typer.Argument(help="Causal question text")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    offline: Annotated[
        bool, typer.Option("--offline", help="Use offline extractive template (no LLM)")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON output")] = False,
) -> None:
    """Answer a single causal question grounded in the graph."""
    _ensure_db_exists(db)
    try:
        with CausalEngine(db, read_only=True) as engine:
            ans = engine.ask(question, use_llm=not offline)
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy blocked: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc
    if as_json:
        typer.echo(ans.model_dump_json(indent=2))
    else:
        console.print(ans.answer_text, markup=False)


@qa_app.command("run")
def qa_run(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    questions: Annotated[Path, typer.Option("--questions", help="Questions JSONL path")],
    out: Annotated[Path, typer.Option("--out", help="Output directory")],
    offline: Annotated[
        bool, typer.Option("--offline", help="Run offline extractive mode (no LLM)")
    ] = False,
    allow_blocked: Annotated[bool, typer.Option("--allow-blocked")] = False,
    discard_incompatible: Annotated[bool, typer.Option("--discard-incompatible")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Execute batch QA over a JSONL question set with preflight privacy and resume checks."""
    _ensure_db_exists(db)
    try:
        with CausalEngine(db, read_only=True) as engine:
            meta, rc = engine.run_qa_batch(
                questions,
                out,
                use_llm=not offline,
                allow_blocked=allow_blocked,
                discard_incompatible=discard_incompatible,
                dry_run=dry_run,
            )
    except InputValidationError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    if as_json:
        typer.echo(meta.model_dump_json(indent=2))
    else:
        console.print(
            f"[jvs qa run] status={meta.status} requested={meta.requested} "
            f"ok={meta.records_ok} blocked={len(meta.blocked)}"
        )
    if rc != EXIT_OK:
        raise typer.Exit(code=rc)


@qa_app.command("prompt")
def qa_prompt(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path")],
    questions: Annotated[Path, typer.Option("--questions", help="Questions JSONL path")],
    out: Annotated[Path, typer.Option("--out", help="Output directory")],
    diff: Annotated[Path | None, typer.Option("--diff", help="Baseline prompt dir to diff")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing prompt output")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Export per-question production prompts offline and optionally diff against a baseline."""
    _ensure_db_exists(db)
    try:
        with CausalEngine(db, read_only=True) as engine:
            stats = engine.export_promptset(questions, out, baseline_dir=diff, force=force)
    except InputValidationError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    if as_json:
        typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        console.print(
            f"[jvs qa prompt] {stats['total']} 题 ｜ {stats['total_chars']:,} 字符 ｜ "
            f"图 {stats['graph']['events']} 事件 / {stats['graph']['edges']} 边"
        )
        console.print(f"  → {stats['prompts_dir']}")
        console.print(f"  → {stats['meta_path']}")
        report = stats.get("diff")
        if report is not None:
            console.print(
                f"[diff] same={report['same']} changed={len(report['changed'])} "
                f"only_new={len(report['only_new'])} only_base={len(report['only_base'])}"
            )
    if stats.get("diff") and stats["diff"]["different"]:
        raise typer.Exit(code=EXIT_PARTIAL_OR_DIFF)


@eval_app.command("score")
def eval_score(
    answers: Annotated[Path, typer.Option("--answers", help="answers.jsonl path")],
    questions: Annotated[Path, typer.Option("--questions", help="gold.jsonl or questions.jsonl")],
    out: Annotated[Path | None, typer.Option("--out", help="Optional JSON output path")] = None,
    threshold: Annotated[float, typer.Option("--threshold")] = 0.45,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Run deterministic M1 rule evaluation over answers and gold standards."""
    try:
        ans_list, gold_map = load_answers_and_gold(answers, questions)
    except Exception as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    summary = RuleEvaluator().evaluate(ans_list, gold_map, match_threshold=threshold)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    if as_json:
        typer.echo(summary.model_dump_json(indent=2))
    else:
        console.print(
            f"[rule eval] n={summary.total_questions} avail={summary.available_count} "
            f"causal={summary.causal_correct_count}/{summary.total_questions} "
            f"({summary.causal_accuracy:.2%}, CI [{summary.causal_accuracy_ci.lower:.2%}, "
            f"{summary.causal_accuracy_ci.upper:.2%}])"
        )


@eval_app.command("judge")
def eval_judge(
    answers: Annotated[Path, typer.Option("--answers", help="answers.jsonl path")],
    questions: Annotated[Path, typer.Option("--questions", help="gold.jsonl or questions.jsonl")],
    out: Annotated[Path, typer.Option("--out", help="Output scores JSON path")],
    negation: Annotated[bool, typer.Option("--negation/--no-negation")] = True,
    cause_relax: Annotated[bool, typer.Option("--cause-relax/--no-cause-relax")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Run locked, checkpointed structured LLM judging over answers and gold standards."""
    try:
        ans_list, gold_map = load_answers_and_gold(answers, questions)
    except Exception as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    settings = JVSSettings()
    gate = PrivacyGate(
        names_path=settings.resolve_privacy_names_path(),
        require_names=settings.privacy_require_names,
        enabled=settings.privacy_enabled,
    )
    llm = OpenAILLMClient(settings.resolve_llm_config(), privacy_gate=gate)
    judge = LLMJudge(llm, negation_rule=negation, cause_relax=cause_relax)
    try:
        summary = judge.evaluate(ans_list, gold_map, out_path=out)
    except OutputBusyError as exc:
        err_console.print(f"[red]jvs: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy blocked: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    if as_json:
        typer.echo(summary.model_dump_json(indent=2))
    else:
        console.print(
            f"[llm judge] causal={summary.causal_correct_count}/{summary.total_questions} "
            f"rubric={summary.rubric_sha256[:12] if summary.rubric_sha256 else ''}"
        )


@eval_app.command("readout")
def eval_readout(
    files: Annotated[list[Path], typer.Argument(help="judge-result.<batch><run>.md files")],
    expect_questions: Annotated[
        Path | None, typer.Option("--expect-questions", help="Question JSONL denominator anchor")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON summary")] = False,
) -> None:
    """Compute strict multi-run majority readout with tie rejection and Wilson CI."""
    expected_qids: list[str] | None = None
    if expect_questions is not None:
        try:
            expected_qids = [q.qid for q in load_questions_jsonl(expect_questions)]
        except InputValidationError as exc:
            err_console.print(f"[red]jvs: {exc}[/red]")
            raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    summary = compute_readout(files, expected_qids=expected_qids)
    if as_json:
        typer.echo(summary.model_dump_json(indent=2))
    else:
        for b in summary.batches:
            console.print(
                f"Batch {b.batch} ({b.runs} runs): causal {b.majority_causal}/{b.total_questions} "
                f"[{b.majority_causal_ci.lower:.2%}, {b.majority_causal_ci.upper:.2%}] | "
                f"consistent={b.consistent_count} flipped={b.flipped_qids} tied={b.tied_qids}"
            )
        for err in summary.errors:
            err_console.print(f"[red]ERROR: {err}[/red]")

    if not summary.ok:
        raise typer.Exit(code=EXIT_FAILED)


@privacy_app.command("scan")
def privacy_scan(
    target: Annotated[Path, typer.Argument(help="File or directory to scan")],
    names: Annotated[Path | None, typer.Option("--names", help="Optional names vocab file")] = None,
    require_names: Annotated[bool, typer.Option("--require-names")] = False,
    allow_urls: Annotated[bool, typer.Option("--allow-urls")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON report")] = False,
) -> None:
    """Scan a file or directory for PII, known names, or leaked credentials."""
    if not target.exists():
        err_console.print(f"[red]jvs: 路径不存在: {target}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED)
    gate = PrivacyGate(
        names_path=names,
        require_names=require_names,
        enabled=True,
        allow_urls=allow_urls,
    )
    try:
        reports = gate.scan_path(target)
    except PrivacyBlocked as exc:
        err_console.print(f"[red]jvs privacy scan error: {exc}[/red]")
        raise typer.Exit(code=EXIT_INPUT_OR_REFUSED) from exc

    dirty = {str(p): r.model_dump() for p, r in reports.items() if not r.clean}
    if as_json:
        typer.echo(
            json.dumps(
                {"clean": len(dirty) == 0, "scanned_files": len(reports), "dirty": dirty},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        console.print(f"Scanned {len(reports)} file(s); hits in {len(dirty)} file(s).")
        for p, info in dirty.items():
            console.print(f"  [red]{p}[/red]: {info['counts']}")

    if dirty:
        raise typer.Exit(code=EXIT_FAILED)


@mcp_app.command("serve")
def mcp_serve(
    db: Annotated[Path, typer.Option("--db", help="SQLite causal graph database path")],
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"],
        typer.Option("--transport", help="MCP transport"),
    ] = "stdio",
    read_only: Annotated[
        bool, typer.Option("--read-only", help="Disallow graph write tools")
    ] = False,
) -> None:
    """Start the JVS MCPServer over stdio, SSE, or streamable HTTP."""
    from jvs.mcp.server import create_mcp_server

    server = create_mcp_server(db, read_only=read_only)
    server.run(transport=transport)


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
