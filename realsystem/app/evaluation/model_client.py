"""OpenAI-compatible, single-shot client. Each call is a brand-new conversation with one user message."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

TRANSPORT_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
MAX_TRANSPORT_RETRIES = 3


@dataclass
class ModelConfig:
    api_key: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "ModelConfig | None":
        key, model = os.getenv("OPENAI_API_KEY"), os.getenv("MODEL_NAME")
        if not key or not model:
            return None
        return cls(api_key=key, model=model, base_url=os.getenv("OPENAI_BASE_URL") or None)


@dataclass
class Completion:
    raw_text: str | None
    finish_reason: str | None
    usage: dict[str, Any] | None
    response_model: str | None
    transport_errors: list[str] = field(default_factory=list)


def complete_once(config: ModelConfig, prompt: str, max_tokens: int = 16000) -> Completion:
    """Retries only on documented transport errors (no response was received). A received answer
    is returned verbatim, however wrong, empty or malformed."""
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=180.0, max_retries=0)
    errors: list[str] = []
    for attempt in range(MAX_TRANSPORT_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=config.model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
            )
            break
        except TRANSPORT_ERRORS as exc:
            errors.append(f"attempt {attempt + 1}: {type(exc).__name__}")
            if attempt == MAX_TRANSPORT_RETRIES:
                raise
            time.sleep(2 ** attempt)
    choice = resp.choices[0] if resp.choices else None
    return Completion(
        raw_text=choice.message.content if choice else None,
        finish_reason=choice.finish_reason if choice else None,
        usage=resp.usage.model_dump() if resp.usage else None,
        response_model=resp.model,
        transport_errors=errors,
    )


def endpoint_host(config: ModelConfig) -> str:
    return urlparse(config.base_url).netloc if config.base_url else "api.openai.com"
