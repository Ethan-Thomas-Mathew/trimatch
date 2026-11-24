# Build context is the repository root:  docker build -f backend/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    UPLOAD_DIR=/data/uploads

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so this layer is cached until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

# Run as an unprivileged user; uploads live on a volume it owns.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data /app
USER app
ENV PYTHONPATH=/app/scripts

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["sh", "-c", "python scripts/bootstrap.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
