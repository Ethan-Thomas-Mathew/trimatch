"""Shared API dependencies: database session, LLM provider, and reviewer authentication."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.llm.base import LLMProvider
from app.llm.factory import build_provider

# auto_error=False: we build the 401 ourselves so we can decide whether to send the challenge.
_basic = HTTPBasic(realm="TriMatch reviewer", auto_error=False)

# The web UI marks its requests with this header. For those, a 401 must NOT carry the
# WWW-Authenticate challenge, or the browser would open its own native login box on top of
# the app's sign-in form. Plain API clients (curl, scripts) still get the standard challenge.
UI_MARKER_HEADER = "x-requested-with"
UI_MARKER_VALUE = "TriMatch-UI"


def get_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_provider() -> LLMProvider:
    return build_provider(get_settings())


def current_settings() -> Settings:
    return get_settings()


def require_reviewer(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    settings: Annotated[Settings, Depends(current_settings)],
) -> str:
    """The single demo reviewer. Constant-time comparison; never reveals which part was wrong."""
    expected_password: str = request.app.state.reviewer_password
    # Always run both comparisons, even with no credentials, so timing does not differ.
    given_user = credentials.username if credentials else ""
    given_password = credentials.password if credentials else ""
    user_ok = secrets.compare_digest(given_user.encode(), settings.reviewer_username.encode())
    pass_ok = secrets.compare_digest(given_password.encode(), expected_password.encode())
    if credentials is None or not (user_ok and pass_ok):
        from_ui = request.headers.get(UI_MARKER_HEADER) == UI_MARKER_VALUE
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid credentials.",
            headers=None if from_ui else {"WWW-Authenticate": 'Basic realm="TriMatch reviewer"'},
        )
    return settings.reviewer_username


SessionDep = Annotated[Session, Depends(get_session)]
ReviewerDep = Annotated[str, Depends(require_reviewer)]
SettingsDep = Annotated[Settings, Depends(current_settings)]
ProviderDep = Annotated[LLMProvider, Depends(get_provider)]
