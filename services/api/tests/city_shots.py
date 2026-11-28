"""Screenshots of the City for PR proof (not collected by default: run it by path).

    CITY_SHOTS=/some/dir pytest tests/city_shots.py

Desktop (1440x900) and iPhone 15, idle and right after a real command. Everything on
screen comes from real rows written by the fixtures and the worker (Rule Zero)."""

import os
import uuid
from pathlib import Path

import pytest
from conftest import auth
from test_city_ui import city, until

from jackson_api.models import Business
from jackson_api.seed import propose_line_agents
from jackson_api.worker import Worker

OUT = Path(os.environ.get("CITY_SHOTS", "/tmp/city-shots"))


def seed(owner_db, world, maker):
    for name, kind, line in [("AI Wealth Academy", "education", "courses"),
                             ("Steady Growth OS", "agency", "marketing")]:
        owner_db.add(Business(tenant_id=world.tenant_a, name=name, kind=kind, line=line))
    owner_db.flush()
    propose_line_agents(owner_db, world.tenant_a, None)
    owner_db.commit()
    Worker(maker).city_heartbeat()


@pytest.mark.parametrize("where", ["desktop", "phone"])
def test_shots(client, world, make_token, maker, owner_db, drain, where):
    OUT.mkdir(parents=True, exist_ok=True)
    seed(owner_db, world, maker)
    kw = {"device": "iPhone 15"} if where == "phone" else {"viewport": {"width": 1440,
                                                                         "height": 900}}
    # ?fxslow: effects last 8x longer so the slow software renderer can photograph them
    with city(client, world, make_token(world.alice), query="?fxslow", **kw) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=30_000)
        page.wait_for_timeout(5000)  # let the intro / camera settle
        page.screenshot(path=str(OUT / f"{where}-idle.png"))
        r = client.post(f"/tenants/{world.tenant_a}/commands",
                        headers=auth(make_token, world.alice),
                        json={"type": "PLAN", "objective": "AI trading course is trending, "
                              "build it", "route": True, "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 201, r.text
        w = Worker(maker, retry_base_seconds=0)
        w.run_once()  # Jaxon plans and routes (envelopes fly)
        w.run_once()  # the department does the work (a beam rises, a report flies back)
        until(page, "window.__city && window.__city.beams > 0", timeout=30_000)
        for k in range(4):  # a short burst while the effects are in the air
            page.screenshot(path=str(OUT / f"{where}-event{'' if k == 0 else k}.png"))
            page.wait_for_timeout(1500)
        assert not [p for p in problems if "WebGL" not in p], problems


@pytest.mark.parametrize("where", ["desktop", "phone"])
def test_intro_and_office_shots(client, world, make_token, maker, owner_db, drain, where):
    from test_city_ui import click3d

    OUT.mkdir(parents=True, exist_ok=True)
    seed(owner_db, world, maker)
    w = Worker(maker, retry_base_seconds=0)
    client.post(f"/tenants/{world.tenant_a}/content/generate",
                headers=auth(make_token, world.alice),
                json={"brief": "grip tips", "channel": "x", "count": 1,
                      "business_id": str(world.biz_a)})
    w.run_once()  # a real Content Writer with a real task log for its monitor
    kw = {"device": "iPhone 15"} if where == "phone" else {"viewport": {"width": 1440,
                                                                         "height": 900}}
    with city(client, world, make_token(world.alice), query="?intro&fxslow",
              **kw) as (page, problems):
        until(page, "document.documentElement.dataset.cityIntro === 'playing'", 30_000)
        for k in range(3):  # the fly-in from space, frame by frame
            page.wait_for_timeout(3000)
            page.screenshot(path=str(OUT / f"{where}-intro{k}.png"))
        until(page, "document.documentElement.dataset.cityIntro === 'done'", 60_000)
        click3d(page, "building-marketing")
        page.click("[data-panel=building] button.floor:has-text('Content')")
        page.wait_for_selector(".floor-bar:has-text('Content')")
        page.wait_for_selector(".screen:has-text('Content Writer')", state="attached")
        page.wait_for_timeout(15_000)  # the eased fly-to is stretched by ?fxslow
        page.screenshot(path=str(OUT / f"{where}-office.png"))
        assert not [p for p in problems if "WebGL" not in p], problems
