"""Run state machine unit tests (no database)."""

import pytest

from jackson_api.contracts import TERMINAL_TASK_STATES, TaskState
from jackson_api.runs import ALLOWED, can_transition


def test_every_state_has_rules():
    assert set(ALLOWED) == set(TaskState)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_TASK_STATES))
def test_terminal_states_never_change(terminal):
    for target in TaskState:
        assert not can_transition(terminal, target)


def test_no_self_transitions():
    for s in TaskState:
        assert not can_transition(s, s)


def test_cannot_skip_running_to_succeed():
    assert not can_transition("queued", "succeeded")
    assert not can_transition("waiting_approval", "succeeded")


def test_approval_resumes_to_running():
    assert can_transition("running", "waiting_approval")
    assert can_transition("waiting_approval", "running")


def test_every_live_state_can_be_canceled():
    for s in set(TaskState) - TERMINAL_TASK_STATES:
        assert can_transition(s, "canceled")


def test_exact_allowed_transitions():
    """Pin the whole graph so any change to it is a deliberate, reviewed edit."""
    edges = {(src.value, dst.value) for src, dsts in ALLOWED.items() for dst in dsts}
    assert edges == {
        ("queued", "running"), ("queued", "canceled"), ("queued", "failed"),
        ("running", "waiting_approval"), ("running", "succeeded"),
        ("running", "failed"), ("running", "canceled"),
        ("waiting_approval", "running"), ("waiting_approval", "failed"),
        ("waiting_approval", "canceled"),
    }
