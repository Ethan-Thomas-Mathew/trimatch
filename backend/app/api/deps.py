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

_basic = HTTPBasic(realm="TriMatch reviewer")


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
    credentials: Annotated[HTTPBasicCredentials, Depends(_basic)],
    settings: Annotated[Settings, Depends(current_settings)],
) -> str:
    """The single demo reviewer. Constant-time comparison; never reveals which part was wrong."""
    expected_password: str = request.app.state.reviewer_password
    user_ok = secrets.compare_digest(
        credentials.username.encode(), settings.reviewer_username.encode()
    )
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_password.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid credentials.",
            headers={"WWW-Authenticate": 'Basic realm="TriMatch reviewer"'},
        )
    return settings.reviewer_username


SessionDep = Annotated[Session, Depends(get_session)]
ReviewerDep = Annotated[str, Depends(require_reviewer)]
SettingsDep = Annotated[Settings, Depends(current_settings)]
ProviderDep = Annotated[LLMProvider, Depends(get_provider)]
