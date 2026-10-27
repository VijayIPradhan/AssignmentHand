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
    }
)


class FakeProvider:
    """Deterministic stand-in for an LLM. Behaviour is scripted per test via ``responses``."""

    name = "fake"
    model = "fake-model"
    supports_images = False
    max_output_tokens = 16000

    def __init__(self):
        self.responses: list = []
        self.calls: list[dict] = []

    def complete_json(self, system, user, schema, *, max_tokens=16000, images=None, effort=None):
        from app.ai.providers import LLMResult

        self.calls.append({"system": system, "user": user, "effort": effort})
        r = (
            self.responses.pop(0)
            if self.responses
            else {"answer": "Default answer text for testing.", "flags": [], "confidence": "high"}
        )
        if isinstance(r, Exception):
            raise r
        return LLMResult(r, "", self.model, 100, 50)

    def transcribe_image(self, image, prompt="", media_type="image/png"):
        return "transcribed text"


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
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
