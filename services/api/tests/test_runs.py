"""Phase 05 acceptance: typed commands, idempotency, cancellation, tenant scope, SSE.

Spec vol. 06 acceptance tests covered here:
- "repeated request does not duplicate external action" (idempotent commands)
- "cancellation prevents new tasks"
- "cross-tenant access denied" (for runs and events)
"""

import uuid

import pytest
from conftest import auth
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from jackson_api.db import bind_tenant
from jackson_api.models import RunEvent, Task, WorkflowRun
from jackson_api.runs import CommandError, add_task


def cmd(**kw):
    body = {"type": "PLAN", "objective": "Find a product opportunity",
            "idempotency_key": f"key-{uuid.uuid4().hex}"}
    body.update(kw)
    return body


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def post(client, world, headers, body, tenant=None):
    return client.post(f"/tenants/{tenant or world.tenant_a}/commands", json=body, headers=headers)


# --- Creating runs ----------------------------------------------------------------

def test_plan_command_creates_queued_run_with_plan_task(client, world, alice):
    body = cmd(business_id=str(world.biz_a), budget_cents=5000,
               constraints=["no paid ads"], success_criteria=["margin>30%"])
    r = post(client, world, alice, body)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued" and run["paused"] is False
    assert run["command_type"] == "PLAN"
    assert run["budget_cents"] == 5000
    assert [t["kind"] for t in run["tasks"]] == ["plan"]
    assert run["tasks"][0]["status"] == "queued"


