"""Unified JVS v2.0 quality gate: ruff + pytest + hygiene & privacy boundary checks."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_LEGACY_IMPORTS = (
    "01-host-product",
    "02-m1-evaluation",
    "03-d10-workspace",
    "04-restricted-materials",
    ".dsh/.credentials.yaml",
)

SECRET_REGEXES = (
    ("sk-", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("ghp_", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("AKIA", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AIza", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)


def _run(cmd: list[str], label: str) -> bool:
    print(f"[check] {label}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        print(f"[FAIL] {label} exited with {proc.returncode}")
        return False
    print(f"[OK]   {label}")
    return True


def _check_src_decoupling() -> bool:
    bad: list[str] = []
    for path in sorted((ROOT / "src" / "jvs").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_LEGACY_IMPORTS:
            if token in text:
                bad.append(f"{path.relative_to(ROOT)} references '{token}'")
    if bad:
        for item in bad:
            print(f"[FAIL] Legacy coupling: {item}")
        return False
    print("[OK]   src/jvs zero legacy coupling")
    return True


def _check_secrets() -> bool:
    targets = [
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
        *(ROOT / "assets").rglob("*"),
        *(ROOT / "examples").rglob("*"),
        *(ROOT / ".github").rglob("*"),
        *(ROOT / "docs").rglob("*.md"),
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "pyproject.toml",
        ROOT / ".env.example",
    ]
    hits: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, rx in SECRET_REGEXES:
            for match in rx.finditer(text):
                hits.append(
                    f"{path.relative_to(ROOT)} matched {name} (len={len(match.group(0))})"
                )
    if hits:
        for item in hits:
            print(f"[FAIL] Secret scan: {item}")
        return False
    print("[OK]   Secret hygiene scan")
    return True


def _check_bundled_assets_sync() -> bool:
    root_assets = ROOT / "assets"
    pkg_assets = ROOT / "src" / "jvs" / "assets"
    expected = {
        p.relative_to(root_assets): p.read_bytes()
        for p in sorted(root_assets.rglob("*"))
        if p.is_file()
    }
    bundled = {
        p.relative_to(pkg_assets): p.read_bytes()
        for p in sorted(pkg_assets.rglob("*"))
        if p.is_file()
    }
    if expected.keys() != bundled.keys():
        missing = sorted(str(k) for k in expected.keys() - bundled.keys())
        extra = sorted(str(k) for k in bundled.keys() - expected.keys())
        print(f"[FAIL] Bundled assets mismatch: missing={missing}, extra={extra}")
        return False
    dirty = [str(k) for k, raw in expected.items() if bundled[k] != raw]
    if dirty:
        print(f"[FAIL] Bundled assets content drift: {dirty}")
        return False
    print(f"[OK]   Bundled package assets in sync ({len(bundled)} files)")
    return True


def _check_restricted_untracked() -> bool:
    proc = subprocess.run(
        ["git", "ls-files", "04-restricted-materials"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print("[WARN] git ls-files unavailable; skipped restricted check")
        return True
    tracked = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if tracked:
        print(f"[FAIL] {len(tracked)} file(s) tracked under 04-restricted-materials/")
        return False
    print("[OK]   04-restricted-materials untracked")
    return True


def main() -> int:
    ok = True
    ruff_targets = ["src", "tests", "scripts/check.py"]
    if (ROOT / "scripts" / "export_public_v2.py").is_file():
        ruff_targets.append("scripts/export_public_v2.py")
    ok &= _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            *ruff_targets,
        ],
        "ruff",
    )
    ok &= _run([sys.executable, "-m", "pytest"], "pytest")
    ok &= _check_src_decoupling()
    ok &= _check_bundled_assets_sync()
    ok &= _check_secrets()
    ok &= _check_restricted_untracked()
    print("=" * 40)
    print("JVS QUALITY CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
