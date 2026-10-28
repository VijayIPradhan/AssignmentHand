from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.budget import budget_status
from app.ai.pricing import LIST_PRICES, price_for
from app.ai.providers import TASKS, provider_status, task_model
from app.db import get_db
from app.models import Assignment, LLMUsage, User
from app.security import current_user

router = APIRouter(prefix="/usage", tags=["usage"])


def _agg(rows) -> dict:
    calls = len(rows)
    priced = [r for r in rows if r.cost_usd is not None]
    return {
        "calls": calls,
        "errors": sum(1 for r in rows if r.status != "ok"),
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "cost_usd": round(sum(r.cost_usd for r in priced), 6),
        "unpriced_calls": calls - len(priced),
        "estimated": any(r.pricing_source == "list_price_estimate" for r in priced),
        "avg_latency_ms": round(sum(r.latency_ms for r in rows) / calls) if calls else 0,
    }


def _group(rows, key) -> list[dict]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    out = [{"key": k, **_agg(v)} for k, v in groups.items()]
    return sorted(out, key=lambda g: (-g["cost_usd"], -g["calls"]))


@router.get("")
def usage_summary(
    days: int = Query(30, ge=1, le=366),
    recent: int = Query(50, ge=0, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """AI usage for the signed-in user: totals, breakdowns by model/task/day/assignment, recent calls,
    pricing in effect and the monthly budget. Costs for cloud (e.g. Bedrock) model ids priced from
    Anthropic list prices are flagged ``estimated``."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(
        select(LLMUsage).where(LLMUsage.user_id == user.id, LLMUsage.created_at >= since).order_by(LLMUsage.created_at)
    ).all()
    titles = dict(db.execute(select(Assignment.id, Assignment.title).where(Assignment.user_id == user.id)).all())
    by_assignment = [g for g in _group(rows, lambda r: r.assignment_id) if g["key"]]
    for g in by_assignment:
        g["title"] = titles.get(g["key"], "(deleted assignment)")
    models = sorted({r.model for r in rows} | {task_model(t, user.model_routing) for t in TASKS})
    pricing = []
    for m in models:
        p = price_for(m)
        pricing.append(
            {
                "model": m,
                "input_per_mtok": p.input_per_mtok if p else None,
                "output_per_mtok": p.output_per_mtok if p else None,
                "source": p.source if p else "unknown",
            }
        )
    all_time = db.execute(
        select(func.count(LLMUsage.id), func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
            LLMUsage.user_id == user.id
        )
    ).one()
    return {
        "period_days": days,
        "totals": _agg(rows),
        "all_time": {"calls": int(all_time[0]), "cost_usd": round(float(all_time[1]), 6)},
        "by_model": _group(rows, lambda r: r.served_model or r.model),
        "by_task": _group(rows, lambda r: r.task),
        "by_day": sorted(_group(rows, lambda r: r.created_at.date().isoformat()), key=lambda g: g["key"]),
        "by_assignment": by_assignment,
        "recent": [
            {
                "at": r.created_at.isoformat(),
                "task": r.task,
                "model": r.model,
                "served_model": r.served_model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "latency_ms": r.latency_ms,
                "status": r.status,
                "error_type": r.error_type,
                "cost_usd": r.cost_usd,
                "pricing_source": r.pricing_source,
                "assignment_id": r.assignment_id,
                "assignment_title": titles.get(r.assignment_id) if r.assignment_id else None,
                "profile_id": r.profile_id,
            }
            for r in reversed(rows[-recent:] if recent else [])
        ],
        "pricing": pricing,
        "list_prices": {k: {"input_per_mtok": v[0], "output_per_mtok": v[1]} for k, v in LIST_PRICES.items()},
        "routing": provider_status(user.model_routing)["routing"],
        "budget": budget_status(db, user.id),
    }
