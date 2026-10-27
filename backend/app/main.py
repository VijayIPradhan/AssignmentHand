"""FastAPI application. OpenAPI docs at /api/docs."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import assignments, auth, profiles, system
from app.config import get_settings
from app.db import Base, get_engine
from app.errors import install_error_handlers
from app.logging_setup import configure_logging

log = logging.getLogger("app.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    if s.app_env == "production" and s.secret_key.startswith("dev-insecure"):
        raise RuntimeError("SECRET_KEY must be set in production")
    if s.is_sqlite:
        # Local development convenience; PostgreSQL deployments use Alembic migrations.
        Base.metadata.create_all(get_engine())
    stop = None
    if s.run_worker_in_process:
        from app.jobs.worker import start_in_process

        stop = start_in_process()
    yield
    if stop is not None:
        stop.set()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="AssignmentHand API",
        version="1.0.0",
        description="Handwriting profiles, assignment extraction, AI answers and handwritten PDF rendering.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Render-Warnings", "X-Request-Id"],
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # Paths contain only opaque ids; bodies (document content) are never logged.
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.monotonic() - start) * 1000),
            },
        )
        return response

    install_error_handlers(app)
    for r in (system.router, auth.router, profiles.router, assignments.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
