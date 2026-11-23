"""Fail-closed privacy and credential gate before outbound LLM or export operations."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

SECRET_PATTERNS: dict[str, str] = {
    "bearer": r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    "api_key": r"\bsk-[A-Za-z0-9_-]{16,}",
    "ghp": r"\bghp_[A-Za-z0-9]{20,}",
    "AKIA": r"\bAKIA[0-9A-Z]{16}\b",
    "AIza": r"\bAIza[0-9A-Za-z_\-]{30,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "credential": (
        r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
        r"[\"']?[ \t]*[:=][ \t]*[\"']?[^\s\"',;}{]{12,}"
    ),
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "url": r"https?://[^\s]+",
    "cn_mobile": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "cn_id_card": (
        r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
        r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
    ),
    "feishu_openid": r"\bou_[0-9a-f]{16,}\b",
}

_PLACEHOLDER_PREFIXES = ("未映射", "未识别", "未知", "待补全")
_NON_PERSON_NOTE = re.compile(
    r"非自然人|排除出人物归一|不参与人物归一|不属于人物归一|非人物归一"
)
_NON_PERSON_KINDS = frozenset(
    ("system", "bot", "service", "org", "organization", "group", "device")
)

SCAN_EXT = frozenset(
    (
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".jsonl",
        ".ndjson",
        ".csv",
        ".tsv",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".htm",
        ".css",
        ".xml",
        ".sql",
        ".sh",
        ".bat",
        ".ps1",
        ".log",
        ".ini",
        ".cfg",
        ".conf",
        ".toml",
        ".rst",
        ".sse",
    )
)

SKIP_EXT = frozenset(
    (
        ".zip",
        ".gz",
        ".7z",
        ".rar",
        ".tar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".db",
        ".sqlite",
        ".pyc",
        ".pkl",
        ".woff",
        ".woff2",
        ".ttf",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".exe",
        ".dll",
        ".so",
        ".bin",
    )
)


class PrivacyBlocked(RuntimeError):
    """Raised when outbound content hits privacy/credential rules or the gate fails closed."""


class UnknownExtensionError(PrivacyBlocked):
    """Raised when directory scanning encounters an unregistered file extension."""


class PrivacyFinding(BaseModel):
    category: str
    line: int
    length: int
    redacted: bool = True


class PrivacyReport(BaseModel):
    clean: bool
    counts: dict[str, int] = Field(default_factory=dict)
    findings: list[PrivacyFinding] = Field(default_factory=list)


def normalize_text(text: str) -> str:
    """Apply Unicode NFKC + casefold to defeat full-width and casing bypasses."""
    return unicodedata.normalize("NFKC", text).casefold()


def _is_non_person(entry: dict[str, Any]) -> bool:
    """Exclude only explicitly non-person structured identity entries."""
    if entry.get("is_person") is False:
        return True
    kind = str(entry.get("entity_kind") or entry.get("entity_type") or "").strip().casefold()
    if kind in _NON_PERSON_KINDS:
        return True
    canonical = str(entry.get("canonical_name") or "").strip()
    if any(canonical.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return True
    note = str(entry.get("note") or "")
    return bool(_NON_PERSON_NOTE.search(note))


def load_names_from_file(path: Path) -> dict[str, list[str]]:
    """Load person names and aliases from JSON, YAML, or newline-delimited TXT."""
    if not path.is_file():
        raise PrivacyBlocked(f"隐私词表文件不存在，拒绝放行: {path}")
    suffix = path.suffix.lower()
    if suffix == ".txt":
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return {name: [] for name in lines}

    if suffix in (".yaml", ".yml"):
        yaml = YAML(typ="safe")
        raw = yaml.load(path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))

    names: dict[str, list[str]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                names.setdefault(item.strip(), [])
            elif isinstance(item, dict) and not _is_non_person(item):
                cn = str(item.get("canonical_name") or "").strip()
                if len(cn) >= 2:
                    names.setdefault(cn, []).extend(
                        str(a) for a in item.get("aliases", []) if a
                    )
        return names

    if isinstance(raw, dict):
        if "identities" in raw and isinstance(raw["identities"], list):
            for entry in raw["identities"]:
                if not isinstance(entry, dict) or _is_non_person(entry):
                    continue
                cn = str(entry.get("canonical_name") or "").strip()
                if len(cn) >= 2:
                    names.setdefault(cn, []).extend(
                        str(a) for a in entry.get("aliases", []) if a
                    )
            return names
        for key, aliases in raw.items():
            if str(key).strip():
                alias_list = [str(a) for a in aliases] if isinstance(aliases, list) else []
                names.setdefault(str(key).strip(), []).extend(alias_list)
        return names

    raise PrivacyBlocked(f"不支持的隐私词表格式: {path}")


def build_name_pattern(names: dict[str, list[str]]) -> re.Pattern[str] | None:
    """Compile normalized longest-first regex over known names & aliases."""
    terms: set[str] = set()
    for canonical, aliases in names.items():
        for raw_term in [canonical, *aliases]:
            term = normalize_text(str(raw_term)).strip()
            if len(term) >= 2 and not re.fullmatch(r"[a-z]\d+", term):
                terms.add(term)
    if not terms:
        return None
    joined = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(joined)


def _iter_pattern_matches(
    text: str,
    pattern: re.Pattern[str],
    category: str,
) -> list[PrivacyFinding]:
    """Find matches with O(N) linear line-number cursor tracking."""
    findings: list[PrivacyFinding] = []
    line_no = 1
    cursor = 0
    for match in pattern.finditer(text):
        line_no += text.count("\n", cursor, match.start())
        cursor = match.start()
        findings.append(
            PrivacyFinding(
                category=category,
                line=line_no,
                length=len(match.group(0)),
            )
        )
    return findings


class PrivacyGate:
    """Fail-closed privacy and credential gate for outbound text and file scans."""

    def __init__(
        self,
        *,
        names_path: Path | None = None,
        extra_names: dict[str, list[str]] | None = None,
        require_names: bool = False,
        enabled: bool = True,
        allow_urls: bool = False,
        extra_recognizers: Sequence[Callable[[str], list[PrivacyFinding]]] | None = None,
    ) -> None:
        self.enabled = enabled
        self.require_names = require_names
        self.names_path = names_path
        self.allow_urls = allow_urls
        self.extra_recognizers = list(extra_recognizers or [])

        self._load_error: Exception | None = None
        self._name_pattern: re.Pattern[str] | None = None
        self._secret_patterns: dict[str, re.Pattern[str]] = {}

        try:
            merged_names: dict[str, list[str]] = {}
            if names_path is not None:
                merged_names.update(load_names_from_file(names_path))
            if extra_names:
                for k, v in extra_names.items():
                    merged_names.setdefault(k, []).extend(v)
            self._name_pattern = build_name_pattern(merged_names)
            if self.require_names and self._name_pattern is None:
                raise PrivacyBlocked("已启用强制姓名词表检查，但有效姓名词表为空")
            self._secret_patterns = {
                k: re.compile(v)
                for k, v in SECRET_PATTERNS.items()
                if not (allow_urls and k == "url")
            }
        except Exception as exc:  # noqa: BLE001 — preserve error for fail-closed checks
            self._load_error = exc

    def scan_text(self, text: str) -> PrivacyReport:
        """Scan text for known names, secrets, PII patterns, and custom recognizers."""
        if not self.enabled:
            return PrivacyReport(clean=True)
        if self._load_error is not None:
            raise PrivacyBlocked(
                f"隐私闸门初始化失败，拒绝放行: {type(self._load_error).__name__}: "
                f"{self._load_error}"
            ) from self._load_error

        findings: list[PrivacyFinding] = []
        counts: dict[str, int] = {}

        if self._name_pattern is not None:
            norm = normalize_text(text)
            name_hits = _iter_pattern_matches(norm, self._name_pattern, "known_names")
            if name_hits:
                counts["known_names"] = len(name_hits)
                findings.extend(name_hits)

        for key, pattern in self._secret_patterns.items():
            hits = _iter_pattern_matches(text, pattern, key)
            if hits:
                counts[key] = len(hits)
                findings.extend(hits)

        for recognizer in self.extra_recognizers:
            rec_hits = recognizer(text)
            for finding in rec_hits:
                counts[finding.category] = counts.get(finding.category, 0) + 1
                findings.append(finding)

        return PrivacyReport(clean=len(findings) == 0, counts=counts, findings=findings)

    def assert_outbound(self, text: str, where: str = "llm") -> dict[str, int]:
        """Fail-closed check before outbound calls. Raises PrivacyBlocked on any hit."""
        if not self.enabled:
            return {}
        try:
            report = self.scan_text(text)
        except PrivacyBlocked:
            raise
        except Exception as exc:  # noqa: BLE001 — fail closed on unexpected scanner failures
            raise PrivacyBlocked(
                f"隐私闸门执行异常，拒绝外发 ({where}): {type(exc).__name__}: {exc}"
            ) from exc

        if not report.clean:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(report.counts.items()))
            raise PrivacyBlocked(f"外发内容命中隐私/凭证判据 ({where}): {detail}")
        return report.counts

    def scan_path(self, target: Path) -> dict[Path, PrivacyReport]:
        """Scan a file or directory tree strictly enforcing SCAN_EXT vs SKIP_EXT."""
        if not target.exists():
            raise FileNotFoundError(f"扫描路径不存在: {target}")
        files = (
            [target]
            if target.is_file()
            else sorted(p for p in target.rglob("*") if p.is_file())
        )
        results: dict[Path, PrivacyReport] = {}
        for path in files:
            ext = path.suffix.lower()
            if ext in SKIP_EXT:
                continue
            if ext not in SCAN_EXT:
                raise UnknownExtensionError(
                    f"发现未登记扩展名，拒绝静默放行: {path} (ext={ext or '<none>'})"
                )
            text = path.read_text(encoding="utf-8", errors="replace")
            results[path] = self.scan_text(text)
        return results
