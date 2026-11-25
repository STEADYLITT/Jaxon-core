"""Phase 08: model adapters, router, planner, metering. No real network calls.

The Anthropic and OpenAI adapters run through the REAL official SDKs, with an
httpx2.MockTransport standing in for the network, so request shape and response
parsing are checked against the SDK itself.
"""

import json
import uuid

import anthropic
import httpx2
import openai
import pytest
from conftest import auth, events, run_state
from sqlalchemy import select, text

from jackson_api.models import ModelUsage, RunEvent, Task
from jackson_api.models_ai import ModelRefused, ModelRequest, ModelRequestInvalid, ModelRouter
from jackson_api.models_ai.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from jackson_api.models_ai.base import cost_micros
from jackson_api.models_ai.mock_provider import MockProvider
from jackson_api.models_ai.openai_provider import OpenAICompatibleProvider
from jackson_api.planner import PLAN_SCHEMA, persona
from jackson_api.worker import Worker

FAKE_KEY = "test-key-not-real-0000"  # must never appear in any stored output
PLAN = {"summary": "Test a $29 bundle with 20 buyers before building anything.",
        "steps": [{"title": "Pull 3 competitor price points", "category": "read",
                   "why": "Anchor the price"},
                  {"title": "Run a $50 ad test", "category": "spend", "why": "Measure demand"}],
        "assumptions": ["Audience overlaps STU students (hypothesis)"],
        "risks": ["Ad spend with no conversions"], "evidence_needed": ["Landing page CVR"]}


# --- Fake transports ----------------------------------------------------------------

class Recorder:
    def __init__(self, status=200, body=None, headers=None):
        self.requests: list[httpx2.Request] = []
        self.status, self.body, self.headers = status, body, headers or {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body, headers=self.headers)

    @property
    def last_json(self):
        return json.loads(self.requests[-1].content)


def anthropic_message(text_out, model="claude-opus-5-5", stop="end_turn", in_tok=1200,
                      out_tok=800, stop_details=None):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text_out}], "stop_reason": stop,
            "stop_sequence": None, "stop_details": stop_details,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}}


def anthropic_provider(rec):
    client = anthropic.Anthropic(
        api_key=FAKE_KEY, max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(rec)))
    return AnthropicProvider(client=client)


def openai_provider(rec, prices=(1.0, 2.0)):
    client = openai.OpenAI(
        api_key=FAKE_KEY, max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(rec)))
    return OpenAICompatibleProvider("openai", "gpt-test", client=client, prices=prices)


REQ = ModelRequest(system="sys", user="hi", effort="high", json_schema=PLAN_SCHEMA)


# --- Anthropic adapter (real SDK) ------------------------------------------------------

def test_anthropic_request_shape_follows_current_api():
    rec = Recorder(body=anthropic_message(json.dumps(PLAN)), headers={"request-id": "req_1"})
    resp = anthropic_provider(rec).complete(REQ, "claude-opus-5-5")
    body = rec.last_json
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in rec.requests[-1].headers["anthropic-beta"]
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in body  # 400s on this model / API version
    assert body["messages"] == [{"role": "user", "content": "hi"}]  # no prefill
    assert json.loads(resp.text) == PLAN
    assert resp.mode == "live" and resp.provider == "anthropic"
    assert resp.request_id == "req_1"


def test_anthropic_cost_uses_the_model_that_actually_served():
    rec = Recorder(body=anthropic_message(json.dumps(PLAN), model="claude-opus-4-8"))
    resp = anthropic_provider(rec).complete(REQ, "claude-opus-5-5")  # served by a fallback
    assert resp.model == "claude-opus-4-8"
    assert resp.cost_usd_micros == cost_micros("claude-opus-4-8", 1200, 800) == 26000


def test_anthropic_refusal_is_not_retried():
    rec = Recorder(body=anthropic_message("", stop="refusal",
                                          stop_details={"type": "refusal", "category": "cyber",
                                                        "explanation": "x"}))
    with pytest.raises(ModelRefused, match="cyber"):
        anthropic_provider(rec).complete(REQ, "claude-opus-5-5")


def test_anthropic_errors_are_classified():
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    with pytest.raises(ModelRequestInvalid):
        anthropic_provider(Recorder(400, err)).complete(REQ, "claude-opus-5-5")
    with pytest.raises(anthropic.RateLimitError):  # retryable -> worker backs off
        anthropic_provider(Recorder(429, err)).complete(REQ, "claude-opus-5-5")
    with pytest.raises(anthropic.InternalServerError):
        anthropic_provider(Recorder(500, err)).complete(REQ, "claude-opus-5-5")


