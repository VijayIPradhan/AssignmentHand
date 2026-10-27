"""Task-based model routing, escalation and adaptive effort."""

import pytest

from app.ai import providers
from app.ai.generate import QuestionSpec, answer_effort
from app.ai.prompts import AnswerPreferences
from app.ai.providers import EscalatingProvider, LLMInvalidOutput, LLMResult, LLMTransientError


class Scripted:
    supports_images = True
    name = "scripted"

    def __init__(self, model, outcomes):
        self.model, self.outcomes, self.calls = model, list(outcomes), 0

    def complete_json(self, system, user, schema, *, max_tokens=16000, images=None, effort=None):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return LLMResult(o, "", self.model)

    def transcribe_image(self, image, prompt="", media_type="image/png"):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture()
def routed(tmp_env, monkeypatch):
    from app import config

    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("LLM_MODEL", "big-model")
    monkeypatch.setenv("LLM_MODEL_TRANSCRIBE", "small-model")
    config.get_settings.cache_clear()
    providers._cached_provider.cache_clear()
    built = []

    def fake(kind, model=None, effort=None):
        built.append((kind, model, effort))
        return Scripted(model, [])

    monkeypatch.setattr(providers, "_cached_provider", fake)
    yield built
    config.get_settings.cache_clear()


def test_tasks_are_routed_to_their_models(routed):
    a = providers.get_provider("answer")
    assert a.model == "big-model"
    t = providers.get_provider("transcribe")
    assert isinstance(t, EscalatingProvider) and t.model == "small-model" and t.main.model == "big-model"
    v = providers.get_provider("verify")  # not routed: uses the main model directly
    assert v.model == "big-model" and not isinstance(v, EscalatingProvider)
    status = providers.provider_status()["routing"]
    assert status == {"answer": "big-model", "transcribe": "small-model", "verify": "big-model"}
    assert ("bedrock", "small-model", "medium") in routed


def test_escalation_on_bad_output_but_not_on_outages():
    light = Scripted("small", [LLMInvalidOutput("bad")])
    main = Scripted("big", [{"ok": True}])
    p = EscalatingProvider(light, main)
    assert p.complete_json("s", "u", {}).model == "big"
    assert light.calls == 1 and main.calls == 1

    p = EscalatingProvider(Scripted("small", ["fine text"]), Scripted("big", []))
    assert p.transcribe_image(b"img") == "fine text"  # success stays on the light model

    p = EscalatingProvider(Scripted("small", [LLMTransientError("down")]), Scripted("big", []))
    with pytest.raises(LLMTransientError):
        p.complete_json("s", "u", {})  # job-level retry handles outages


def test_adaptive_effort(tmp_env):
    q_small = QuestionSpec("1", "Define x.", 2, None, None, [])
    q_big = QuestionSpec("2", "Discuss y.", 10, None, None, [])
    prefs = AnswerPreferences()
    assert answer_effort(prefs, q_small) == "medium"
    assert answer_effort(prefs, q_big) is None  # configured default (high)
    assert answer_effort(AnswerPreferences(detail="concise"), q_big) == "medium"
    assert answer_effort(AnswerPreferences(technical_depth="advanced"), q_small) is None
