"""PDF to validated ``InvoiceExtraction`` (build plan Phase 2).

Digital PDFs: pdfplumber text goes to the text model.
Scanned PDFs (too little text): page images go to the vision model.
If the model's JSON fails validation, retry once with the error passed back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import ValidationError

from app.config import Settings
from app.extraction import pdf
from app.extraction.prompts import (
    RETRY_SUFFIX,
    SYSTEM_PROMPT,
    VISION_PROMPT,
    build_text_prompt,
)
from app.llm.base import LLMProvider, LLMRequest
from app.schemas.extraction import InvoiceExtraction, llm_json_schema

Mode = Literal["text", "vision"]


class ExtractionFailedError(Exception):
    """The model never produced valid output. The invoice must go to a human."""


@dataclass(frozen=True)
class ExtractionResult:
    extraction: InvoiceExtraction
    mode: Mode
    model: str
    attempts: int
    raw_output: dict[str, Any]
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: Decimal


def choose_mode(text: str, min_chars: int) -> Mode:
    """Scans have no text layer; a stray watermark or two words is not enough to extract from."""
    return "text" if len("".join(text.split())) >= min_chars else "vision"


def extract_invoice(
    pdf_bytes: bytes, provider: LLMProvider, settings: Settings
) -> ExtractionResult:
    text = pdf.extract_text(pdf_bytes)
    mode = choose_mode(text, settings.min_text_chars)

    if mode == "text":
        model = settings.ollama_text_model
        prompt = build_text_prompt(text)
        images: tuple[bytes, ...] = ()
    else:
        model = settings.ollama_vision_model
        prompt = VISION_PROMPT
        images = tuple(pdf.render_pages(pdf_bytes, settings.max_vision_pages))

    schema = llm_json_schema()
    tokens_in = tokens_out = latency_ms = 0
    cost = Decimal("0")
    error_text = ""
    previous = ""

    for attempt in (1, 2):
        user_prompt = prompt
        if attempt == 2:
            user_prompt += RETRY_SUFFIX.format(previous=previous[:4000], error=error_text[:2000])
        response = provider.generate(
            LLMRequest(
                model=model, system=SYSTEM_PROMPT, prompt=user_prompt, schema=schema, images=images
            )
        )
        tokens_in += response.tokens_in
        tokens_out += response.tokens_out
        latency_ms += response.latency_ms
        cost += response.cost_usd
        previous = response.text
        try:
            raw = json.loads(response.text)
            extraction = InvoiceExtraction.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            error_text = str(exc)
            continue
        return ExtractionResult(
            extraction=extraction,
            mode=mode,
            model=model,
            attempts=attempt,
            raw_output=raw,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    raise ExtractionFailedError(f"invalid output after 2 attempts: {error_text[:500]}")
