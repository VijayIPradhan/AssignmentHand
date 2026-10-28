"""AI usage accounting, pricing resolution and the monthly budget."""

from app.ai.pricing import base_model, cost, price_for
from app.ai.usage import record, usage_scope
from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf


def test_pricing_resolution(tmp_env, monkeypatch):
    from app import config
    from app.ai import pricing

    assert base_model("global.anthropic.claude-opus-5-5") == "claude-opus-5-5"
    assert base_model("apac.anthropic.claude-3-7-sonnet-20250219-v1:0") == "claude-3-7-sonnet"
    p = price_for("claude-sonnet-5-5")
    assert (p.input_per_mtok, p.output_per_mtok, p.source) == (2.0, 10.0, "list_price")
    assert price_for("global.anthropic.claude-sonnet-5-5").source == "list_price_estimate"
    assert price_for("some-local-model") is None and cost("some-local-model", 10, 10) is None
    assert cost("claude-opus-5-5", 1_000_000, 500_000) == 14.0

    monkeypatch.setenv("LLM_PRICING", '{"global.anthropic.claude-opus-5-5": [3.0, 15.0]}')
    config.get_settings.cache_clear()
    pricing._configured.cache_clear()
    p = price_for("global.anthropic.claude-opus-5-5")
    assert (p.input_per_mtok, p.source) == (3.0, "configured")
    pricing._configured.cache_clear()


def _uid(client) -> str:
    return client.get("/api/v1/auth/me").json()["id"]


def test_calls_are_recorded_and_summarised(auth_client):
    uid = _uid(auth_client)
    with usage_scope(uid, "answer"):
        record(
            provider="bedrock",
            model="global.anthropic.claude-opus-5-5",
            served_model="claude-opus-5-5",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1200,
            status="ok",
        )
    with usage_scope(uid, "transcribe"):
        record(
            provider="bedrock",
            model="global.anthropic.claude-sonnet-5-5",
            served_model=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=300,
            status="error",
            error_type="RateLimitError",
        )
    record(
        provider="x", model="m", served_model=None, input_tokens=1, output_tokens=1, latency_ms=1, status="ok"
    )  # no scope
    u = auth_client.get("/api/v1/usage").json()
    t = u["totals"]
    assert t["calls"] == 2 and t["errors"] == 1 and t["input_tokens"] == 1000 and t["estimated"] is True
    assert t["cost_usd"] == round(1000 / 1e6 * 4 + 500 / 1e6 * 20, 6)
    assert {g["key"] for g in u["by_task"]} == {"answer", "transcribe"}
    assert u["recent"][0]["task"] == "transcribe" and u["recent"][0]["error_type"] == "RateLimitError"
    assert any(p["source"] == "list_price_estimate" for p in u["pricing"])
    assert auth_client.get("/api/v1/dashboard").json()["usage"]["month_calls"] == 2


def test_generation_records_usage_per_question(auth_client, fake_llm):
    # The fake provider does not call record(); wrap it so the scope set by the job is exercised.
    orig = fake_llm.complete_json

    def recording(*a, **k):
        res = orig(*a, **k)
        record(
            provider="fake",
            model="claude-sonnet-5-5",
            served_model="claude-sonnet-5-5",
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            latency_ms=5,
            status="ok",
        )
        return res

    fake_llm.complete_json = recording
    c = auth_client
    aid = c.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    c.post(f"/api/v1/assignments/{aid}/confirm-extraction")
    c.post(f"/api/v1/assignments/{aid}/generate", json={})
    run_jobs()
    u = c.get("/api/v1/usage").json()
    assert u["totals"]["calls"] == 6
    assert u["by_assignment"][0]["key"] == aid and u["by_assignment"][0]["calls"] == 6
    assert u["by_task"][0]["key"] == "answer"


def test_budget_blocks_new_ai_work(auth_client, fake_llm, monkeypatch):
    from app import config

    c = auth_client
    uid = _uid(c)
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.01")
    config.get_settings.cache_clear()
    aid = c.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    c.post(f"/api/v1/assignments/{aid}/confirm-extraction")
    with usage_scope(uid, "answer"):
        record(
            provider="anthropic",
            model="claude-opus-5-5",
            served_model="claude-opus-5-5",
            input_tokens=10_000,
            output_tokens=10_000,
            latency_ms=1,
            status="ok",
        )
    r = c.post(f"/api/v1/assignments/{aid}/generate", json={})
    assert r.status_code == 402 and r.json()["error"]["code"] == "budget_exceeded"
    b = c.get("/api/v1/usage").json()["budget"]
    assert b["exceeded"] is True and b["limit_usd"] == 0.01
