import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Tests must never reach a real AI provider or the developer's data.
os.environ.update(
    {
        "APP_ENV": "test",
        "LLM_PROVIDER": "none",
        "OCR_PROVIDER": "none",
        "RUN_WORKER_IN_PROCESS": "false",
        "SECRET_KEY": "test-secret-key-0123456789-abcdefghijkl",
        "RETENTION_DAYS": "0",
        "LOG_DIR": "",  # tests never write to the developer's log files
        "LOG_FORMAT": "json",
        "LLM_MIN_LENGTH_RATIO": "0",  # scripted fake answers are short; the rule has its own test
        # Never the developer's real file storage (backend/.env may point at a Cloudflare R2 bucket).
        "STORAGE_BACKEND": "local",
        "S3_BUCKET": "",
        "R2_ACCOUNT_ID": "",
        "S3_ENDPOINT_URL": "",
        "S3_ACCESS_KEY_ID": "",
        "S3_SECRET_ACCESS_KEY": "",
        "AWS_BEARER_TOKEN_BEDROCK": "",
    }
)


# TEST_STORAGE=s3 runs the whole suite against an in-memory S3 (moto) instead of a local directory, so
# every upload, render and delete path is exercised through the S3/R2 backend too.
if os.environ.get("TEST_STORAGE") == "s3":
    os.environ.update(
        {
            "STORAGE_BACKEND": "s3",
            "S3_BUCKET": "assignmenthand-test",
            "S3_REGION": "us-east-1",
            "S3_ENDPOINT_URL": "",
            "S3_ACCESS_KEY_ID": "testing",
            "S3_SECRET_ACCESS_KEY": "testing",
            "S3_PREFIX": "suite",
        }
    )


@pytest.fixture(scope="session", autouse=True)
def _s3_storage():
    if os.environ.get("TEST_STORAGE") != "s3":
        yield
        return
    import boto3
    from moto import mock_aws

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="assignmenthand-test")
        yield


class FakeProvider:
    """Deterministic stand-in for an LLM, safe under concurrent use (generation runs questions in
    parallel). Script it with ``responses`` (consumed in order) or, when the order across threads
    matters, ``responder(user_prompt) -> dict | Exception`` keyed on the question."""

    name = "fake"
    model = "fake-model"
    supports_images = False
    max_output_tokens = 16000

    def __init__(self):
        import threading

        self.responses: list = []
        self.responder = None
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def complete_json(self, system, user, schema, *, max_tokens=16000, images=None, effort=None):
        from app.ai.providers import LLMResult

        with self._lock:
            self.calls.append({"system": system, "user": user, "effort": effort})
            if self.responder is not None:
                r = self.responder(user)
            elif self.responses:
                r = self.responses.pop(0)
            else:
                r = {"answer": "Default answer text for testing.", "flags": [], "confidence": "high"}
        if isinstance(r, Exception):
            raise r
        return LLMResult(r, "", self.model, 100, 50)

    def transcribe_image(self, image, prompt="", media_type="image/png"):
        return "transcribed text"


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    if os.environ.get("TEST_STORAGE") == "s3":
        monkeypatch.setenv("S3_PREFIX", f"suite/{tmp_path.name}")  # each test its own folder in the bucket
    from app import config, db, ratelimit, storage
    from app.services import profiles

    config.get_settings.cache_clear()
    storage.get_storage.cache_clear()
    db.reset_engine()
    db.Base.metadata.create_all(db.get_engine())
    ratelimit.reset()
    profiles._LIB_CACHE.clear()
    yield tmp_path
    db.get_engine().dispose()
    config.get_settings.cache_clear()
    storage.get_storage.cache_clear()


@pytest.fixture()
def fake_llm(tmp_env):
    from app.ai import providers

    fp = FakeProvider()
    providers.set_provider_override(fp)
    yield fp
    providers.set_provider_override(None)


@pytest.fixture()
def client(tmp_env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    r = client.post("/api/v1/auth/register", json={"email": "student@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 201, r.text
    return client


def run_jobs():
    from app.jobs.worker import drain

    return drain()


@pytest.fixture(scope="session")
def template_library():
    """Glyph library built from synthetic filled template sheets (shared, read-only)."""
    from app.handwriting.profile_builder import SampleInput, build_library
    from tests.synth import degrade, filled_template

    res = build_library(
        [
            SampleInput("t0", "template", degrade(filled_template(0), rotate=2, seed=1)),
            SampleInput("t1", "template", filled_template(1)),
        ]
    )
    return res.library


def stored_keys() -> list[str]:
    """Every object key in the current storage backend (local directory or S3), for assertions."""
    from app.storage import LocalStorage, S3Storage, get_storage

    st = get_storage()
    if isinstance(st, LocalStorage):
        return [p.relative_to(st.root).as_posix() for p in st.root.rglob("*") if p.is_file()]
    assert isinstance(st, S3Storage)
    pages = st.client.get_paginator("list_objects_v2").paginate(Bucket=st.bucket, Prefix=st.prefix)
    return [o["Key"][len(st.prefix) :] for page in pages for o in page.get("Contents", [])]
