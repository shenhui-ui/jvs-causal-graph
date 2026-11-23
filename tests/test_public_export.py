"""Tests for runnable examples dataset and clean public V2 bundle exporter."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from jvs.application.engine import CausalEngine
from jvs.application.evaluation import RuleEvaluator, load_answers_and_gold
from jvs.application.qa import EXIT_OK
from jvs.config import JVSSettings
from jvs.domain.models import CausalEdge, DegradationClass, Event


def test_examples_dataset_end_to_end(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    ex_dir = repo_root / "examples"
    events = [
        Event.model_validate_json(line)
        for line in (ex_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    edges = [
        CausalEdge.model_validate_json(line)
        for line in (ex_dir / "edges.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(events) == 6
    assert len(edges) == 5

    db_path = tmp_path / "demo.db"
    settings = JVSSettings(db_path=db_path, assets_dir=repo_root / "assets")
    with CausalEngine(db_path, settings=settings) as engine:
        engine.init()
        ev_stats = engine.ingest_events(events)
        ed_stats = engine.ingest_edges(edges, apply_gate=True)
        assert ev_stats["inserted"] == 6
        assert ed_stats["inserted"] == 5
        # AX4 (confidence=0.50) must be gated to pending
        assert ed_stats["relabeled_pending"] == 1

        res1, deg1 = engine.why("EX1")
        assert res1.ok is True
        assert any(ev.id == "EX3" for ev in res1.events)
        assert deg1.degraded is False

        res6, deg6 = engine.why("EX6")
        assert res6.ok is True
        assert deg6.degradation_class == DegradationClass.NO_CHAIN

        qa_out = tmp_path / "qa_out"
        meta, rc = engine.run_qa_batch(ex_dir / "questions.jsonl", qa_out, use_llm=False)
        assert rc == EXIT_OK
        assert meta.records_ok == 3

    answers, gold_map = load_answers_and_gold(qa_out / "answers.jsonl", qa_out / "gold.jsonl")
    summary = RuleEvaluator().evaluate(answers, gold_map)
    assert summary.total_questions == 3
    assert summary.available_count == 3


def test_public_v2_exporter_allowlist_and_fail_closed(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    if not (scripts_dir / "export_public_v2.py").is_file():
        return

    sys.path.insert(0, str(scripts_dir))
    import export_public_v2  # type: ignore[import-not-found]

    secret_name = "机密" + "测试人"
    export_names = tmp_path / "export_names.json"
    export_names.write_text(
        json.dumps({"identities": [{"canonical_name": secret_name}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    out_dir = tmp_path / "public_out"
    summary = export_public_v2.export_public_bundle(
        source_root=repo_root,
        out_dir=out_dir,
        names_path=export_names,
        verify_tests=False,
        init_git=False,
    )
    assert summary["file_count"] >= 20
    assert (out_dir / "LICENSE").is_file()
    assert (out_dir / "release-manifest.json").is_file()
    assert (out_dir / ".github" / "workflows" / "ci.yml").is_file()
    assert (out_dir / "examples" / "events.jsonl").is_file()

    manifest = json.loads((out_dir / "release-manifest.json").read_text(encoding="utf-8"))
    manifest_paths = {item["path"] for item in manifest["files"]}
    assert "LICENSE" in manifest_paths
    assert "src/jvs/__init__.py" in manifest_paths
    assert "scripts/export_public_v2.py" not in manifest_paths

    # Negative test: inject a known personal name into a staged copy -> MUST fail closed
    dirty_src = tmp_path / "dirty_src"
    shutil.copytree(out_dir, dirty_src)
    (dirty_src / "README.md").write_text(f"泄露姓名：{secret_name}\n", encoding="utf-8")
    with pytest.raises(export_public_v2.PublicExportError):
        export_public_v2.export_public_bundle(
            source_root=dirty_src,
            dry_run=True,
            names_path=export_names,
            verify_tests=False,
        )
