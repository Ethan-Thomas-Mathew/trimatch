"""PDF to text, or to page images when there is no usable text layer."""

from __future__ import annotations

import io
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium


class PdfError(Exception):
    """The file is not a readable PDF."""


def extract_text(pdf_bytes: bytes) -> str:
    """Text of all pages in reading order. Empty for image-only (scanned) PDFs."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages).strip()
    except Exception as exc:  # pdfminer raises many different types on malformed input
        raise PdfError(f"could not read PDF: {exc}") from exc


def render_pages(pdf_bytes: bytes, max_pages: int, dpi: int = 150) -> list[bytes]:
    """PNG bytes of the first ``max_pages`` pages, for a vision model."""
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise PdfError(f"could not render PDF: {exc}") from exc
    try:
        images: list[bytes] = []
        for index in range(min(len(doc), max_pages)):
            buffer = io.BytesIO()
            doc[index].render(scale=dpi / 72).to_pil().convert("RGB").save(buffer, "PNG")
            images.append(buffer.getvalue())
        return images
    finally:
        doc.close()


def read_pdf(path: Path) -> bytes:
    return path.read_bytes()
