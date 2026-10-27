from datetime import timedelta

from app.db import session_factory
from app.jobs.queue import claim_next, enqueue, requeue_stale
from app.models import Assignment, AssignmentStatus, JobStatus, ProcessingJob, User, utcnow
from tests.conftest import run_jobs


def _user(db) -> User:
    u = User(email="jobs@example.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


def test_claim_is_exclusive_and_dedupes(tmp_env):
    with session_factory()() as db:
        u = _user(db)
        j1 = enqueue(db, user_id=u.id, type="export")
        j2 = enqueue(db, user_id=u.id, type="export")
        assert j1.id == j2.id
        first = claim_next(db)
        assert first.id == j1.id and first.status == JobStatus.running and first.attempts == 1
        assert claim_next(db) is None


def test_stale_running_jobs_are_requeued_then_failed(tmp_env):
    with session_factory()() as db:
        u = _user(db)
        j = enqueue(db, user_id=u.id, type="export")
        claim_next(db)
        row = db.get(ProcessingJob, j.id)
        row.locked_at = utcnow() - timedelta(hours=2)
        db.commit()
        assert requeue_stale(db) == 1
        assert db.get(ProcessingJob, j.id).status == JobStatus.queued
        row = db.get(ProcessingJob, j.id)
        row.status, row.attempts, row.locked_at = JobStatus.running, row.max_attempts, utcnow() - timedelta(hours=2)
        db.commit()
        requeue_stale(db)
        assert db.get(ProcessingJob, j.id).status == JobStatus.failed


def test_failed_extraction_marks_assignment_recoverable(tmp_env):
    with session_factory()() as db:
        u = _user(db)
        a = Assignment(user_id=u.id, title="t", preferences={})
        db.add(a)
        db.commit()
        j = enqueue(db, user_id=u.id, type="extract", assignment_id=a.id)
        jid, aid = j.id, a.id
    run_jobs()
    with session_factory()() as db:
        assert db.get(ProcessingJob, jid).status == JobStatus.failed
        assert "No assignment PDF" in db.get(ProcessingJob, jid).error
        a = db.get(Assignment, aid)
        assert a.status == AssignmentStatus.extraction_failed and a.error


def test_processing_with_only_unusable_samples_reports_needs_samples(auth_client):
    c = auth_client
    pid = c.post("/api/v1/profiles", json={"name": "p"}).json()["id"]
    import numpy as np

    from tests.synth import to_png

    c.post(f"/api/v1/profiles/{pid}/samples", files={"file": ("x.png", to_png(np.full((800, 600, 3), 255, np.uint8)))})
    job = c.post(f"/api/v1/profiles/{pid}/process").json()
    run_jobs()
    j = c.get(f"/api/v1/jobs/{job['id']}").json()
    assert j["status"] == "succeeded"  # processing completes; the sample itself is rejected
    p = c.get(f"/api/v1/profiles/{pid}").json()
    assert p["status"] == "needs_samples" and p["samples"][0]["status"] == "rejected"


def test_retention_purge_deletes_old_assignments_and_files(tmp_env, monkeypatch):
    from datetime import timedelta

    from app import config
    from app.services.retention import purge_expired
    from app.storage import get_storage

    monkeypatch.setenv("RETENTION_DAYS", "30")
    config.get_settings.cache_clear()
    with session_factory()() as db:
        u = _user(db)
        old = Assignment(user_id=u.id, title="old", preferences={})
        new = Assignment(user_id=u.id, title="new", preferences={})
        db.add_all([old, new])
        db.commit()
        get_storage().put(f"users/{u.id}/assignments/{old.id}/docs/x/source.pdf", b"%PDF-")
        old.updated_at = utcnow() - timedelta(days=45)
        db.commit()
        assert purge_expired(db) == {"deleted_assignments": 1}
        assert db.get(Assignment, new.id) is not None and db.get(Assignment, old.id) is None
        assert not get_storage().exists(f"users/{u.id}/assignments/{old.id}/docs/x/source.pdf")
