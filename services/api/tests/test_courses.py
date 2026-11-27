"""ADR-040: Course Factory end to end. "AI trading course is trending, build it" ->
outline -> lessons -> student platform draft -> sales page draft -> Launch proposal."""

import json
import os
import uuid
from dataclasses import replace

import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import courses
from jackson_api.models import Business, Course, Event, Launch, MemoryRecord
from jackson_api.models_ai.mock_provider import MockProvider
from jackson_api.worker import Worker

OUTLINE = {
    "title": "AI for Traders", "subtitle": "Use AI tools to study the markets, step by step.",
    "audience": "Beginner traders who want a research routine.",
    "promise": "Build a repeatable research routine with AI.\n\nEducation only.",
    "outcomes": ["Write a research prompt", "Read a backtest report"],
    "modules": [
        {"title": "Foundations", "outcome": "Know what AI can and can't do",
         "lessons": [{"title": "What AI is", "objective": "Explain a model in plain words"},
                     {"title": "Risk first", "objective": "Size a position"}]},
        {"title": "Research", "outcome": "Run a weekly routine",
         "lessons": [{"title": "Prompts", "objective": "Write a research prompt"}]},
    ],
}
LESSON = {"script": "Markets are uncertain.\n\nThis lesson shows a routine.",
          "slides": [{"heading": "Key idea", "bullets": ["Risk first", "Then research"]}],
          "quiz": [{"question": "What comes first?", "choices": ["Risk", "Profit"],
                    "answer_index": 0, "why": "You can only control risk."}],
          "worksheet": ["Write your max loss per trade."]}


@pytest.fixture(autouse=True)
def _browser(monkeypatch):
    if not os.environ.get("BROWSER_EXECUTABLE") and os.path.exists("/opt/pw-browsers/chromium"):
        monkeypatch.setenv("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")


def scripted(monkeypatch, lesson=LESSON):
    """A model that answers like a real one (labeled live in the test only)."""
    real = MockProvider.complete

    def complete(self, req, model):
        r = real(self, req, model)
        props = (req.json_schema or {}).get("properties", {})
        if "modules" in props:
            return replace(r, text=json.dumps(OUTLINE), mode="live")
        if "script" in props:
            return replace(r, text=json.dumps(lesson), mode="live")
        return r
    monkeypatch.setattr(MockProvider, "complete", complete)


def courses_business(owner_db, world):
    b = Business(tenant_id=world.tenant_a, name="AI Wealth Academy", kind="education",
                 line="courses")
    owner_db.add(b)
    owner_db.commit()
    return b.id


