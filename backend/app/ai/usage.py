"""Per-call AI usage accounting.

Callers declare what the work is for with ``usage_scope(...)``; every provider call made inside the
scope (including retries and escalations) is written to ``llm_usage`` with tokens, latency, outcome
and cost. Recording never breaks the calling work: failures to write are logged and ignored.
Scopes are context variables, so wrap work submitted to thread pools inside the thread.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from app.ai.pricing import cost, price_for

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageScope:
    user_id: str
    task: str  # answer | transcribe | ocr | verify
    assignment_id: str | None = None
    profile_id: str | None = None
    question_id: str | None = None


_scope: ContextVar[UsageScope | None] = ContextVar("llm_usage_scope", default=None)


@contextmanager
def usage_scope(user_id: str, task: str, *, assignment_id=None, profile_id=None, question_id=None):
    token = _scope.set(UsageScope(user_id, task, assignment_id, profile_id, question_id))
    try:
        yield
    finally:
        _scope.reset(token)


def record(
    *,
    provider: str,
    model: str,
    served_model: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    status: str,
    error_type: str | None = None,
) -> None:
    scope = _scope.get()
    p = price_for(model) or price_for(served_model or "")
    c = cost(model if price_for(model) else (served_model or model), input_tokens, output_tokens) if p else None
    log.log(
        logging.INFO if status == "ok" else logging.WARNING,
        f"ai call {status}: {scope.task if scope else '?'} via {served_model or model}",
        extra={
            "task": scope.task if scope else None,
            "provider": provider,
            "model": model,
            "served_model": served_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost_usd": c,
            "pricing_source": p.source if p else "unknown",
            "error_type": error_type,
            "question_id": scope.question_id if scope else None,
        },
    )
    if scope is None:
        return  # calls outside a declared scope (tests, scripts) are not attributed to anyone
    try:
        from app.db import session_factory
        from app.models import LLMUsage

        priced_model = served_model or model
        p = price_for(model) or price_for(priced_model)
        with session_factory()() as db:
            db.add(
                LLMUsage(
                    user_id=scope.user_id,
                    assignment_id=scope.assignment_id,
                    profile_id=scope.profile_id,
                    question_id=scope.question_id,
                    task=scope.task,
                    provider=provider,
                    model=model,
                    served_model=served_model,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    latency_ms=int(latency_ms),
                    status=status,
                    error_type=error_type,
                    cost_usd=cost(model if price_for(model) else priced_model, input_tokens, output_tokens)
                    if p
                    else None,
                    pricing_source=p.source if p else "unknown",
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()
    except Exception:
        log.warning("could not record AI usage", exc_info=True)
