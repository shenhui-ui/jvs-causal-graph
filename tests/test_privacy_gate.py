"""Tests for fail-closed PrivacyGate, Unicode normalization, and zero-network guarantees."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import FakeLLMClient

from jvs.infrastructure.privacy.gate import (
    PrivacyBlocked,
    PrivacyGate,
    UnknownExtensionError,
)


def test_privacy_gate_blocks_names_with_nfkc_and_casefold(sample_names_file: Path):
    gate = PrivacyGate(names_path=sample_names_file, require_names=True)

    # Human entry with note "待人物归一确认" MUST NOT be excluded!
    with pytest.raises(PrivacyBlocked):
        gate.assert_outbound("今天张三丰开会了")

    # Case-insensitive & fullwidth NFKC bypasses must be blocked!
    with pytest.raises(PrivacyBlocked):
        gate.assert_outbound("contact alicetestER or ａｌｔｅｓｔ soon")

    # Non-person entries (SystemBot, 未映射Z99, HelperService, GhostID) must not block!
    assert gate.assert_outbound("SystemBot and HelperService completed Z99 check") == {}


def test_privacy_gate_blocks_credentials_and_prevents_llm_call(sample_names_file: Path):
    gate = PrivacyGate(names_path=sample_names_file, require_names=True)
    llm = FakeLLMClient(privacy_gate=gate)

    fake_sk = "sk-" + "a1b2c3d4" * 3
    fake_bearer = "Bearer " + "xYz1234567890"
    fake_email = "user@" + "example.com"
    fake_mobile = "138" + "00138000"
    fake_url = "https://" + "secret.site"
    for secret in (fake_sk, fake_bearer, fake_email, fake_mobile, fake_url):
        with pytest.raises(PrivacyBlocked):
            llm.complete_text([{"role": "user", "content": f"hello {secret}"}])
    assert len(llm.calls) == 0


def test_privacy_gate_fails_closed_when_required_names_missing(tmp_path: Path):
    gate = PrivacyGate(names_path=tmp_path / "missing.json", require_names=True)
    with pytest.raises(PrivacyBlocked):
        gate.assert_outbound("clean text")


def test_privacy_gate_scan_path_rejects_unknown_extension(tmp_path: Path):
    (tmp_path / "good.txt").write_text("clean content", encoding="utf-8")
    (tmp_path / "mystery.customext").write_text("hidden", encoding="utf-8")
    gate = PrivacyGate(require_names=False)
    with pytest.raises(UnknownExtensionError):
        gate.scan_path(tmp_path)