def test_cost_math():
    assert cost_micros("claude-opus-5-5", 1_000_000, 0) == 4_000_000  # $4 per MTok in
    assert cost_micros("claude-opus-5-5", 0, 1_000_000) == 20_000_000  # $20 per MTok out
    assert cost_micros("unknown-model", 10, 10) is None


# --- OpenAI-compatible adapter (real SDK) ------------------------------------------------

def openai_completion(content, model="gpt-test", refusal=None):
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content,
                                     "refusal": refusal}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}


def test_openai_request_shape_and_cost():
    rec = Recorder(body=openai_completion(json.dumps(PLAN)))
    resp = openai_provider(rec).complete(REQ, "gpt-test")
    body = rec.last_json
    assert body["model"] == "gpt-test" and body["messages"][0]["role"] == "system"
    assert body["response_format"]["type"] == "json_schema"
    assert json.loads(resp.text) == PLAN
    assert resp.cost_usd_micros == 100 * 1 + 50 * 2


def test_openai_refusal_and_bad_request():
    with pytest.raises(ModelRefused):
        openai_provider(Recorder(body=openai_completion(None, refusal="no"))).complete(
            REQ, "gpt-test")
    err = {"error": {"message": "bad", "type": "invalid_request_error"}}
    with pytest.raises(ModelRequestInvalid):
        openai_provider(Recorder(400, err)).complete(REQ, "gpt-test")


def test_openai_unavailable_without_model_or_prices(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert not OpenAICompatibleProvider.openai_from_env().available()  # no model, no prices
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    assert not OpenAICompatibleProvider.openai_from_env().available()  # still no prices
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MTOK", "1")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MTOK", "2")
    assert OpenAICompatibleProvider.openai_from_env().available()


# --- Router --------------------------------------------------------------------------

def test_no_provider_configured_routes_to_labeled_mock():
    route = ModelRouter().select("planning")
    assert route.provider.name == "mock" and "not configured" in route.reason


def test_default_route_is_claude_opus_5_5_with_effort_by_profile():
    r = ModelRouter(providers={"anthropic": anthropic_provider(Recorder())})
    assert (r.select("planning").model, r.select("planning").effort) == ("claude-opus-5-5", "high")
    assert r.select("fast").effort == "low" and r.select("fast").model == "claude-opus-5-5"


def test_forced_provider(monkeypatch):
    rec = Recorder(body=openai_completion("x"))
    r = ModelRouter(providers={"openai": openai_provider(rec)})
    monkeypatch.setenv("JACKSON_MODEL_PROVIDER", "openai")
    route = r.select("standard")
    assert route.provider.name == "openai" and route.model == "gpt-test"
    monkeypatch.setenv("JACKSON_MODEL_PROVIDER", "local")  # not configured
    assert r.select("standard").provider.name == "mock"


def test_persona_is_separate_from_policy():
    p = persona()
    assert "Jackson Vale" in p and "Money first" in p
    assert "autonomy_level" not in p and "ALWAYS_APPROVE" not in p  # no policy in persona


# --- Planner end to end through the worker ---------------------------------------------------

