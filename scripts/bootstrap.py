"""Container start-up: apply migrations, then optionally load the demo data.

Usage: python scripts/bootstrap.py

  SEED_DEMO_DATA=true   load vendors, purchase orders and goods receipts, but only if the
                        database has no vendors yet (it never wipes an existing database).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Vendor
from app.db.session import get_engine
from seed import seed
from world import build_world

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = next(
    p for p in (ROOT / "alembic.ini", ROOT / "backend" / "alembic.ini") if p.exists()
)


def main() -> int:
    settings = get_settings()
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    print("migrations applied")

    if os.environ.get("SEED_DEMO_DATA", "").lower() == "true":
        with Session(get_engine()) as session, session.begin():
            if session.scalar(select(func.count()).select_from(Vendor)):
                print("database already has vendors; demo data not loaded")
            else:
                counts = seed(session, build_world())
                print("demo data loaded: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
