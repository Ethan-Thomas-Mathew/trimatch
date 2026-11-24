"""FastAPI application: health checks, the reviewer API, structured logging and security headers."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.config import get_settings
from app.db.session import get_engine
from app.logging_config import configure_logging, request_id_var

log = logging.getLogger("trimatch.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    password = settings.reviewer_password
    if not password:
        # Secure by default: no built-in password. A random one is generated per start.
        password = secrets.token_urlsafe(12)
        log.warning(
            "REVIEWER_PASSWORD is not set; generated a one-time password for this run",
            extra={"username": settings.reviewer_username, "generated_password": password},
        )
    app.state.reviewer_password = password
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="TriMatch", version="0.1.0", lifespan=lifespan)

    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        started = time.perf_counter()
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        response.headers["X-Request-ID"] = rid
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Needs no database and no login."""
        return {"status": "ok"}

    @app.get("/ready")
    def ready(response: Response) -> dict[str, str]:
        """Readiness: the database answers."""
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            return {"status": "database unavailable"}
        return {"status": "ready"}

    app.include_router(router)
    return app


app = create_app()
