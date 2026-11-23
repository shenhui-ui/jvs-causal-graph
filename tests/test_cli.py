"""Tests for the Typer CLI (`jvs`) across graph, pipeline, qa, eval, and privacy."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from jvs.cli.main import app

runner = CliRunner()


def test_cli_graph_qa_eval_and_privacy_roundtrip(tmp_path: Path, sample_graph_db: Path):
    # 1. graph stats --json
    r_stats = runner.invoke(app, ["graph", "stats", "--db", str(sample_graph_db), "--json"])
    assert r_stats.exit_code == 0, r_stats.output
    stats_data = json.loads(r_stats.stdout)
    assert stats_data["storage"]["events"] == 7

    # 2. graph search --json
    r_search = runner.invoke(
        app, ["graph", "search", "扩容连接池", "--db", str(sample_graph_db), "--k", "2", "--json"]
    )
    assert r_search.exit_code == 0
    hits = json.loads(r_search.stdout)
    assert len(hits) >= 1

    # 3. graph why --json
    r_why = runner.invoke(app, ["graph", "why", "E1", "--db", str(sample_graph_db), "--json"])
    assert r_why.exit_code == 0
    why_data = json.loads(r_why.stdout)
    assert why_data["traversal"]["ok"] is True

    # 4. qa ask --offline
    r_ask = runner.invoke(
        app, ["qa", "ask", "为什么扩容连接池", "--db", str(sample_graph_db), "--offline", "--json"]
    )
    assert r_ask.exit_code == 0
    ans_data = json.loads(r_ask.stdout)
    assert ans_data["pipeline_ok"] is True

    # 5. qa run --offline + eval score
    q_file = tmp_path / "qs.jsonl"
    q_file.write_text(
        json.dumps(
            {
                "qid": "Q001",
                "question": "为什么扩容连接池？",
                "gold_answer": {"cause": "压测", "effect": "扩容", "evidence_hint": "E2"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "qa_out"
    r_run = runner.invoke(
        app,
        [
            "qa",
            "run",
            "--db",
            str(sample_graph_db),
            "--questions",
            str(q_file),
            "--out",
            str(out_dir),
            "--offline",
        ],
    )
    assert r_run.exit_code == 0

    r_score = runner.invoke(
        app,
        [
            "eval",
            "score",
            "--answers",
            str(out_dir / "answers.jsonl"),
            "--questions",
            str(out_dir / "gold.jsonl"),
            "--json",
        ],
    )
    assert r_score.exit_code == 0
    assert json.loads(r_score.stdout)["total_questions"] == 1

    # 6. qa prompt + invalid input exit code 2
    p_out = tmp_path / "p_out"
    r_prompt = runner.invoke(
        app,
        [
            "qa",
            "prompt",
            "--db",
            str(sample_graph_db),
            "--questions",
            str(q_file),
            "--out",
            str(p_out),
        ],
    )
    assert r_prompt.exit_code == 0
    assert (p_out / "prompts" / "Q001.md").is_file()

    r_bad = runner.invoke(
        app,
        [
            "qa",
            "prompt",
            "--db",
            str(tmp_path / "missing.db"),
            "--questions",
            str(q_file),
            "--out",
            str(tmp_path / "bad"),
        ],
    )
    assert r_bad.exit_code == 2

    # 7. privacy scan clean vs dirty
    clean_dir = tmp_path / "scan_clean"
    clean_dir.mkdir()
    (clean_dir / "note.md").write_text("clean design note", encoding="utf-8")
    assert runner.invoke(app, ["privacy", "scan", str(clean_dir)]).exit_code == 0

    (clean_dir / "leak.txt").write_text("contact admin@" + "example.com", encoding="utf-8")
    assert runner.invoke(app, ["privacy", "scan", str(clean_dir)]).exit_code == 1
