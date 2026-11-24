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


# --------------------------------------------------------------------------------------
# Shared environment for pipeline and API tests: a migrated + seeded throwaway database,
# the synthetic dataset, and the application pointed at that database.
# --------------------------------------------------------------------------------------
import json  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402


@dataclass
class Env:
    settings: Any
    dataset: Path
    engine: Engine


def clear_caches() -> None:
    from app.db.session import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture(scope="module")
def env(
    migrated_engine: Engine, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Env]:
    from generate_dataset import generate
    from seed import run as seed_run
    from world import DEFAULT_SEED, build_world

    seed_run(migrated_engine)
    root = tmp_path_factory.mktemp("env")
    dataset = root / "dataset"
    generate(build_world(DEFAULT_SEED), dataset)

    patch = pytest.MonkeyPatch()
    patch.setenv("DATABASE_URL", database_url)
    patch.setenv("UPLOAD_DIR", str(root / "uploads"))
    patch.setenv("LLM_PROVIDER", "replay")
    patch.setenv("REVIEWER_PASSWORD", "test-password")
    clear_caches()
    yield Env(get_settings(), dataset, migrated_engine)
    patch.undo()
    clear_caches()


def forget(env: Env, invoice_number: str) -> None:
    """Remove earlier copies of a dataset invoice so a test can process the same PDF again
    without the pipeline (correctly) rejecting it as a duplicate of itself."""
    with Session(env.engine) as s, s.begin():
        ids = "SELECT id FROM invoices WHERE invoice_number = :n AND file_path IS NOT NULL"
        for table in ("invoice_lines", "extraction_runs", "match_results", "decisions",
                      "review_actions"):  # fmt: skip
            s.execute(
                text(f"DELETE FROM {table} WHERE invoice_id IN ({ids})"), {"n": invoice_number}
            )
        s.execute(
            text("DELETE FROM invoices WHERE invoice_number = :n AND file_path IS NOT NULL"),
            {"n": invoice_number},
        )


def dataset_invoice(env: Env, **where: Any) -> tuple[bytes, dict[str, Any]]:
    """The first dataset invoice whose ground truth matches every ``where`` key; its earlier
    copies are cleared first."""
    for path in sorted((env.dataset / "ground_truth").glob("*.json")):
        truth = json.loads(path.read_text("utf-8"))
        if all(truth.get(k) == v for k, v in where.items()):
            forget(env, truth["fields"]["invoice_number"])
            return (env.dataset / truth["pdf"]).read_bytes(), truth
    raise AssertionError(f"no dataset invoice matches {where}")
