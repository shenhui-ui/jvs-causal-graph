"""OpenAI SDK + Tenacity LLM & embedding clients with mandatory pre-send privacy gating."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from jvs.config import ResolvedEmbeddingConfig, ResolvedLLMConfig
from jvs.domain.ports import PrivacyGatePort

TModel = TypeVar("TModel", bound=BaseModel)

_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def _extract_json_text(raw: str) -> str:
    """Strip optional markdown code fences and isolate the outermost JSON object or array."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    obj_idx = text.find("{")
    arr_idx = text.find("[")
    starts = [idx for idx in (obj_idx, arr_idx) if idx != -1]
    if not starts:
        return text
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return text[start : end + 1] if end > start else text[start:]


class OpenAILLMClient:
    """OpenAI-compatible chat client enforcing PrivacyGate before any network call."""

    def __init__(
        self,
        config: ResolvedLLMConfig,
        *,
        privacy_gate: PrivacyGatePort,
        openai_client: Any | None = None,
    ) -> None:
        self.config = config
        self.privacy_gate = privacy_gate
        self._client = openai_client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_s,
            max_retries=0,  # handled explicitly by tenacity with jitter
        )

    @property
    def model_name(self) -> str:
        return self.config.model

    def _guard_outbound(self, payload: dict[str, Any], where: str) -> None:
        # Serialize with ensure_ascii=False so non-ASCII names cannot bypass regex scanning.
        serialized = json.dumps(payload, ensure_ascii=False)
        self.privacy_gate.assert_outbound(serialized, where=where)

    def _call_with_retry(self, kwargs: dict[str, Any]) -> str:
        attempts = max(1, self.config.max_retries + 1)

        @retry(
            reraise=True,
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(multiplier=2.0, max=60.0),
            retry=retry_if_exception_type(_RETRYABLE),
        )
        def _invoke() -> str:
            resp = self._client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            return (content or "").strip()

        return _invoke()

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        where: str = "llm/chat",
    ) -> str:
        """Send a privacy-checked chat completion request and return text."""
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": (
                self.config.temperature if temperature is None else temperature
            ),
        }
        self._guard_outbound(kwargs, where=where)
        return self._call_with_retry(kwargs)

    def complete_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        where: str = "llm/structured",
    ) -> TModel:
        """Request structured JSON output validated against a Pydantic response_model."""
        base_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": (
                self.config.temperature if temperature is None else temperature
            ),
            "response_format": {"type": "json_object"},
        }
        self._guard_outbound(base_kwargs, where=where)

        try:
            raw = self._call_with_retry(base_kwargs)
        except BadRequestError:
            # Some OpenAI-compatible endpoints reject response_format=json_object.
            fallback_kwargs = dict(base_kwargs)
            fallback_kwargs.pop("response_format", None)
            raw = self._call_with_retry(fallback_kwargs)

        try:
            return self._parse_model(raw, response_model)
        except (ValueError, ValidationError):
            repair_messages = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        "上次返回格式不符合要求。请只输出一个合法的 JSON 对象，"
                        "不要任何解释或代码块标记。"
                    ),
                }
            ]
            repair_kwargs = {
                "model": self.config.model,
                "messages": repair_messages,
                "max_tokens": max_tokens or self.config.max_tokens,
                "temperature": 0.0,
            }
            self._guard_outbound(repair_kwargs, where=f"{where}/repair")
            repaired = self._call_with_retry(repair_kwargs)
            return self._parse_model(repaired, response_model)

    @staticmethod
    def _parse_model(raw: str, response_model: type[TModel]) -> TModel:
        cleaned = _extract_json_text(raw)
        data = json.loads(cleaned)
        if isinstance(data, list):
            fields = list(response_model.model_fields.keys())
            if len(fields) == 1:
                data = {fields[0]: data}
        return response_model.model_validate(data)


class OpenAIEmbeddingClient:
    """OpenAI-compatible embedding client gated by PrivacyGate."""

    def __init__(
        self,
        config: ResolvedEmbeddingConfig,
        *,
        privacy_gate: PrivacyGatePort,
        openai_client: Any | None = None,
    ) -> None:
        self.config = config
        self.privacy_gate = privacy_gate
        self._client = openai_client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_s,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        payload = {"model": self.config.model, "input": items}
        self.privacy_gate.assert_outbound(
            json.dumps(payload, ensure_ascii=False),
            where="llm/embeddings",
        )
        resp = self._client.embeddings.create(model=self.config.model, input=items)
        return [list(entry.embedding) for entry in resp.data]
