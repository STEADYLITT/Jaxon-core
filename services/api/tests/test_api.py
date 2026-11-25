from fastapi.testclient import TestClient

from jackson_api.contracts import TERMINAL_TASK_STATES, CommandType, TaskState
from jackson_api.main import app

client = TestClient(app)


def test_health_ok_and_honest_about_integrations():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["phase"] == "07-policy-approvals-budget"
    assert "none" in body["integrations"]


def test_contracts_match_spec_volume_02_and_04():
    body = client.get("/contracts").json()
    assert body["task_states"] == [
        "queued", "running", "waiting_approval", "succeeded", "failed", "canceled",
    ]
    assert body["command_types"] == [
        "PLAN", "EXECUTE", "SIMULATE", "REPORT", "OPTIMIZE", "BUILD", "PAUSE", "RESUME", "STOP",
    ]
    assert body["autonomy_levels"] == {
        "READ_ONLY": 0,
        "DRAFTS_AND_SIMULATIONS": 1,
        "REVERSIBLE_SANDBOX": 2,
        "APPROVED_BOUNDED_PRODUCTION": 3,
    }
    assert len(body["entities"]) == 16


def test_terminal_states():
    assert TERMINAL_TASK_STATES == {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELED}
    assert TaskState.WAITING_APPROVAL not in TERMINAL_TASK_STATES


def test_no_unscoped_command_route():
    # Commands exist only under /tenants/{id}/ (ADR-013); there is no global, unscoped route.
    assert client.post("/commands", json={"type": CommandType.PLAN}).status_code == 404
