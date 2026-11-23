"""Fail-closed outbound privacy and secret scanning gate."""

from jvs.infrastructure.privacy.gate import (
    PrivacyBlocked,
    PrivacyFinding,
    PrivacyGate,
    PrivacyReport,
    UnknownExtensionError,
)

__all__ = [
    "PrivacyBlocked",
    "PrivacyFinding",
    "PrivacyGate",
    "PrivacyReport",
    "UnknownExtensionError",
]
