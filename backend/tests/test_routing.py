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
    for task in ("ANSWER", "ANSWER_HARD", "VERIFY"):  # independent of any local .env routing
        monkeypatch.setenv(f"LLM_MODEL_{task}", "")
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
    assert status == {
        "answer": "big-model",
        "answer_hard": "big-model",
        "transcribe": "small-model",
        "verify": "big-model",
    }
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


def test_hard_questions_are_detected(tmp_env):
    from app.ai.generate import is_hard_question

    prefs = AnswerPreferences()
    assert is_hard_question(prefs, QuestionSpec("1", "Discuss the role of HR.", 10, None, None, []))[0] is False
    assert is_hard_question(prefs, QuestionSpec("1", "Calculate the NPV of the project.", 5, None, None, []))[0]
    assert is_hard_question(prefs, QuestionSpec("1", "Solve $x^2 + 3x = 4$.", 5, None, None, []))[0]
    assert is_hard_question(prefs, QuestionSpec("1", "Describe the figure.", 5, None, None, [b"png"]))[0]
    assert is_hard_question(prefs, QuestionSpec("1", "Discuss.", 20, None, None, []))[0]
    assert is_hard_question(
        AnswerPreferences(technical_depth="advanced"), QuestionSpec("1", "Discuss.", 5, None, None, [])
    )[0]


def test_answers_must_reach_95_percent_of_target(tmp_env, monkeypatch):
    from app import config
    from app.ai.generate import generate_answer
    from tests.conftest import FakeProvider

    monkeypatch.setenv("LLM_MIN_LENGTH_RATIO", "0.95")
    config.get_settings.cache_clear()
    fp = FakeProvider()
    fp.responses = [
        {"answer": "word " * 50, "flags": [], "confidence": "high"},  # 50 of 100: too short
        {"answer": "word " * 97, "flags": [], "confidence": "high"},
    ]
    q = QuestionSpec("1", "Discuss.", 4, None, None, [])
    out = generate_answer(
        fp, subject=None, assignment_instructions=None, prefs=AnswerPreferences(detail="concise"), q=q, references=[]
    )
    assert out.word_count == 97 and len(fp.calls) == 2
    assert "only 50 words" in fp.calls[1]["user"] and "at least 95 words" in fp.calls[1]["user"]
    assert out.input_tokens == 200  # both attempts counted

    # The expanded rewrite fails: the shorter valid answer is kept and flagged
    fp.responses = [{"answer": "word " * 50, "flags": [], "confidence": "high"}, LLMInvalidOutput("bad")]
    out = generate_answer(
        fp, subject=None, assignment_instructions=None, prefs=AnswerPreferences(detail="concise"), q=q, references=[]
    )
    assert out.word_count == 50 and any("shorter than the 95 required" in f["message"] for f in out.flags)


def test_user_routing_overrides_server_defaults(routed):
    choices = providers.model_choices()
    assert "big-model" in choices and "small-model" in choices
    assert providers.task_model("answer", {"answer": "small-model"}) == "small-model"
    assert providers.task_model("answer", {"answer": "not-offered"}) == "big-model"  # stale choice ignored
    p = providers.get_provider("verify", {"verify": "small-model"})
    assert isinstance(p, EscalatingProvider) and p.model == "small-model" and p.main.model == "big-model"


def test_model_routing_api(auth_client):
    c = auth_client
    out = c.get("/api/v1/auth/model-routing").json()
    assert {t["task"] for t in out["tasks"]} == set(providers.TASKS)
    pick = out["choices"][0]["model"]
    out = c.put("/api/v1/auth/model-routing", json={"routing": {"answer": pick}}).json()
    answer = next(t for t in out["tasks"] if t["task"] == "answer")
    assert answer["model"] == pick and answer["custom"]
    assert c.put("/api/v1/auth/model-routing", json={"routing": {"answer": "made-up"}}).status_code == 422
    assert c.put("/api/v1/auth/model-routing", json={"routing": {"cooking": pick}}).status_code == 422
    out = c.put("/api/v1/auth/model-routing", json={"routing": {"answer": None}}).json()
    assert not next(t for t in out["tasks"] if t["task"] == "answer")["custom"]


def test_bedrock_api_key_read_from_dotenv(tmp_env, monkeypatch):
    from app import config

    (tmp_env / ".env").write_text("AWS_BEARER_TOKEN_BEDROCK=key-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_env)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    for name in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setattr(providers.Path, "home", lambda: tmp_env)  # no ~/.aws/credentials
    config.get_settings.cache_clear()
    assert providers.provider_configured()
    client = providers.BedrockProvider("some-model")._make_client(config.get_settings())
    assert client.api_key == "key-from-dotenv"

    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "key-from-env")  # the environment wins
    config.get_settings.cache_clear()
    assert providers.BedrockProvider("some-model")._make_client(config.get_settings()).api_key == "key-from-env"
