"""FastAPI application factory.

uvicorn app.main:app            (production / compose)
create_app(container=...)       (tests: inject fakes, no network)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.errors import install_error_handlers
from app.api.v1 import alerts, audit, auth, documents, health, investigations
from app.core.container import Container
from app.core.settings import Settings, get_settings
from app.infrastructure.observability.logging import configure_logging, get_logger
from app.infrastructure.observability.middleware import RequestContextMiddleware
from app.infrastructure.observability.tracing import configure_tracing

log = get_logger("app")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None  # only close what we created
        app.state.container = container or Container.build(settings)
        log.info("app.startup", env=settings.env, llm_provider=settings.llm_provider)
        try:
            yield
        finally:
            if owned:
                await app.state.container.aclose()
            log.info("app.shutdown")

    app = FastAPI(
        title="Fraud Ring Investigator API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "prod" else None,
        redoc_url=None,
    )
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:  # same-origin via the nginx proxy in compose; CORS only for local frontend dev
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )
    install_error_handlers(app)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        # Not under /api, so the nginx proxy never exposes it publicly; scrape it on the internal network.
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    v1 = APIRouter(prefix="/api/v1")
    for r in (health.router, auth.router, alerts.router, investigations.router, documents.router, audit.router):
        v1.include_router(r)
    app.include_router(v1)
    configure_tracing(settings, service_name="fri-api")
    FastAPIInstrumentor.instrument_app(app, excluded_urls="api/v1/health")
    return app


def __getattr__(name: str) -> FastAPI:
    # Lazy module attribute so `import app.main` (e.g. in tests) doesn't require env vars;
    # `uvicorn app.main:app` still works.
    if name == "app":
        return create_app()
    raise AttributeError(name)