def command(client, world, make_token, text):
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=auth(make_token, world.alice),
                    json={"type": "PLAN", "objective": text, "route": True,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ai_trading_course_end_to_end(client, world, make_token, maker, owner_db, drain,
                                      monkeypatch):
    scripted(monkeypatch)
    biz = courses_business(owner_db, world)
    run_id = command(client, world, make_token, "AI trading course is trending, build it")
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()  # plan + route
    routed = owner_db.scalars(select(Event).where(Event.run_id == uuid.UUID(run_id),
                                                  Event.type == "command.routed")).one()
    assert routed.payload["line"] == "courses" and routed.payload["agent"] == "Course Builder"
    w.run_once()  # course_build
    run, tasks = run_state(owner_db, run_id)
    assert [t.kind for t in tasks] == ["plan", "course_build"]
    assert tasks[1].status == "succeeded", tasks[1].error
    out = tasks[1].output
    assert out["modules"] == 2 and out["lessons"] == 3 and out["model_calls"] == 4
    assert out["passed"] is True, out["problems"]  # HTML, a11y, claims, Chromium desktop+phone

    owner_db.expire_all()
    c = owner_db.get(Course, uuid.UUID(out["course_id"]))
    assert c.business_id == biz and c.status == "ready" and c.mode == "live"
    assert {"index.html", "dashboard.html", "lesson-1-1.html", "lesson-1-2.html",
            "lesson-2-1.html", "certificate.html", "privacy.html"} == set(c.platform_files)
    assert "WHOP_API_KEY" in c.platform_files["index.html"]  # sign-in says it isn't live
    assert "Show the answer" in c.platform_files["lesson-1-1.html"]
    assert "not set" in c.sales_files["index.html"]  # no price given -> none invented
    assert "Nothing can be bought" in c.sales_files["index.html"]

    la = owner_db.get(Launch, c.launch_id)
    assert la.status == "proposed" and la.proposed_by == "jackson" and la.business_id == biz
    assert la.total_budget_cents == 0 and "approve once" in la.plan
    mem = owner_db.scalars(select(MemoryRecord).where(MemoryRecord.tenant_id == world.tenant_a,
                                                      MemoryRecord.source_ref == str(run.id)))
    assert any("AI for Traders" in m.content for m in mem)

    h = auth(make_token, world.alice)
    listed = client.get(f"/tenants/{world.tenant_a}/courses", headers=h).json()
    assert listed[0]["launch"]["status"] == "proposed" and listed[0]["lessons"] == 3
    page = client.get(f"/tenants/{world.tenant_a}/courses/{c.id}/files/platform/"
                      "dashboard.html", headers=h)
    assert page.status_code == 200 and "script-src" not in page.headers[
        "content-security-policy"] and "default-src 'none'" in page.headers[
        "content-security-policy"]
    assert "0 of 3 lessons complete" in page.text
    assert client.get(f"/tenants/{world.tenant_b}/courses/{c.id}",
                      headers=auth(make_token, world.bob)).status_code in (403, 404)


def test_income_promises_fail_the_checks(client, world, make_token, maker, owner_db, drain,
                                         monkeypatch):
    scripted(monkeypatch, lesson={**LESSON, "script": "Follow this and get rich. Guaranteed "
                                  "income every month."})
    courses_business(owner_db, world)
    r = client.post(f"/tenants/{world.tenant_a}/courses", headers=auth(make_token, world.alice),
                    json={"brief": "AI trading course", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202
    Worker(maker, retry_base_seconds=0).run_once()
    owner_db.expire_all()
    c = owner_db.scalars(select(Course).where(Course.tenant_id == world.tenant_a)).one()
    assert c.status == "needs_fixes"
    assert any("guaranteed" in p.lower() or "get rich" in p.lower()
               for p in c.checks["platform"]["problems"])


def test_no_model_key_still_runs_and_is_labeled_mock(client, world, make_token, maker,
                                                    owner_db, drain):
    courses_business(owner_db, world)
    client.post(f"/tenants/{world.tenant_a}/courses", headers=auth(make_token, world.alice),
                json={"brief": "AI trading course for $49", "idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    owner_db.expire_all()
    c = owner_db.scalars(select(Course).where(Course.tenant_id == world.tenant_a)).one()
    assert c.mode == "mock" and "[MOCK]" in c.outline["modules"][0]["title"]
    assert c.price == "49" and "$49" in c.sales_files["index.html"]
    assert "[MOCK]" in owner_db.get(Launch, c.launch_id).plan
    assert owner_db.scalar(select(MemoryRecord.id).where(
        MemoryRecord.tenant_id == world.tenant_a,
        MemoryRecord.created_by_agent == "course_build")) is None  # nothing learned from a mock


def test_outline_is_capped():
    big = {**OUTLINE, "modules": [{"title": f"M{i}", "outcome": "o",
                                   "lessons": [{"title": f"L{j}", "objective": "x"}
                                               for j in range(9)]} for i in range(9)]}
    o = courses.normalize_outline(big, mock=False)
    assert len(o["modules"]) == courses.MAX_MODULES
    assert all(len(m["lessons"]) == courses.MAX_LESSONS for m in o["modules"])


def test_courses_building_lists_the_build_and_its_launch(client, world, make_token, maker,
                                                         owner_db, drain, monkeypatch):
    from test_city_ui import city, click3d

    scripted(monkeypatch)
    courses_business(owner_db, world)
    client.post(f"/tenants/{world.tenant_a}/courses", headers=auth(make_token, world.alice),
                json={"brief": "AI trading course", "idempotency_key": uuid.uuid4().hex})
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    w.city_heartbeat()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "building-courses")
        page.wait_for_selector("[data-line=courses] .courses li:has-text('AI for Traders')")
        txt = page.text_content("[data-line=courses] .courses")
        assert "3 lessons" in txt and "waiting for your approval" in txt
        assert "passed its checks" in txt
        assert problems == [], problems
