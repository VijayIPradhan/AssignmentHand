"""Data retention and user-data deletion."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Assignment, HandwritingProfile, User, utcnow
from app.services.profiles import forget_library
from app.storage import get_storage


def delete_assignment(db: Session, a: Assignment) -> None:
    get_storage().delete_prefix(f"users/{a.user_id}/assignments/{a.id}")
    db.delete(a)
    db.commit()


def delete_profile(db: Session, p: HandwritingProfile) -> None:
    forget_library(p.id)
    get_storage().delete_prefix(f"users/{p.user_id}/profiles/{p.id}")
    db.delete(p)
    db.commit()


def delete_user(db: Session, u: User) -> None:
    for p in db.scalars(select(HandwritingProfile).where(HandwritingProfile.user_id == u.id)):
        forget_library(p.id)
    get_storage().delete_prefix(f"users/{u.id}")
    db.delete(u)
    db.commit()


def purge_expired(db: Session) -> dict:
    days = get_settings().retention_days
    if days <= 0:
        return {"deleted_assignments": 0}
    cutoff = utcnow() - timedelta(days=days)
    old = db.scalars(select(Assignment).where(Assignment.updated_at < cutoff)).all()
    for a in old:
        delete_assignment(db, a)
    return {"deleted_assignments": len(old)}
