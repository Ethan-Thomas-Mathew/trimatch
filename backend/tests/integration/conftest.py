"""Integration fixtures: a throwaway Postgres database migrated with Alembic.

Needs the Postgres from docker-compose (``make up``). Locally the tests skip with a clear
message if it is unreachable; in CI (``CI=true``) an unreachable database is a failure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def admin_engine() -> Iterator[Engine]:
    url = make_url(get_settings().database_url).set(database="postgres")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        if os.environ.get("CI"):
            pytest.fail(f"Postgres is required in CI but unreachable: {exc}")
        pytest.skip("Postgres not reachable; run `make up` to enable integration tests")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def database_url(admin_engine: Engine) -> Iterator[str]:
    """A fresh, empty database that is dropped after the session."""
    name = f"trimatch_test_{uuid.uuid4().hex[:8]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(get_settings().database_url).set(database=name)
    yield url.render_as_string(hide_password=False)
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(database_url), "head")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()
