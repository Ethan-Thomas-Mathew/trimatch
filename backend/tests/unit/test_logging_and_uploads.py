"""Structured logging and upload handling (Phase 7 hardening)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.logging_config import JsonFormatter, request_id_var
from app.uploads import (
    UploadError,
    resolve_upload,
    safe_display_name,
    save_upload,
    validate_pdf,
)


# ------------------------------------------------------------------- JSON logging
def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("trimatch.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_log_lines_are_single_json_objects_with_the_expected_fields() -> None:
    line = JsonFormatter().format(_record("processed", invoice_id=7))
    data = json.loads(line)
    assert "\n" not in line
    assert data["message"] == "processed"
    assert data["level"] == "INFO"
    assert data["logger"] == "trimatch.test"
    assert data["invoice_id"] == 7  # fields passed with extra= are kept
    assert data["ts"].endswith("+00:00")


def test_the_request_id_is_attached_to_every_line() -> None:
    token = request_id_var.set("abc123")
    try:
        assert json.loads(JsonFormatter().format(_record()))["request_id"] == "abc123"
    finally:
        request_id_var.reset(token)


def test_exceptions_are_logged_as_a_field_not_as_extra_lines() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    line = JsonFormatter().format(record)
    assert "\n" not in line.replace("\\n", "")
    assert "ValueError: boom" in json.loads(line)["exception"]


def test_unserialisable_extras_do_not_break_logging() -> None:
    line = JsonFormatter().format(_record(thing=object()))
    assert "thing" in json.loads(line)


# ----------------------------------------------------------------------- uploads
def test_validate_pdf_rules() -> None:
    validate_pdf(b"%PDF-1.7 ...", 10)  # accepted
    for content, status in ((b"", 400), (b"GIF89a", 415), (b"%PD", 415)):
        with pytest.raises(UploadError) as exc:
            validate_pdf(content, 10)
        assert exc.value.status == status
    with pytest.raises(UploadError) as big:
        validate_pdf(b"%PDF-" + b"0" * (2 * 1024 * 1024), 1)
    assert big.value.status == 413


def test_size_limit_is_inclusive_of_the_limit_itself() -> None:
    validate_pdf(b"%PDF-" + b"0" * (1024 * 1024 - 5), 1)  # exactly 1 MB passes
    with pytest.raises(UploadError):
        validate_pdf(b"%PDF-" + b"0" * (1024 * 1024 - 4), 1)  # one byte over


def test_stored_files_get_random_names_and_can_be_read_back(tmp_path: Path) -> None:
    stored, digest = save_upload(b"%PDF-1.4 abc", str(tmp_path))
    assert len(stored) == 36
    assert stored.endswith(".pdf")
    assert len(digest) == 64
    assert resolve_upload(str(tmp_path), stored).read_bytes() == b"%PDF-1.4 abc"


@pytest.mark.parametrize(
    "name",
    [
        "../secret.pdf",
        "..\\secret.pdf",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "a" * 32 + ".pdf/../x",
        "NOTHEX" + "0" * 26 + ".pdf",
        ("0" * 32) + ".exe",
        "",
        ("0" * 32) + ".pdf",  # well-formed but does not exist
    ],
)
def test_only_our_own_stored_names_resolve(tmp_path: Path, name: str) -> None:
    with pytest.raises(UploadError) as exc:
        resolve_upload(str(tmp_path), name)
    assert exc.value.status == 404


def test_a_valid_looking_name_outside_the_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = tmp_path / "uploads"
    inside.mkdir()
    name = ("a" * 32) + ".pdf"
    (outside / name).write_bytes(b"%PDF-")
    with pytest.raises(UploadError):
        resolve_upload(str(inside), name)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("invoice.pdf", "invoice.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\inv 1.pdf", "inv 1.pdf"),
        ("a;rm -rf *.pdf", "a_rm -rf _.pdf"),
        (None, "unnamed"),
        ("", "unnamed"),
        ("x" * 500, "x" * 100),
    ],
)
def test_display_names_are_sanitised(given: str | None, expected: str) -> None:
    assert safe_display_name(given) == expected
