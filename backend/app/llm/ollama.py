"""Ollama provider: talks to a local Ollama server over HTTP. No API key involved."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from app.config import Settings
from app.llm.base import LLMError, LLMRequest, LLMResponse


class OllamaProvider:
    def __init__(
        self,
        host: str,
        timeout: float,
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(base_url=host, timeout=timeout, transport=transport)
        self._max_retries = max_retries
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> OllamaProvider:
        return cls(settings.ollama_host, settings.llm_timeout_seconds, settings.llm_max_retries)

    def generate(self, request: LLMRequest) -> LLMResponse:
        user: dict[str, object] = {"role": "user", "content": request.prompt}
        if request.images:
            user["images"] = [base64.b64encode(i).decode("ascii") for i in request.images]
        body = {
            "model": request.model,
            "messages": [{"role": "system", "content": request.system}, user],
            "format": request.schema,  # constrained decoding to the JSON schema
            "stream": False,
            "options": {"temperature": 0, "seed": 0},  # as deterministic as the backend allows
        }

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                resp = self._client.post("/api/chat", json=body)
                if resp.status_code >= 500:
                    raise LLMError(f"Ollama server error {resp.status_code}")
                if resp.status_code != 200:
                    # 4xx will not get better by retrying (unknown model, bad request).
                    raise LLMError(
                        f"Ollama rejected the request ({resp.status_code}): {resp.text[:300]}"
                    )
                data = resp.json()
                return LLMResponse(
                    text=data["message"]["content"],
                    model=request.model,
                    tokens_in=int(data.get("prompt_eval_count", 0)),
                    tokens_out=int(data.get("eval_count", 0)),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    cost_usd=Decimal("0"),
                )
            except (httpx.TransportError, LLMError, KeyError, ValueError) as exc:
                last_error = exc
                if isinstance(exc, LLMError) and "rejected" in str(exc):
                    break
                if attempt < self._max_retries:
                    self._sleep(2**attempt)  # exponential backoff
        raise LLMError(f"Ollama request failed after retries: {last_error}") from last_error