def test_get_and_list_runs(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    got = client.get(f"/tenants/{world.tenant_a}/runs/{run['id']}", headers=alice)
    assert got.status_code == 200 and got.json()["id"] == run["id"]
    listed = client.get(f"/tenants/{world.tenant_a}/runs", headers=alice).json()
    assert run["id"] in [x["id"] for x in listed]


@pytest.mark.parametrize("body,code", [
    ({"objective": "   "}, "objective_required"),
    ({"type": "EXECUTE"}, "execute_needs_business"),
    ({"type": "STOP"}, "target_run_required"),
])
def test_invalid_commands_rejected(client, world, alice, body, code):
    r = post(client, world, alice, cmd(**body))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == code


def test_negative_budget_rejected(client, world, alice):
    assert post(client, world, alice, cmd(budget_cents=-1)).status_code == 422


def test_unknown_command_type_rejected(client, world, alice):
    assert post(client, world, alice, cmd(type="LAUNCH_ROCKET")).status_code == 422


def test_business_from_other_tenant_rejected(client, world, alice):
    r = post(client, world, alice, cmd(business_id=str(world.biz_b)))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_business"


def test_execute_without_business_creates_nothing(client, world, alice, app_session):
    post(client, world, alice, cmd(type="EXECUTE", idempotency_key="exec-key-12345"))
    bind_tenant(app_session, world.tenant_a)
    assert app_session.scalar(select(func.count()).select_from(WorkflowRun)
                              .where(WorkflowRun.idempotency_key == "exec-key-12345")) == 0


# --- Idempotency ------------------------------------------------------------------

def test_repeated_command_returns_same_run_without_duplicates(client, world, alice, app_session):
    body = cmd()
    first = post(client, world, alice, body)
    second = post(client, world, alice, body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json()["id"] == first.json()["id"]
    bind_tenant(app_session, world.tenant_a)
    run_id = uuid.UUID(first.json()["id"])
    assert app_session.scalar(select(func.count()).select_from(Task)
                              .where(Task.run_id == run_id)) == 1
    created = app_session.scalar(select(func.count()).select_from(RunEvent).where(
        RunEvent.run_id == run_id, RunEvent.type == "run.created"))
    assert created == 1


def test_same_key_different_request_conflicts(client, world, alice):
    body = cmd()
    assert post(client, world, alice, body).status_code == 201
    r = post(client, world, alice, {**body, "objective": "Something else"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "idempotency_key_reused"


def test_idempotency_keys_are_per_tenant(client, world, alice, make_token):
    body = cmd()
    a = post(client, world, alice, body)
    b = post(client, world, auth(make_token, world.bob), body, tenant=world.tenant_b)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


# --- Cancel / pause / resume --------------------------------------------------------

def test_cancel_cancels_run_and_tasks_and_is_idempotent(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    url = f"/tenants/{world.tenant_a}/runs/{run['id']}/cancel"
    r1 = client.post(url, headers=alice)
    assert r1.status_code == 200
    assert r1.json()["status"] == "canceled"
    assert r1.json()["finished_at"] is not None
    assert {t["status"] for t in r1.json()["tasks"]} == {"canceled"}
    r2 = client.post(url, headers=alice)
    assert r2.status_code == 200 and r2.json()["status"] == "canceled"


def test_stop_command_equals_cancel(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    r = post(client, world, alice, cmd(type="STOP", target_run_id=run["id"]))
    assert r.status_code == 200 and r.json()["status"] == "canceled"


def test_cancellation_prevents_new_tasks(client, world, alice, app_session):
    run_id = uuid.UUID(post(client, world, alice, cmd()).json()["id"])
    client.post(f"/tenants/{world.tenant_a}/runs/{run_id}/cancel", headers=alice)
    bind_tenant(app_session, world.tenant_a)
    run = app_session.get(WorkflowRun, run_id)
    with pytest.raises(CommandError) as e:
        add_task(app_session, run, kind="build", title="should never exist")
    assert e.value.code == "run_finished"


def test_pause_resume_are_idempotent(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    pause = cmd(type="PAUSE", target_run_id=run["id"])
    assert post(client, world, alice, pause).json()["paused"] is True
    assert post(client, world, alice, {**pause, "idempotency_key": "again-12345"}).json()["paused"]
    resumed = post(client, world, alice, cmd(type="RESUME", target_run_id=run["id"])).json()
    assert resumed["paused"] is False and resumed["status"] == "queued"


def test_cannot_pause_finished_run(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    client.post(f"/tenants/{world.tenant_a}/runs/{run['id']}/cancel", headers=alice)
    r = post(client, world, alice, cmd(type="PAUSE", target_run_id=run["id"]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_finished"


# --- Permissions and tenant isolation ---------------------------------------------------

def test_viewer_cannot_issue_commands(client, world, make_token):
    assert post(client, world, auth(make_token, world.carol), cmd()).status_code == 403


def test_viewer_can_read_runs(client, world, alice, make_token):
    run = post(client, world, alice, cmd()).json()
    r = client.get(f"/tenants/{world.tenant_a}/runs/{run['id']}",
                   headers=auth(make_token, world.carol))
    assert r.status_code == 200


def test_cross_tenant_run_access_denied(client, world, alice, make_token):
    run = post(client, world, alice, cmd()).json()
    bob = auth(make_token, world.bob)
    # Bob via his own tenant URL: the run isn't there.
    assert client.get(f"/tenants/{world.tenant_b}/runs/{run['id']}", headers=bob).status_code == 404
    assert client.post(f"/tenants/{world.tenant_b}/runs/{run['id']}/cancel",
                       headers=bob).status_code == 404
    r = post(client, world, bob, cmd(type="STOP", target_run_id=run["id"]), tenant=world.tenant_b)
    assert r.status_code == 404
    # Bob via Alice's tenant URL: not a member.
    assert client.get(f"/tenants/{world.tenant_a}/runs/{run['id']}", headers=bob).status_code == 403
    assert client.get(f"/tenants/{world.tenant_a}/events?timeout=0.2",
                      headers=bob).status_code == 403
    # And the run is still alive.
    assert client.get(f"/tenants/{world.tenant_a}/runs/{run['id']}",
                      headers=alice).json()["status"] == "queued"


def test_rls_hides_runs_without_scope(client, world, alice, app_session):
    post(client, world, alice, cmd())
    for model in (WorkflowRun, Task, RunEvent):
        assert app_session.scalar(select(func.count()).select_from(model)) == 0


def test_run_history_is_append_only(app_session, world):
    bind_tenant(app_session, world.tenant_a)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_session.execute(text("DELETE FROM run_events"))


# --- Server-Sent Events ----------------------------------------------------------------

def _parse_sse(body: str) -> list[dict]:
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line
                      and not line.startswith(":"))
        if "id" in fields:
            out.append(fields)
    return out


def test_events_stream_run_history_in_order(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    client.post(f"/tenants/{world.tenant_a}/runs/{run['id']}/cancel", headers=alice)
    r = client.get(f"/tenants/{world.tenant_a}/events?run_id={run['id']}&timeout=0.5",
                   headers=alice)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert [e["event"] for e in events] == ["run.created", "task.created", "run.state_changed"]
    ids = [int(e["id"]) for e in events]
    assert ids == sorted(ids)


def test_events_resume_after_last_event_id(client, world, alice):
    run = post(client, world, alice, cmd()).json()
    url = f"/tenants/{world.tenant_a}/events?run_id={run['id']}&timeout=0.5"
    first = _parse_sse(client.get(url, headers=alice).text)
    resumed = client.get(url, headers={**alice, "Last-Event-ID": first[0]["id"]})
    assert [e["id"] for e in _parse_sse(resumed.text)] == [e["id"] for e in first[1:]]


def test_events_never_include_other_tenants(client, world, alice, make_token):
    post(client, world, auth(make_token, world.bob), cmd(), tenant=world.tenant_b)
    r = client.get(f"/tenants/{world.tenant_a}/events?timeout=0.5", headers=alice)
    a_runs = {str(x["id"]) for x in client.get(f"/tenants/{world.tenant_a}/runs",
                                               headers=alice).json()}
    import json
    for e in _parse_sse(r.text):
        assert json.loads(e["data"])["run_id"] in a_runs
