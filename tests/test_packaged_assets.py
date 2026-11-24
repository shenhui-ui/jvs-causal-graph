"""Tests verifying bundled package assets work regardless of working directory."""

from __future__ import annotations

from pathlib import Path

import jvs
from jvs.config import JVSSettings


def test_resolve_assets_dir_from_empty_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = JVSSettings().resolve_assets_dir()
    assert (resolved / "fallback.yaml").is_file()
    assert (resolved / "prompts" / "event_extract_v2.md").is_file()
    assert (resolved / "prompts" / "causal_edges_v2.1.md").is_file()
    assert resolved == Path(jvs.__file__).resolve().parent / "assets"
