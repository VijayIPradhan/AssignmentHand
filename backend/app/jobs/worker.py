"""Background worker: ``python -m app.jobs.worker``. Also usable in-process (RUN_WORKER_IN_PROCESS)."""

import logging
import threading
import time
from datetime import UTC

from app.ai.providers import LLMConfigError, LLMError
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.jobs.handlers import HANDLERS
from app.jobs.queue import WORKER_ID, backoff, claim_next, requeue_stale
from app.logging_setup import bind
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


MAX_EVENTS = 200


def _event(level: str, msg: str, **fields) -> dict:
    return {
        "at": utcnow().isoformat(),
        "level": level,
        "msg": msg[:300],
        **{k: v for k, v in fields.items() if v is not None},
    }


def _append_events(Session, job_id: str, *events: dict, **updates) -> None:
    """Append timeline events (and optional field updates) in a separate, short transaction."""
    with Session() as s2:
        j = s2.get(ProcessingJob, job_id)
        if j is None:
            return
        j.events = [*(j.events or []), *events][-MAX_EVENTS:]
        for k, v in updates.items():
            setattr(j, k, v)
        s2.commit()


def run_job(job_id: str) -> None:
    Session = session_factory()
    db = Session()
    try:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        handler, on_failure = HANDLERS[job.type]
        ctx = {
            "job_id": job.id,
            "job_type": job.type,
            "user_id": job.user_id,
            "assignment_id": job.assignment_id,
            "profile_id": job.profile_id,
        }
        with bind(**ctx):
            _run(db, Session, job, handler, on_failure)
    finally:
        db.close()


def _run(db, Session, job, handler, on_failure) -> None:
    job_id = job.id
    created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
    waited_s = round((utcnow() - created).total_seconds(), 2)
    started = time.monotonic()
    last = {"t": started, "msg": "start"}

    def progress(p: float, msg: str) -> None:
        now = time.monotonic()
        step_ms = round((now - last["t"]) * 1000)
        log.info(
            f"step: {msg}",
            extra={"progress": round(float(p), 3), "prev_step": last["msg"], "prev_step_ms": step_ms},
        )
        last.update(t=now, msg=msg)
        # Separate session so progress is visible while the main transaction is open.
        _append_events(
            Session,
            job_id,
            _event("info", msg, progress=round(float(p), 3), prev_step_ms=step_ms),
            progress=max(0.0, min(1.0, float(p))),
            message=msg[:500],
            locked_at=utcnow(),
        )

    log.info(
        f"job started: {job.type}",
        extra={
            "attempt": job.attempts,
            "max_attempts": job.max_attempts,
            "queue_wait_s": waited_s,
            "payload_keys": sorted(job.payload or {}),
        },
    )
    _append_events(Session, job_id, _event("info", f"Started (attempt {job.attempts})", queue_wait_s=waited_s))
    try:
        result = handler(db, job, progress) or {}
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        msg, retryable = _user_message(exc)
        seconds = round(time.monotonic() - started, 2)
        will_retry = retryable and job.attempts < job.max_attempts
        log.log(
            logging.WARNING if will_retry else logging.ERROR,
            f"job failed: {job.type} - {msg}",
            exc_info=exc,
            extra={
                "error_type": type(exc).__name__,
                "retryable": retryable,
                "will_retry": will_retry,
                "attempt": job.attempts,
                "seconds": seconds,
                "last_step": last["msg"],
            },
        )
        if will_retry:
            job.status = JobStatus.queued
            job.run_after = backoff(job.attempts)
            job.message = f"Retrying ({job.attempts}/{job.max_attempts}): {msg}"
            job.error = msg
            job.locked_by = None
            job.events = [
                *(job.events or []),
                _event(
                    "warning",
                    f"Failed, will retry: {msg}",
                    error_type=type(exc).__name__,
                    retry_at=job.run_after.isoformat(),
                ),
            ][-MAX_EVENTS:]
            db.commit()
            log.info("retry scheduled", extra={"retry_at": job.run_after.isoformat()})
            return
        job.status = JobStatus.failed
        job.error = msg
        job.message = msg
        job.finished_at = utcnow()
        job.locked_by = None
        job.events = [
            *(job.events or []),
            _event("error", f"Failed: {msg}", error_type=type(exc).__name__, seconds=seconds),
        ][-MAX_EVENTS:]
        db.commit()
        if on_failure is not None:
            try:
                on_failure(db, job, msg)
            except Exception:
                db.rollback()
                log.exception("failure hook error")
        return
    job = db.get(ProcessingJob, job_id)
    db.refresh(job)  # events were appended in separate sessions; do not overwrite them with a stale copy
    seconds = round(time.monotonic() - started, 2)
    job.status = JobStatus.succeeded
    job.result = result
    job.progress = 1.0
    job.message = "Done"
    job.error = None
    job.finished_at = utcnow()
    job.locked_by = None
    job.events = [*(job.events or []), _event("info", "Done", seconds=seconds)][-MAX_EVENTS:]
    db.commit()
    summary = {k: v for k, v in result.items() if isinstance(v, int | float | bool | str) and len(str(v)) < 80}
    log.info(
        f"job finished: {job.type} in {seconds}s",
        extra={"seconds": seconds, "result": summary, "attempt": job.attempts},
    )


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


def concurrency() -> int:
    s = get_settings()
    return 1 if s.database_url.startswith("sqlite") else s.worker_concurrency


def loop(stop: threading.Event | None = None, housekeeping: bool = True) -> None:
    """Claim and run jobs until stopped. One loop per thread; only one thread does housekeeping."""
    s = get_settings()
    last_stale = last_purge = 0.0
    if housekeeping:
        log.info("worker started", extra={"worker": WORKER_ID, "concurrency": concurrency()})
    while stop is None or not stop.is_set():
        _heartbeat["at"] = time.time()
        try:
            if housekeeping and time.monotonic() - last_stale > 60:
                from app.ratelimit import purge as purge_rate_limits

                with session_factory()() as db:
                    requeue_stale(db)
                purge_rate_limits()
                last_stale = time.monotonic()
            if housekeeping and s.retention_days > 0 and time.monotonic() - last_purge > 3600:
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


def _start_threads(stop: threading.Event, n: int, housekeeper: bool) -> None:
    for i in range(n):
        args = (stop, housekeeper and i == 0)
        threading.Thread(target=loop, args=args, name=f"job-worker-{i + 1}", daemon=True).start()


def start_in_process() -> threading.Event:
    stop = threading.Event()
    _start_threads(stop, concurrency(), housekeeper=True)
    return stop


def main() -> None:
    from app.logging_setup import configure_logging

    configure_logging(get_settings().log_level)
    stop = threading.Event()
    _start_threads(stop, concurrency() - 1, housekeeper=False)  # the main thread does the housekeeping
    try:
        loop(stop, housekeeping=True)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