def start_plan(client, world, make_token, objective="Find a product for the Shopify store"):
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=auth(make_token, world.alice),
                    json={"type": "PLAN", "objective": objective, "business_id": str(world.biz_a),
                          "constraints": ["no paid ads over $50"],
                          "idempotency_key": f"k-{uuid.uuid4().hex}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_plan_without_keys_is_mock_and_free(client, world, make_token, maker, owner_db, drain):
    run_id = start_plan(client, world, make_token)
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    out = tasks[0].output
    assert run.status == "succeeded"
    assert out["mode"] == "mock" and out["provider"] == "mock"
    assert out["plan"]["summary"].startswith("[MOCK]")
    [usage] = owner_db.scalars(select(ModelUsage).where(ModelUsage.run_id == run.id)).all()
    assert usage.mode == "mock" and usage.cost_usd_micros == 0
    assert "model.called" in events(owner_db, run_id)


def test_plan_with_claude_records_cost_and_sends_context(
        client, world, make_token, maker, owner_db, drain):
    rec = Recorder(body=anthropic_message(json.dumps(PLAN)))
    router = ModelRouter(providers={"anthropic": anthropic_provider(rec)})
    run_id = start_plan(client, world, make_token)
    Worker(maker, router=router, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded"
    assert tasks[0].output["plan"] == PLAN and tasks[0].output["mode"] == "live"
    body = rec.last_json
    assert "Jackson Vale" in body["system"]
    assert "Find a product for the Shopify store" in body["messages"][0]["content"]
    assert "no paid ads over $50" in body["messages"][0]["content"]
    assert body["output_config"]["effort"] == "high"  # planning profile
    [usage] = owner_db.scalars(select(ModelUsage).where(ModelUsage.run_id == run.id)).all()
    assert (usage.mode, usage.model, usage.cost_usd_micros) == ("live", "claude-opus-5-5", 20800)


def test_plan_steps_are_data_not_actions(client, world, make_token, maker, owner_db, drain):
    """A plan containing 'spend' steps creates no tasks, approvals or ledger entries."""
    rec = Recorder(body=anthropic_message(json.dumps(PLAN)))
    run_id = start_plan(client, world, make_token)
    Worker(maker, router=ModelRouter(providers={"anthropic": anthropic_provider(rec)}),
           retry_base_seconds=0).run_once()
    _, tasks = run_state(owner_db, run_id)
    assert [t.kind for t in tasks] == ["plan"]
    for table in ("approvals", "ledger_entries"):
        n = owner_db.execute(text(f"SELECT count(*) FROM {table} WHERE run_id = :r"),
                             {"r": run_id}).scalar()
        assert n == 0


def test_model_budget_exhausted_stops_before_calling(
        client, world, make_token, maker, owner_db, drain):
    rec = Recorder(body=anthropic_message(json.dumps(PLAN)))
    router = ModelRouter(providers={"anthropic": anthropic_provider(rec)})
    w = Worker(maker, router=router, retry_base_seconds=0, model_monthly_budget_usd=0.02)
    start_plan(client, world, make_token)
    w.run_once()  # first plan costs $0.0208 > $0.02 budget
    assert len(rec.requests) == 1
    w2_run = start_plan(client, world, make_token)
    before = len(rec.requests)
    w.run_once()
    run, tasks = run_state(owner_db, w2_run)
    assert len(rec.requests) == before  # no model call was made
    assert run.status == "failed" and "monthly model budget used up" in tasks[0].error


def test_rate_limit_is_retried_by_the_worker(client, world, make_token, maker, owner_db, drain):
    err = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    responses = [(429, err), (200, anthropic_message(json.dumps(PLAN)))]

    def handler(request):
        status, body = responses.pop(0)
        return httpx2.Response(status, json=body)

    client_ = anthropic.Anthropic(api_key=FAKE_KEY, max_retries=0,
                                  http_client=anthropic.DefaultHttpxClient(
                                      transport=httpx2.MockTransport(handler)))
    router = ModelRouter(providers={"anthropic": AnthropicProvider(client=client_)})
    run_id = start_plan(client, world, make_token)
    w = Worker(maker, router=router, retry_base_seconds=0)
    w.run_once()
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].attempts == 2


def test_secrets_never_stored(client, world, make_token, maker, owner_db, drain):
    """Spec acceptance: secrets absent from logs. The API key never lands in any table."""
    rec = Recorder(body=anthropic_message(json.dumps(PLAN)))
    run_id = start_plan(client, world, make_token)
    Worker(maker, router=ModelRouter(providers={"anthropic": anthropic_provider(rec)}),
           retry_base_seconds=0).run_once()
    assert any(FAKE_KEY in v for v in rec.requests[-1].headers.values())  # really used...
    blobs = [json.dumps(t.output) + (t.error or "") for t in
             owner_db.scalars(select(Task).where(Task.run_id == uuid.UUID(run_id)))]
    blobs += [json.dumps(e.data) for e in
              owner_db.scalars(select(RunEvent).where(RunEvent.run_id == uuid.UUID(run_id)))]
    blobs += [owner_db.execute(text("SELECT string_agg(row_to_json(a)::text, '') "
                                    "FROM audit_events a")).scalar() or ""]
    blobs += [owner_db.execute(text("SELECT string_agg(row_to_json(m)::text, '') "
                                    "FROM model_usage m")).scalar() or ""]
    assert all(FAKE_KEY not in b for b in blobs)  # ...but never stored


def test_mock_provider_is_always_labeled():
    resp = MockProvider().complete(REQ, "mock")
    assert resp.mode == "mock" and resp.cost_usd_micros == 0
    assert all(s["title"].startswith("[MOCK]") for s in json.loads(resp.text)["steps"])


def test_usage_is_tenant_isolated_and_append_only(app_session, maker, world):
    from sqlalchemy.exc import DBAPIError

    from jackson_api.db import bind_tenant
    bind_tenant(app_session, world.tenant_b)
    assert world.tenant_a not in set(app_session.scalars(select(ModelUsage.tenant_id)))
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE model_usage SET cost_usd_micros = 0"))
