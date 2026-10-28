"""Structured logging.

* Every record carries the active context (request id, user, job, assignment, profile, task) via
  ``bind(...)`` - a context variable, so it follows a request or job through the code, including
  into worker threads that call ``bind`` themselves.
* Output: readable console lines in development (``LOG_FORMAT=pretty``) or JSON lines
  (``LOG_FORMAT=json``), plus rotating JSON files ``LOG_DIR/app.jsonl`` (everything) and
  ``LOG_DIR/errors.jsonl`` (warnings and errors).
* Privacy: values of sensitive fields (passwords, tokens, keys, document/answer/transcript text) are
  redacted by a filter, so a careless ``extra=`` can never leak them.
"""

import json
import logging
import logging.handlers
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

_context: ContextVar[dict | None] = ContextVar("log_context", default=None)

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "authorization",
    "cookie",
    "api_key",
    "secret",
    "secret_key",
    "aws_bearer_token_bedrock",
    "content",
    "text",
    "answer",
    "transcript",
    "question",
    "prompt",
}
REDACTED = "[redacted]"


def current_context() -> dict:
    return dict(_context.get() or {})


@contextmanager
def bind(**fields):
    """Add fields (e.g. job_id=...) to every log record emitted inside the block."""
    merged = {**(_context.get() or {}), **{k: v for k, v in fields.items() if v is not None}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def bind_into(**fields) -> None:
    """Add fields to the current context without a block (e.g. the user id once auth resolves)."""
    _context.set({**(_context.get() or {}), **{k: v for k, v in fields.items() if v is not None}})


_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class ContextFilter(logging.Filter):
    """Attach the bound context and redact sensitive extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        for k, v in (_context.get() or {}).items():
            if not hasattr(record, k):
                setattr(record, k, v)
        for k in list(vars(record)):
            if k in SENSITIVE_KEYS and k not in _RESERVED:
                setattr(record, k, REDACTED)
        return True


def _extras(record: logging.LogRecord) -> dict:
    # Empty values (None) are omitted so lines show only what is known.
    return {k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_") and v is not None}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    """``12:03:04 INFO  worker   job finished  [job=3f2a… type=render] seconds=1.2 pages=2``"""

    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[37m",
        "INFO": "\033[36m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    CTX = ("request_id", "user_id", "job_id", "job_type", "assignment_id", "profile_id", "task")

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        lvl = record.levelname
        if self.color:
            lvl = f"{self.COLORS.get(lvl, '')}{lvl:<5}\033[0m"
        extras = _extras(record)
        ctx = " ".join(f"{k.replace('_id', '')}={str(extras.pop(k))[:8]}" for k in self.CTX if k in extras)
        rest = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{ts} {lvl:<5} {record.name.removeprefix('app.'):<12} {record.getMessage()}"
        if ctx:
            line += f"  [{ctx}]"
        if rest:
            line += f"  {rest}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "pretty", log_dir: str | Path | None = None) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    ctx = ContextFilter()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(JsonFormatter() if fmt == "json" else PrettyFormatter(color=sys.stdout.isatty()))
    console.addFilter(ctx)
    root.addHandler(console)
    if log_dir:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        for name, lvl in (("app.jsonl", logging.DEBUG), ("errors.jsonl", logging.WARNING)):
            fh = logging.handlers.RotatingFileHandler(
                d / name, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setLevel(lvl)
            fh.setFormatter(JsonFormatter())
            fh.addFilter(ctx)
            root.addHandler(fh)
    root.setLevel(level.upper())
    for noisy in (
        "pdfminer",
        "httpx",
        "httpx2",
        "httpcore",
        "PIL",
        "anthropic",
        "botocore",
        "urllib3",
        "uvicorn.access",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Timer:
    """``with Timer() as t: ...; t.ms`` - small helper for stage timings."""

    def __enter__(self):
        import time

        self._t0 = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc):
        import time

        self.ms = round((time.perf_counter() - self._t0) * 1000)
        return False
