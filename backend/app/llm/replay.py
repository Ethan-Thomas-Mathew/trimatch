"""Record and replay LLM responses.

``RecordingProvider`` wraps a real provider and saves every answer to a fixture file named
by the request's fingerprint. ``ReplayProvider`` serves those files back and never calls a
model, so CI can exercise the whole pipeline for free and deterministically (build plan 9).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.llm.base import FixtureMissingError, LLMProvider, LLMRequest, LLMResponse


def _path(directory: Path, request: LLMRequest) -> Path:
    return directory / f"{request.fingerprint()}.json"


class ReplayProvider:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def generate(self, request: LLMRequest) -> LLMResponse:
        path = _path(self._dir, request)
        if not path.exists():
            raise FixtureMissingError(
                f"No recorded response for this request ({path.name}). "
                "Re-record with the real provider and --record."
            )
        data = json.loads(path.read_text("utf-8"))
        return LLMResponse(
            text=data["text"],
            model=data["model"],
            tokens_in=data["tokens_in"],
            tokens_out=data["tokens_out"],
            latency_ms=data["latency_ms"],
            cost_usd=Decimal(data["cost_usd"]),
        )


class RecordingProvider:
    def __init__(self, inner: LLMProvider, directory: Path) -> None:
        self._inner = inner
        self._dir = directory
        directory.mkdir(parents=True, exist_ok=True)

    def generate(self, request: LLMRequest) -> LLMResponse:
        response = self._inner.generate(request)
        record = {
            "model": response.model,
            "text": response.text,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "latency_ms": response.latency_ms,
            "cost_usd": str(response.cost_usd),
        }
        _path(self._dir, request).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        return response
