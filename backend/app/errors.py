"""Structured API errors. Every error response has the shape
``{"error": {"code": str, "message": str, "details": any}}``."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, details=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMedia(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class ConfigurationError(AppError):
    status_code = 503
    code = "not_configured"


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(req: Request, exc: AppError):
        # Messages of AppError are written for users and contain no document content.
        log.log(
            logging.ERROR if exc.status_code >= 500 else logging.INFO,
            f"{exc.code}: {exc.message}",
            extra={"error_code": exc.code, "status": exc.status_code, "path": req.url.path},
        )
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, exc: RequestValidationError):
        log.info(
            "validation error",
            extra={"path": req.url.path, "fields": [".".join(map(str, e.get("loc", []))) for e in exc.errors()][:10]},
        )
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Invalid request", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        return JSONResponse(_body("http_error", str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception):
        # Never echo internal details (they may contain document content) to the client.
        log.exception("unhandled error on %s %s", req.method, req.url.path)
        return JSONResponse(_body("internal_error", "An unexpected error occurred"), status_code=500)
