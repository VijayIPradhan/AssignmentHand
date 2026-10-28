"""Logging: context propagation, redaction, request/job/AI records, job timelines, browser reports."""

import json
import logging

import pytest

from app.logging_setup import REDACTED, ContextFilter, JsonFormatter, PrettyFormatter, bind
from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.addFilter(ContextFilter())

    def emit(self, record):
        self.records.append(record)

    def find(self, contains: str) -> list[logging.LogRecord]:
        return [r for r in self.records if contains in r.getMessage()]


@pytest.fixture()
def cap():
    h = Capture()
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    yield h
    root.removeHandler(h)
    root.setLevel(old)


def test_context_is_attached_and_sensitive_fields_redacted(cap):
    log = logging.getLogger("app.test")
    with bind(job_id="job123", user_id="u1"):
        log.info("doing work", extra={"password": "hunter2", "content": "my private answer", "pages": 3})
    rec = cap.find("doing work")[0]
    assert rec.job_id == "job123" and rec.user_id == "u1" and rec.pages == 3
    assert rec.password == REDACTED and rec.content == REDACTED
    line = json.loads(JsonFormatter().format(rec))
    assert line["job_id"] == "job123" and "hunter2" not in json.dumps(line) and "private" not in json.dumps(line)
    pretty = PrettyFormatter(color=False).format(rec)
    assert "doing work" in pretty and "job=job123" in pretty and "hunter2" not in pretty
    log.info("outside")
    assert not hasattr(cap.find("outside")[0], "job_id")  # context does not leak out of the block


def test_request_lines_include_user_status_and_timing(auth_client, cap):
    auth_client.get("/api/v1/dashboard")
    auth_client.get("/api/v1/assignments/does-not-exist")
    ok = cap.find("GET /api/v1/dashboard -> 200")[0]
    assert ok.user_id and ok.request_id and isinstance(ok.ms, int) and ok.levelno == logging.INFO
    missing = cap.find("GET /api/v1/assignments/does-not-exist -> 404")[0]
    assert missing.levelno == logging.WARNING
    assert cap.find("not_found:")  # the structured error is logged with its code


def test_job_lifecycle_is_logged_and_kept_as_a_timeline(auth_client, cap):
    c = auth_client
    aid = c.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    assert cap.find("job queued: extract") and cap.find("job started: extract")
    step = cap.find("step: Finding questions")[0]
    assert step.job_id and step.assignment_id == aid and step.job_type == "extract"
    summary = cap.find("extraction: 6 questions from 2 pages")[0]
    assert summary.needs_review >= 1 and summary.job_id == step.job_id
    assert cap.find("job finished: extract")
    job = c.get(f"/api/v1/jobs?assignment_id={aid}").json()[0]
    msgs = [e["msg"] for e in job["events"]]
    assert msgs[0].startswith("Started") and "Finding questions" in msgs and msgs[-1] == "Done"


def test_failed_job_records_error_with_traceback(tmp_env, cap):
    from app.db import session_factory
    from app.jobs.queue import enqueue
    from app.models import Assignment, ProcessingJob, User

    with session_factory()() as db:
        u = User(email="log@example.com", password_hash="x")
        db.add(u)
        db.commit()
        a = Assignment(user_id=u.id, title="t", preferences={})
        db.add(a)
        db.commit()
        jid = enqueue(db, user_id=u.id, type="extract", assignment_id=a.id).id
    run_jobs()
    rec = cap.find("job failed: extract")[0]
    assert rec.levelno == logging.ERROR and rec.exc_info and rec.error_type == "PipelineError"
    with session_factory()() as db:
        ev = db.get(ProcessingJob, jid).events
    assert ev[-1]["level"] == "error" and "No assignment PDF" in ev[-1]["msg"]


def test_ai_calls_are_logged_with_tokens_and_cost(tmp_env, cap):
    from app.ai.usage import record, usage_scope

    with usage_scope("u1", "answer"):
        record(
            provider="bedrock",
            model="global.anthropic.claude-opus-5-5",
            served_model="claude-opus-5-5",
            input_tokens=1000,
            output_tokens=200,
            latency_ms=900,
            status="ok",
        )
    rec = cap.find("ai call ok: answer")[0]
    assert rec.input_tokens == 1000 and rec.cost_usd and rec.pricing_source == "list_price_estimate"


def test_browser_errors_are_reported(client, cap):
    r = client.post(
        "/api/v1/client-log",
        json={
            "level": "error",
            "message": "TypeError: x is undefined",
            "url": "http://phone/assignments/1",
            "source": "window.onerror",
        },
    )
    assert r.status_code == 204
    rec = cap.find("browser: TypeError: x is undefined")[0]
    assert rec.levelno == logging.ERROR and rec.source == "window.onerror"


def test_log_files_are_written(tmp_env, monkeypatch):
    from app.logging_setup import configure_logging

    d = tmp_env / "logs"
    configure_logging("INFO", "json", d)
    logging.getLogger("app.test").warning("disk check", extra={"k": 1})
    for h in logging.getLogger().handlers:
        h.flush()
    assert "disk check" in (d / "app.jsonl").read_text(encoding="utf-8")
    assert "disk check" in (d / "errors.jsonl").read_text(encoding="utf-8")
    configure_logging("INFO", "pretty", None)
