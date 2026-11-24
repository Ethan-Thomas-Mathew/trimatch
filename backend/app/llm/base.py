"""Provider-neutral LLM interface.

Extraction code depends only on ``LLMProvider``. Which model answers (a local Ollama
model, or recorded replays for tests and CI) is a configuration choice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class LLMError(Exception):
    """The model could not be reached or answered with an error."""


class FixtureMissingError(LLMError):
    """Replay mode was asked for a request that was never recorded."""


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    prompt: str
    schema: dict[str, Any]
    images: tuple[bytes, ...] = ()

    def fingerprint(self) -> str:
        """Stable hash of everything that determines the answer (used for replay fixtures)."""
        payload = {
            "model": self.model,
            "system": self.system,
            "prompt": self.prompt,
            "schema": self.schema,
            "images": [hashlib.sha256(i).hexdigest() for i in self.images],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = field(default=Decimal("0"))  # local models cost nothing per call


class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...
