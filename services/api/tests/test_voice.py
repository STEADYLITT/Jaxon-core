"""ADR-042: AI voice via ElevenLabs. Claims check, cost first, approval, then an mp3 artifact
labeled AI voice. ElevenLabs' network is faked with its documented shapes."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import voice
from jackson_api.models import Approval, Artifact, Course, LedgerEntry
from jackson_api.worker import Worker

KEY = "sk_" + "v" * 40
MP3 = b"ID3\x04fake-mp3-frames"


class FakeEleven:
    def __init__(self):
        self.calls = []

    def tts(self):
        return [json.loads(c.content) for c in self.calls if "/text-to-speech/" in c.url.path]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["xi-api-key"] == KEY
        p = req.url.path
        if p == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "creator", "character_count": 1200,
                                             "character_limit": 100000})
        if p == "/v2/voices":
            return httpx.Response(200, json={"voices": [
                {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "category": "premade"}],
                "has_more": False})
        if p.startswith("/v1/text-to-speech/"):
            assert req.url.params["output_format"] == "mp3_44100_128"
            if p.endswith("/bad_voice"):
                return httpx.Response(422, json={"detail": {"message": "voice not found"}})
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        return httpx.Response(404, json={"detail": "unexpected"})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeEleven()
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    for v in ("ELEVENLABS_MODEL", "ELEVENLABS_CENTS_PER_1K_CHARS"):
        monkeypatch.delenv(v, raising=False)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(voice, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def ask(client, world, alice, **body):
    return client.post(f"/tenants/{world.tenant_a}/voice", headers=alice,
                       json={"voice_id": "JBFqnCBsd6RMkjVDRZzb",
                             "idempotency_key": uuid.uuid4().hex, **body})


def test_estimate_and_chunks(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_CENTS_PER_1K_CHARS", raising=False)
    assert voice.estimate_cents(10_000, "eleven_multilingual_v2") == 80  # $0.08 / 1k chars
    assert voice.estimate_cents(10_000, "eleven_flash_v2_5") == 40
    assert voice.estimate_cents(3, "eleven_flash_v2_5") == 1
    monkeypatch.setenv("ELEVENLABS_CENTS_PER_1K_CHARS", "30")
    assert voice.estimate_cents(1000) == 30
    parts = voice.chunks("A. " * 6000, 10_000)
    assert all(len(p) <= 10_000 for p in parts) and len(parts) == 2


def test_connection_test_shows_characters_left(client, world, alice, live):
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/voice/test", headers=alice)
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert r.json()["result"]["characters_left"] == 98800 and KEY not in r.text
    v = client.get(f"/tenants/{world.tenant_a}/voice/voices", headers=alice).json()
    assert v["voices"][0]["name"] == "George"


def test_ad_voiceover_cost_first_then_approval_then_audio(client, world, alice, maker,
                                                          owner_db, drain, live):
    script = "Meet Mag Grip.\n\nIt holds your bits so you don't drop them."
    r = ask(client, world, alice, purpose="ad", text=script, title="Mag Grip ad",
            business_id=str(world.biz_a))
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["estimate_cents"] == 1 and out["characters"] == len(script)
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, out["run_id"])
    assert tasks[0].kind == "voice_generate" and tasks[0].status == "waiting_approval"
    assert live.tts() == []  # nothing generated or paid before approval
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(out["run_id"])))
    assert ap.category == "spend" and "ElevenLabs" in ap.description
    assert client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                       json={"decision": "approve"}).status_code == 200
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, out["run_id"])
    assert run.status == "succeeded", tasks[0].error
    res = tasks[0].output
    assert res["label"] == "AI voice" and res["cost_cents"] == 1
    assert live.tts() == [{"text": script, "model_id": "eleven_multilingual_v2"}]
    art = owner_db.get(Artifact, uuid.UUID(res["artifact_id"]))
    assert art.kind == "audio" and art.content_type == "audio/mpeg"
    assert art.meta["ai_generated"] is True and art.size_bytes == len(MP3)
    kinds = {e.kind: e.amount_cents for e in owner_db.scalars(select(LedgerEntry).where(
        LedgerEntry.run_id == uuid.UUID(out["run_id"])))}
    assert kinds == {"reserve": 1, "commit": 1}


def test_claims_fail_before_any_cost(client, world, alice, maker, owner_db, drain, live):
    r = ask(client, world, alice, purpose="ad", text="Make $500 a day, guaranteed income.",
            business_id=str(world.biz_a))
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert tasks[0].status == "failed" and "claims check" in tasks[0].error
    assert owner_db.scalar(select(Approval.id).where(
        Approval.run_id == uuid.UUID(r.json()["run_id"]))) is None
    assert live.tts() == []


def test_narrates_a_course_lesson(client, world, alice, maker, owner_db, drain, live):
    from test_courses import LESSON, OUTLINE
    c = Course(tenant_id=world.tenant_a, business_id=world.biz_a, brief="b", title="AI",
               status="ready", mode="live", outline=OUTLINE,
               lessons=[{"module": 0, "lesson": 1, **LESSON}], platform_files={},
               sales_files={}, checks={})
    owner_db.add(c)
    owner_db.commit()
    assert ask(client, world, alice, course_id=str(c.id), module=0, lesson=0
               ).status_code == 422  # that lesson has no script
    r = ask(client, world, alice, course_id=str(c.id), module=0, lesson=1)
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    assert "Risk first" in ap.description
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["course_id"] == str(c.id) and live.tts()[0]["text"] == LESSON["script"]


def test_mock_makes_no_audio(client, world, alice, maker, owner_db, drain, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = ask(client, world, alice, text="Hello there.", business_id=str(world.biz_a))
    assert r.json()["connector"] == "mock"
    Worker(maker, retry_base_seconds=0).run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    out = tasks[0].output
    assert out["mock"] is True and out["artifact_id"] is None and out["cost_cents"] == 0
    assert "[MOCK]" in out["summary"]
