"""FastAPI entrypoint. Real routers arrive in Phase 6; Phase 0 only exposes a health check."""

from fastapi import FastAPI

app = FastAPI(title="TriMatch", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
