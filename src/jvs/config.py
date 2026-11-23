"""Unified configuration and provider resolution via pydantic-settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ProviderPreset:
    """Default connection parameters for a named OpenAI-compatible LLM provider."""

    name: str
    base_url: str | None
    default_model: str
    key_env: str | None
    model_env: str | None = None


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        key_env="OPENAI_API_KEY",
    ),
    "deepseek": ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        key_env="DEEPSEEK_API_KEY",
    ),
    "doubao": ProviderPreset(
        name="doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-seed-1-6-250615",
        key_env="DOUBAO_API_KEY",
        model_env="DOUBAO_MODEL",
    ),
    "sensenova": ProviderPreset(
        name="sensenova",
        base_url="https://token.sensenova.cn/v1",
        default_model="deepseek-v4-pro",
        key_env="SENSENOVA1_API_KEY",
    ),
    "ollama": ProviderPreset(
        name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        default_model="qwen2.5:7b",
        key_env=None,
    ),
    "custom": ProviderPreset(
        name="custom",
        base_url=None,
        default_model="gpt-4o-mini",
        key_env="JVS_LLM_API_KEY",
    ),
}


@dataclass(frozen=True)
class ResolvedLLMConfig:
    """Concrete runtime LLM endpoint configuration."""

    provider: str
    base_url: str | None
    api_key: str
    model: str
    max_tokens: int
    temperature: float
    timeout_s: float
    max_retries: int


@dataclass(frozen=True)
class ResolvedEmbeddingConfig:
    """Concrete runtime embedding endpoint configuration."""

    base_url: str | None
    api_key: str
    model: str
    dimensions: int
    timeout_s: float


class JVSSettings(BaseSettings):
    """Application settings resolved cleanly from kwargs, env vars, and .env."""

    model_config = SettingsConfigDict(
        env_prefix="JVS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("jvs_causal.db")
    assets_dir: Path | None = None

    # LLM settings
    llm_provider: str = "openai"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_max_tokens: int = Field(default=1024, ge=1, le=128000)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_s: float = Field(default=120.0, gt=0.0)
    llm_max_retries: int = Field(default=4, ge=0, le=12)

    # Optional embedding settings (enables sqlite-vec semantic search)
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_dimensions: int = Field(default=1024, ge=8, le=8192)

    # Graph / retrieval / prompt behavior settings
    conf_gate: float = Field(default=0.6, ge=0.0, le=1.0)
    allow_weak_expand: bool = False
    anchor_k: int = Field(default=5, ge=1, le=50)
    max_hops: int = Field(default=3, ge=0, le=10)
    reverse_enabled: bool = True
    reverse_types: tuple[str, ...] = (
        "cause",
        "trigger",
        "supersede",
        "evolve",
        "resolve",
    )
    qa_reverse_types: tuple[str, ...] = ("cause", "trigger")
    include_temporal_neighbors: bool = False
    neighbor_days: int = Field(default=12, ge=1, le=365)
    prioritize_direct_edges: bool = False
    render_chain_after: bool = True
    prompt_origin_guidance: bool = True
    prompt_cause_guidance: bool = False
    prompt_evidence_first: bool = False

    # Privacy gate settings (fail-closed)
    privacy_enabled: bool = True
    privacy_names_path: Path | None = Field(
        default=None,
        description="Optional path to known names JSON/YAML/TXT file.",
    )
    privacy_require_names: bool = False

    def resolve_assets_dir(self) -> Path:
        """Locate the assets directory without hardcoded host-specific paths."""
        if self.assets_dir is not None:
            return self.assets_dir.resolve()
        candidates = [
            Path(__file__).resolve().parents[2] / "assets",
            Path.cwd() / "assets",
        ]
        for cand in candidates:
            if (cand / "fallback.yaml").is_file():
                return cand
        for cand in candidates:
            if cand.is_dir():
                return cand
        return Path.cwd() / "assets"

    def resolve_privacy_names_path(self) -> Path | None:
        """Resolve optional external privacy vocabulary path."""
        if self.privacy_names_path is not None:
            return self.privacy_names_path
        env_val = os.environ.get("JVS_PRIVACY_NAMES", "").strip()
        return Path(env_val) if env_val else None

    def resolve_llm_config(
        self,
        provider_override: str | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
        temperature_override: float | None = None,
    ) -> ResolvedLLMConfig:
        """Resolve concrete OpenAI-compatible endpoint credentials and parameters."""
        pname = (provider_override or self.llm_provider or "openai").strip().lower()
        preset = PROVIDER_PRESETS.get(pname) or PROVIDER_PRESETS["custom"]

        base_url = self.llm_base_url or preset.base_url
        api_key = self.llm_api_key.get_secret_value() if self.llm_api_key else ""
        if not api_key and preset.key_env:
            api_key = os.environ.get(preset.key_env, "").strip()
        if not api_key and pname == "ollama":
            api_key = "ollama"
        if not api_key:
            hint = f" or {preset.key_env}" if preset.key_env else ""
            raise RuntimeError(
                f"API key not configured for provider '{pname}'. Set JVS_LLM_API_KEY{hint}."
            )

        model = model_override or self.llm_model
        if not model and preset.model_env:
            model = os.environ.get(preset.model_env, "").strip() or None
        model = model or preset.default_model

        return ResolvedLLMConfig(
            provider=pname,
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens_override or self.llm_max_tokens,
            temperature=(
                self.llm_temperature if temperature_override is None else temperature_override
            ),
            timeout_s=self.llm_timeout_s,
            max_retries=self.llm_max_retries,
        )

    def resolve_embedding_config(self) -> ResolvedEmbeddingConfig | None:
        """Resolve optional embedding client configuration, or None if disabled."""
        if not self.embedding_model:
            return None
        base_url = self.embedding_base_url or self.llm_base_url
        if not base_url:
            preset = PROVIDER_PRESETS.get(self.llm_provider.strip().lower())
            base_url = preset.base_url if preset else None
        api_key = ""
        if self.embedding_api_key:
            api_key = self.embedding_api_key.get_secret_value()
        elif self.llm_api_key:
            api_key = self.llm_api_key.get_secret_value()
        else:
            preset = PROVIDER_PRESETS.get(self.llm_provider.strip().lower())
            if preset and preset.key_env:
                api_key = os.environ.get(preset.key_env, "").strip()
            elif self.llm_provider.strip().lower() == "ollama":
                api_key = "ollama"
        if not api_key:
            raise RuntimeError("Embedding model configured, but no API key found.")
        return ResolvedEmbeddingConfig(
            base_url=base_url,
            api_key=api_key,
            model=self.embedding_model,
            dimensions=self.embedding_dimensions,
            timeout_s=self.llm_timeout_s,
        )

    def safe_summary(self) -> dict[str, Any]:
        """Return non-sensitive settings snapshot for run-meta and diagnostics."""
        return {
            "db_path": str(self.db_path),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model
            or PROVIDER_PRESETS.get(
                self.llm_provider.lower(), PROVIDER_PRESETS["custom"]
            ).default_model,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_temperature": self.llm_temperature,
            "embedding_model": self.embedding_model,
            "conf_gate": self.conf_gate,
            "allow_weak_expand": self.allow_weak_expand,
            "anchor_k": self.anchor_k,
            "max_hops": self.max_hops,
            "reverse_enabled": self.reverse_enabled,
            "qa_reverse_types": list(self.qa_reverse_types),
            "include_temporal_neighbors": self.include_temporal_neighbors,
            "privacy_enabled": self.privacy_enabled,
            "privacy_require_names": self.privacy_require_names,
        }
