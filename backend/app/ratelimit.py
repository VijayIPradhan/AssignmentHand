"""Sliding-window rate limiter for expensive and sensitive endpoints (generation, rendering, login).

Hits are stored in the database, so every API process and server enforces the same limit. Two requests
racing at the very edge of the window can both pass; that small overshoot is acceptable here.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db import session_factory
from app.errors import RateLimited
from app.models import RateLimitHit, utcnow

WINDOW = timedelta(seconds=60)


def check_rate(subject: str, bucket: str, per_minute: int) -> None:
    if per_minute <= 0:
        return
    key = f"{bucket}:{subject}"[:200]
    now = utcnow()
    with session_factory()() as db:
        db.execute(delete(RateLimitHit).where(RateLimitHit.key == key, RateLimitHit.at < now - WINDOW))
        hits = db.scalars(select(RateLimitHit.at).where(RateLimitHit.key == key).order_by(RateLimitHit.at)).all()
        if len(hits) >= per_minute:
            db.commit()
            oldest = hits[0] if hits[0].tzinfo else hits[0].replace(tzinfo=UTC)  # SQLite drops the zone
            retry = int((WINDOW - (now - oldest)).total_seconds()) + 1
            raise RateLimited(f"Too many {bucket} requests. Try again in {retry}s.", details={"retry_after": retry})
        db.add(RateLimitHit(key=key, at=now))
        db.commit()


def purge(older_than: timedelta = timedelta(minutes=10)) -> int:
    """Delete old hits of keys that were never checked again (run periodically by the worker)."""
    with session_factory()() as db:
        n = db.execute(delete(RateLimitHit).where(RateLimitHit.at < datetime.now(UTC) - older_than)).rowcount
        db.commit()
    return n or 0


def reset() -> None:
    with session_factory()() as db:
        db.execute(delete(RateLimitHit))
        db.commit()
