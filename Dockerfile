# syntax=docker/dockerfile:1.7
# One image, several roles (D-004): api (default CMD), worker, migrate and seed (compose overrides the command).

# ---------- build: install dependencies from the lockfile
FROM python:3.15-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# dependencies first: this layer is cached until pyproject.toml / uv.lock change
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY workers ./workers
COPY simulation ./simulation
COPY migrations ./migrations
COPY config ./config
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# ---------- dev/test image: adds dev dependencies and the test suite (compose profile "test")
FROM builder AS dev
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
COPY reports ./reports
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["pytest", "-q"]

# ---------- runtime: no build tools, non-root
FROM python:3.15-slim AS runtime
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app \
 && mkdir -p /data/documents /data/bank && chown -R app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "20"]
