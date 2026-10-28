"""FastAPI application. OpenAPI docs at /api/docs."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import assignments, auth, profiles, signature, system, usage
from app.config import get_settings
from app.db import Base, get_engine
from app.errors import install_error_handlers
from app.logging_setup import bind, configure_logging

log = logging.getLogger("app.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    fmt = s.log_format if s.log_format != "auto" else ("pretty" if s.app_env == "development" else "json")
    log_dir = s.log_dir if s.log_dir and str(s.log_dir).strip() not in ("", ".") else None  # LOG_DIR= disables files
    configure_logging(s.log_level, fmt, log_dir)
    _log_startup(s)
    if s.app_env == "production" and s.secret_key.startswith("dev-insecure"):
        raise RuntimeError("SECRET_KEY must be set in production")
    if s.is_sqlite:
        # Local development convenience: apply migrations on start (PostgreSQL deployments run
        # `alembic upgrade head` explicitly, see the Dockerfile).
        _migrate_sqlite()
    stop = None
    if s.run_worker_in_process:
        from app.jobs.worker import start_in_process

        stop = start_in_process()
    yield
    if stop is not None:
        stop.set()


def _migrate_sqlite() -> None:
    from pathlib import Path

    from sqlalchemy import inspect

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini.is_file():
        Base.metadata.create_all(get_engine())
        return
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    insp = inspect(get_engine())
    tables = set(insp.get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        # Created without migrations (create_all): record which schema it already matches, then upgrade.
        job_cols = {c["name"] for c in insp.get_columns("processing_jobs")} if "processing_jobs" in tables else set()
        user_cols = {c["name"] for c in insp.get_columns("users")}
        if "rate_limit_hits" in tables:
            current = "0006"
        elif "model_routing" in user_cols:
            current = "0005"
        elif "answer_preferences" in user_cols:
            current = "0004"
        elif "events" in job_cols:
            current = "0003"
        elif "llm_usage" in tables:
            current = "0002"
        else:
            current = "0001"
        command.stamp(cfg, current)
    command.upgrade(cfg, "head")
    logging.getLogger("app.startup").info("database migrated", extra={"revision": "head"})


def _log_startup(s) -> None:
    """One line describing how this instance is configured (no secrets)."""
    from app.ai.providers import provider_status
    from app.ingestion.ocr import get_ocr
    from app.render.fonts import glyph_fallback_fonts

    try:
        ocr = get_ocr()
        ocr_name = ocr.name if ocr else "unavailable"
    except Exception:
        ocr_name = "error"
    ai = provider_status()
    logging.getLogger("app.startup").info(
        "AssignmentHand started",
        extra={
            "env": s.app_env,
            "database": s.database_url.split(":", 1)[0],
            "storage": f"{s.storage_backend}:{s.storage_root}",
            "worker_in_process": s.run_worker_in_process,
            "ai_provider": ai["provider"],
            "ai_configured": ai["configured"],
            "routing": ai["routing"],
            "adaptive_effort": ai["adaptive_effort"],
            "budget_usd": s.llm_monthly_budget_usd,
            "ocr": ocr_name,
            "fallback_fonts": len(glyph_fallback_fonts()),
            "log_dir": str(s.log_dir) if s.log_dir else None,
        },
    )


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
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:16]
        start = time.monotonic()
        with bind(request_id=rid):
            try:
                response = await call_next(request)
            except Exception:
                log.exception("request crashed", extra={"method": request.method, "path": request.url.path})
                raise
            ms = round((time.monotonic() - start) * 1000)
            response.headers["X-Request-Id"] = rid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            path = request.url.path
            if path.endswith("/health") or path.endswith("/health/ready") or path.endswith(".png"):
                level = logging.DEBUG  # high-volume, low-value
            elif response.status_code >= 500:
                level = logging.ERROR
            elif response.status_code >= 400 or ms >= get_settings().log_slow_request_ms:
                level = logging.WARNING
            else:
                level = logging.INFO
            # Paths contain only opaque ids; request/response bodies (documents, answers) are never logged.
            log.log(
                level,
                f"{request.method} {path} -> {response.status_code} in {ms} ms",
                extra={
                    "method": request.method,
                    "path": path,
                    "query": request.url.query[:200] or None,
                    "status": response.status_code,
                    "ms": ms,
                    "slow": ms >= get_settings().log_slow_request_ms or None,
                    "user_id": getattr(request.state, "user_id", None),
                    "client": request.client.host if request.client else None,
                    "bytes": response.headers.get("content-length"),
                },
            )
            return response

    install_error_handlers(app)
    for r in (system.router, auth.router, profiles.router, assignments.router, usage.router, signature.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
