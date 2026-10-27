import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.providers import provider_status
from app.api.deps import owned_job
from app.config import get_settings
from app.db import get_db
from app.ingestion.ocr import get_ocr
from app.models import Assignment, HandwritingProfile, JobStatus, ProcessingJob, RenderedDocument, User
from app.schemas import AssignmentOut, JobOut, ProfileOut, RenderedOut
from app.security import current_user
from app.storage import get_storage

router = APIRouter(tags=["system"])


@router.get("/health", include_in_schema=True)
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    """Readiness: database and storage reachable; reports worker heartbeat and integrations."""
    checks: dict = {}
    ok = True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        ok = False
    try:
        st = get_storage()
        st.put("health/probe", b"ok")
        st.delete("health/probe")
        checks["storage"] = "ok"
    except Exception:
        checks["storage"] = "error"
        ok = False
    stale = db.scalar(
        select(ProcessingJob.id)
        .where(ProcessingJob.status == JobStatus.queued)
        .order_by(ProcessingJob.created_at)
        .limit(1)
    )
    from app.jobs.worker import last_heartbeat

    hb = last_heartbeat()
    checks["worker_in_process"] = (time.time() - hb < 30) if hb else None
    checks["queued_jobs"] = bool(stale)
    checks["ai"] = provider_status()
    ocr = get_ocr()
    checks["ocr"] = ocr.name if ocr else "unavailable"
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)


@router.get("/config/public")
def public_config():
    s = get_settings()
    return {
        "max_upload_mb": s.max_upload_mb,
        "max_pdf_pages": s.max_pdf_pages,
        "allow_registration": s.allow_registration,
        "ai": provider_status(),
    }


@router.get("/jobs/{jid}", response_model=JobOut)
def job(jid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_job(db, jid, user)


@router.get("/jobs", response_model=list[JobOut])
def jobs(
    assignment_id: str | None = None,
    profile_id: str | None = None,
    active: bool = False,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(ProcessingJob).where(ProcessingJob.user_id == user.id)
    if assignment_id:
        q = q.where(ProcessingJob.assignment_id == assignment_id)
    if profile_id:
        q = q.where(ProcessingJob.profile_id == profile_id)
    if active:
        q = q.where(ProcessingJob.status.in_([JobStatus.queued, JobStatus.running]))
    return db.scalars(q.order_by(ProcessingJob.created_at.desc()).limit(limit)).all()


@router.post("/jobs/{jid}/retry", response_model=JobOut)
def retry_job(jid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = owned_job(db, jid, user)
    if j.status == JobStatus.failed:
        j.status = JobStatus.queued
        j.attempts = 0
        j.error = None
        j.message = "Retrying"
        db.commit()
    return j


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    assignments = db.scalars(
        select(Assignment).where(Assignment.user_id == user.id).order_by(Assignment.updated_at.desc()).limit(12)
    ).all()
    profiles = db.scalars(
        select(HandwritingProfile)
        .where(HandwritingProfile.user_id == user.id)
        .order_by(HandwritingProfile.updated_at.desc())
    ).all()
    active = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.user_id == user.id, ProcessingJob.status.in_([JobStatus.queued, JobStatus.running]))
        .order_by(ProcessingJob.created_at.desc())
    ).all()
    exports = db.scalars(
        select(RenderedDocument)
        .where(RenderedDocument.user_id == user.id, RenderedDocument.is_preview.is_(False))
        .order_by(RenderedDocument.created_at.desc())
        .limit(10)
    ).all()
    titles = {a.id: a.title for a in db.scalars(select(Assignment).where(Assignment.user_id == user.id))}
    return {
        "assignments": [AssignmentOut.model_validate(a).model_dump(mode="json") for a in assignments],
        "profiles": [ProfileOut.model_validate(p).model_dump(mode="json", exclude={"samples"}) for p in profiles],
        "active_jobs": [JobOut.model_validate(j).model_dump(mode="json") for j in active],
        "exports": [
            {
                **RenderedOut.model_validate(r).model_dump(mode="json"),
                "assignment_id": r.assignment_id,
                "assignment_title": titles.get(r.assignment_id, ""),
            }
            for r in exports
        ],
        "ai": provider_status(),
    }
