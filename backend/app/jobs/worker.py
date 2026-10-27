"""Background worker: ``python -m app.jobs.worker``. Also usable in-process (RUN_WORKER_IN_PROCESS)."""

import logging
import threading
import time
import traceback

from app.ai.providers import LLMConfigError, LLMError
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.jobs.handlers import HANDLERS
from app.jobs.queue import WORKER_ID, backoff, claim_next, requeue_stale
from app.models import JobStatus, ProcessingJob, utcnow
from app.services.assignments import PipelineError

log = logging.getLogger("app.worker")

_heartbeat = {"at": 0.0}


def last_heartbeat() -> float:
    return _heartbeat["at"]


def _user_message(exc: Exception) -> tuple[str, bool]:
    """(safe message, retryable). Internal exceptions never leak document content to the client."""
    if isinstance(exc, (PipelineError, AppError)):
        return str(exc), False
    if isinstance(exc, LLMConfigError):
        return f"AI provider not configured: {exc}", False
    if isinstance(exc, LLMError):
        return str(exc), exc.retryable
    if isinstance(exc, (ValueError, KeyError)):
        return "The file could not be processed", False
    return "Unexpected processing error", True


def run_job(job_id: str) -> None:
    Session = session_factory()
    db = Session()
    try:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        handler, on_failure = HANDLERS[job.type]

        def progress(p: float, msg: str) -> None:
            # Separate session so progress is visible while the main transaction is open.
            with Session() as s2:
                j = s2.get(ProcessingJob, job_id)
                if j is not None:
                    j.progress = max(0.0, min(1.0, float(p)))
                    j.message = msg[:500]
                    j.locked_at = utcnow()
                    s2.commit()

        started = time.monotonic()
        log.info("job started", extra={"job_id": job.id, "job_type": job.type, "attempt": job.attempts})
        try:
            result = handler(db, job, progress) or {}
        except Exception as exc:
            db.rollback()
            job = db.get(ProcessingJob, job_id)
            msg, retryable = _user_message(exc)
            log.warning(
                "job failed",
                extra={
                    "job_id": job.id,
                    "job_type": job.type,
                    "error_type": type(exc).__name__,
                    "retryable": retryable,
                    "trace": traceback.format_exc(limit=8),
                },
            )
            if retryable and job.attempts < job.max_attempts:
                job.status = JobStatus.queued
                job.run_after = backoff(job.attempts)
                job.message = f"Retrying ({job.attempts}/{job.max_attempts}): {msg}"
                job.error = msg
                job.locked_by = None
                db.commit()
                return
            job.status = JobStatus.failed
            job.error = msg
            job.message = msg
            job.finished_at = utcnow()
            job.locked_by = None
            db.commit()
            if on_failure is not None:
                try:
                    on_failure(db, job, msg)
                except Exception:
                    db.rollback()
                    log.exception("failure hook error", extra={"job_id": job_id})
            return
        job = db.get(ProcessingJob, job_id)
        job.status = JobStatus.succeeded
        job.result = result
        job.progress = 1.0
        job.message = "Done"
        job.error = None
        job.finished_at = utcnow()
        job.locked_by = None
        db.commit()
        log.info(
            "job finished",
            extra={"job_id": job.id, "job_type": job.type, "seconds": round(time.monotonic() - started, 2)},
        )
    finally:
        db.close()


def run_once() -> bool:
    """Claim and run one job. Returns True if a job ran."""
    Session = session_factory()
    with Session() as db:
        job = claim_next(db)
        jid = job.id if job else None
    if jid is None:
        return False
    run_job(jid)
    return True


def drain(max_jobs: int = 100) -> int:
    """Run queued jobs until none are ready (used by tests and the CLI)."""
    n = 0
    while n < max_jobs and run_once():
        n += 1
    return n


def loop(stop: threading.Event | None = None) -> None:
    s = get_settings()
    last_stale = last_purge = 0.0
    log.info("worker started", extra={"worker": WORKER_ID})
    while stop is None or not stop.is_set():
        _heartbeat["at"] = time.time()
        try:
            if time.monotonic() - last_stale > 60:
                with session_factory()() as db:
                    requeue_stale(db)
                last_stale = time.monotonic()
            if s.retention_days > 0 and time.monotonic() - last_purge > 3600:
                from app.services.retention import purge_expired

                with session_factory()() as db:
                    purged = purge_expired(db)
                if purged["deleted_assignments"]:
                    log.info("retention purge", extra=purged)
                last_purge = time.monotonic()
            if not run_once():
                time.sleep(s.job_poll_interval_s)
        except Exception:
            log.exception("worker loop error")
            time.sleep(min(10.0, s.job_poll_interval_s * 5))


def start_in_process() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), name="job-worker", daemon=True).start()
    return stop


def main() -> None:
    from app.logging_setup import configure_logging

    configure_logging(get_settings().log_level)
    loop()


if __name__ == "__main__":
    main()
