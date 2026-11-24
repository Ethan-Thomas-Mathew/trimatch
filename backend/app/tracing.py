"""Tracing: every LLM call and tool call is recorded as an event (build plan section 5).

Events are written to ``audit_log`` so a reviewer can read exactly what the agent did. The
``Tracer`` interface is the seam where a Langfuse exporter would plug in; that exporter is not
implemented yet (see docs/DECISIONS.md D-022).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def jsonable(value: Any) -> Any:
    """Round-trip through JSON so Decimals, dates and enums become plain JSON values."""
    return json.loads(json.dumps(value, default=_default))


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def audit(
    session: Session,
    entity: str,
    entity_id: int | None,
    action: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            entity=entity,
            entity_id=entity_id,
            action=action,
            actor=actor,
            payload=jsonable(payload or {}),
        )
    )


class Tracer(Protocol):
    def event(self, name: str, actor: str, payload: dict[str, Any]) -> None: ...


class NullTracer:
    def event(self, name: str, actor: str, payload: dict[str, Any]) -> None:
        return None


class AuditTracer:
    """Writes each event as an ``audit_log`` row for one invoice."""

    def __init__(self, session: Session, invoice_id: int) -> None:
        self._session = session
        self._invoice_id = invoice_id

    def event(self, name: str, actor: str, payload: dict[str, Any]) -> None:
        audit(self._session, "invoice", self._invoice_id, name, actor, payload)
