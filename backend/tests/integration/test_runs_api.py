"""Full API + DB + Redis + worker flow. The model is the ScriptedProvider MOCK fixture."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from jaxon.api.app import create_app
from jaxon.workers.run_worker import Worker
from tests.fixtures import call, reply

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(runtime):
    app = create_app(runtime=runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_readiness(client):
    r = await client.get("/health/ready")
    body = r.json()
    assert r.status_code == 200, body
    assert body["checks"]["pgvector"]["ok"] and body["checks"]["migrations"]["revision"] == "0001"


async def test_sync_run_with_tool_call(client, runtime):
    runtime.provider.responses = [
        call("math__calculate", {"expression": "19*3"}),
        reply("19*3 = 57"),
    ]
    r = await client.post(
        "/api/v1/runs", json={"agent": "analyst", "input": "19*3?", "mode": "sync"}
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "succeeded" and run["output"] == "19*3 = 57"
    assert [s["kind"] for s in run["steps"]] == [
        "model_response",
        "skill_call",
        "skill_result",
        "model_response",
    ]
    assert run["steps"][2]["payload"]["result"]["result"] == 57
    assert run["usage"]["input_tokens"] == 20

    got = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert got["id"] == run["id"] and len(got["steps"]) == 4
    listing = (await client.get("/api/v1/runs")).json()
    assert listing["total"] == 1


async def test_unknown_agent_and_run(client):
    r = await client.post("/api/v1/runs", json={"agent": "ghost", "input": "x"})
    assert r.status_code == 404
    r = await client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_queued_run_processed_by_worker(runtime, client):
    runtime.provider.responses = [reply("done via worker")]
    c = client
    if True:
        r = await c.post("/api/v1/runs", json={"agent": "general", "input": "hello"})
        assert r.status_code == 201 and r.json()["status"] == "queued"
        run_id = r.json()["id"]
        assert await runtime.queue.depth() == 1

        worker = Worker(runtime, worker_id="test-worker")
        assert await worker.process_one(block_seconds=1) is True
        assert await runtime.queue.depth() == 0

        run = (await c.get(f"/api/v1/runs/{run_id}")).json()
        assert run["status"] == "succeeded" and run["output"] == "done via worker"
        # idempotent: re-enqueue a finished run, worker must not re-execute it
        await runtime.queue.enqueue(uuid.UUID(run_id))
        assert await worker.process_one(block_seconds=1) is True
        assert (await c.get(f"/api/v1/runs/{run_id}")).json()["output"] == "done via worker"


async def test_worker_recovers_inflight_jobs(runtime):
    rid = uuid.uuid4()
    await runtime.queue.enqueue(rid)
    assert await runtime.queue.dequeue("w1", block_seconds=1) == rid  # "crash" before ack
    assert await runtime.queue.depth() == 0
    assert await runtime.queue.recover("w1") == 1
    assert await runtime.queue.depth() == 1


async def test_failed_run_records_provider_config_error(runtime, client, monkeypatch):
    """Uses the REAL Anthropic provider with no key: run must fail cleanly, not crash."""
    from jaxon.providers.anthropic_provider import AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    runtime.provider = AnthropicProvider(runtime.settings)  # no API key configured
    r = await client.post("/api/v1/runs", json={"agent": "general", "input": "hi", "mode": "sync"})
    body = r.json()
    assert body["status"] == "failed" and body["error"]["code"] == "configuration_error"


async def test_custom_agent_crud(client, runtime):
    spec = {
        "name": "lead-scout",
        "description": "find leads",
        "system_prompt": "Find leads.",
        "skills": ["text.extract_contacts", "memory.remember"],
        "max_steps": 5,
    }
    r = await client.post("/api/v1/agents", json=spec)
    assert r.status_code == 201, r.text
    assert (await client.post("/api/v1/agents", json=spec)).status_code == 409
    bad = {**spec, "name": "bad-skills", "skills": ["nope.nope"]}
    assert (await client.post("/api/v1/agents", json=bad)).status_code == 422
    assert (
        await client.post("/api/v1/agents", json={**spec, "name": "general"})
    ).status_code == 409
    names = [a["name"] for a in (await client.get("/api/v1/agents")).json()]
    assert {"general", "researcher", "analyst", "lead-scout"} <= set(names)

    runtime.provider.responses = [reply("scouted")]
    run = await client.post(
        "/api/v1/runs", json={"agent": "lead-scout", "input": "go", "mode": "sync"}
    )
    assert run.json()["status"] == "succeeded"
    assert {t.name for t in runtime.provider.requests[0].tools} == {
        "text__extract_contacts",
        "memory__remember",
    }

    assert (await client.delete("/api/v1/agents/lead-scout")).status_code == 204
    assert (await client.get("/api/v1/agents/lead-scout")).status_code == 404


async def test_memory_api(client):
    r = await client.post(
        "/api/v1/memory", json={"content": "Opportunity: AI bookkeeping for dentists"}
    )
    assert r.status_code == 201
    hits = (
        await client.post("/api/v1/memory/search", json={"query": "dentists bookkeeping"})
    ).json()["hits"]
    assert hits[0]["content"].startswith("Opportunity")
    assert (await client.delete(f"/api/v1/memory/{hits[0]['id']}")).status_code == 204


async def test_system_info(client):
    info = (await client.get("/api/v1/system")).json()
    assert info["skills"] >= 8 and info["queue_depth"] == 0
    assert {i["name"] for i in info["integrations"]} == {"anthropic", "github", "web"}
