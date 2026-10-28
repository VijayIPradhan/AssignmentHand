"""Monthly AI spending limit per user (``LLM_MONTHLY_BUDGET_USD``)."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import LLMUsage


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend(db: Session, user_id: str) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
            LLMUsage.user_id == user_id, LLMUsage.created_at >= month_start()
        )
    )
    return float(total or 0.0)


def budget_status(db: Session, user_id: str) -> dict:
    limit = get_settings().llm_monthly_budget_usd
    spent = round(month_spend(db, user_id), 4)
    if not limit:
        return {"limit_usd": None, "spent_usd": spent, "remaining_usd": None, "percent": None, "exceeded": False}
    return {
        "limit_usd": limit,
        "spent_usd": spent,
        "remaining_usd": round(max(0.0, limit - spent), 4),
        "percent": round(100 * spent / limit, 1),
        "exceeded": spent >= limit,
        "warning": spent >= 0.8 * limit,
    }


def enforce_budget(db: Session, user_id: str) -> None:
    st = budget_status(db, user_id)
    if st["exceeded"]:
        raise AppError(
            f"Monthly AI budget of ${st['limit_usd']:.2f} reached (spent ${st['spent_usd']:.2f}). "
            "Raise LLM_MONTHLY_BUDGET_USD or wait until next month; you can still edit answers yourself.",
            code="budget_exceeded",
            status_code=402,
            details=st,
        )
