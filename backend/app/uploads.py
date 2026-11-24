"""Upload validation and storage.

Untrusted input rules: never use the client's filename on disk, never trust the declared
content type, enforce a size limit, and require the PDF magic bytes. Files are stored under a
random name inside ``UPLOAD_DIR`` and served only through that same directory.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path


class UploadError(ValueError):
    """The upload is unacceptable. ``status`` is the HTTP status the API should return."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


_STORED_NAME = re.compile(r"^[0-9a-f]{32}\.pdf$")


def validate_pdf(content: bytes, max_mb: int) -> None:
    if not content:
        raise UploadError("The file is empty.")
    if len(content) > max_mb * 1024 * 1024:
        raise UploadError(f"The file is larger than the {max_mb} MB limit.", status=413)
    if not content.startswith(b"%PDF-"):
        raise UploadError("Only PDF files are accepted.", status=415)


def safe_display_name(name: str | None) -> str:
    """For the audit log only: strip path parts and odd characters, cap the length."""
    base = (name or "unnamed").replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:100] or "unnamed"


def save_upload(content: bytes, upload_dir: str) -> tuple[str, str]:
    """Store the bytes and return ``(stored_filename, sha256)``."""
    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.pdf"
    (directory / stored).write_bytes(content)
    return stored, hashlib.sha256(content).hexdigest()


def resolve_upload(upload_dir: str, stored_name: str) -> Path:
    """Map a stored name back to a path, refusing anything that is not one of our names."""
    if not _STORED_NAME.match(stored_name):
        raise UploadError("Unknown file.", status=404)
    path = (Path(upload_dir) / stored_name).resolve()
    if path.parent != Path(upload_dir).resolve() or not path.is_file():
        raise UploadError("Unknown file.", status=404)
    return path
