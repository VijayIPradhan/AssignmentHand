"""Database-backed job queue. Jobs survive restarts; workers claim them with a conditional UPDATE so
several workers can run safely against PostgreSQL (and one against SQLite)."""

import logging
import os
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobStatus, ProcessingJob, utcnow

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def enqueue(
    db: Session,
    *,
    user_id: str,
    type: str,
    payload: dict | None = None,
    assignment_id: str | None = None,
    profile_id: str | None = None,
    dedupe: bool = True,
) -> ProcessingJob:
    if dedupe:
        existing = db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.user_id == user_id,
                ProcessingJob.type == type,
                ProcessingJob.assignment_id == assignment_id,
                ProcessingJob.profile_id == profile_id,
                ProcessingJob.status.in_([JobStatus.queued, JobStatus.running]),
            )
        )
        if existing is not None and existing.payload == (payload or {}):
            log.info(f"job already queued: {type}", extra={"job_id": existing.id, "job_type": type})
            return existing
    job = ProcessingJob(
        user_id=user_id,
        type=type,
        payload=payload or {},
        assignment_id=assignment_id,
        profile_id=profile_id,
        max_attempts=get_settings().job_max_attempts,
    )
    db.add(job)
    db.commit()
    log.info(
        f"job queued: {type}",
        extra={"job_id": job.id, "job_type": type, "assignment_id": assignment_id, "profile_id": profile_id},
    )
    return job


def claim_next(db: Session) -> ProcessingJob | None:
    now = utcnow()
    candidates = db.scalars(
        select(ProcessingJob.id)
        .where(ProcessingJob.status == JobStatus.queued, ProcessingJob.run_after <= now)
        .order_by(ProcessingJob.created_at)
        .limit(5)
    ).all()
    for jid in candidates:
        res = db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == jid, ProcessingJob.status == JobStatus.queued)
            .values(
                status=JobStatus.running,
                locked_by=WORKER_ID,
                locked_at=now,
                attempts=ProcessingJob.attempts + 1,
                updated_at=now,
            )
        )
        db.commit()
        if res.rowcount == 1:
            return db.get(ProcessingJob, jid)
    return None


def requeue_stale(db: Session) -> int:
    """Return jobs whose worker died mid-run to the queue (or fail them if out of attempts)."""
    cutoff = utcnow() - timedelta(seconds=get_settings().job_stale_after_s)
    stale = db.scalars(
        select(ProcessingJob).where(ProcessingJob.status == JobStatus.running, ProcessingJob.locked_at < cutoff)
    ).all()
    for job in stale:
        log.warning(
            "stale job recovered",
            extra={"job_id": job.id, "job_type": job.type, "locked_by": job.locked_by, "attempt": job.attempts},
        )
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.failed
            job.error = "The job stopped responding and ran out of retries"
            job.finished_at = utcnow()
        else:
            job.status = JobStatus.queued
            job.message = "Restarting after an interruption"
        job.locked_by = None
    db.commit()
    return len(stale)


def backoff(attempt: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=min(300, 5 * 2 ** max(0, attempt - 1)))
