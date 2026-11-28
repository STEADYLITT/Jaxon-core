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
    with city(client, world, make_token(world.alice), **kw) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=30_000)
        page.wait_for_timeout(5000)  # let the intro / camera settle
        page.screenshot(path=str(OUT / f"{where}-idle.png"))
        r = client.post(f"/tenants/{world.tenant_a}/commands",
                        headers=auth(make_token, world.alice),
                        json={"type": "PLAN", "objective": "AI trading course is trending, "
                              "build it", "route": True, "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 201, r.text
        Worker(maker, retry_base_seconds=0).run_once()
        until(page, "window.__city && (window.__city.envelopes > 0 || window.__city.beams > 0)",
              timeout=20_000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / f"{where}-event.png"))
        assert not [p for p in problems if "WebGL" not in p], problems
