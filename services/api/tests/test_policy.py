"""Pure policy-engine tests (no database)."""

import uuid

import pytest

from jackson_api.policy import ALWAYS_APPROVE, Action, Category, Outcome, evaluate


def ev(cat, level, amount=0, remaining=10_000, approved=False):
    return evaluate(Action(cat, "x", amount), autonomy_level=level,
                    budget_remaining_cents=remaining, approved=approved).outcome


@pytest.mark.parametrize("cat", sorted(ALWAYS_APPROVE))
@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_restricted_actions_always_need_approval(cat, level):
    assert ev(cat, level) == Outcome.REQUIRE_APPROVAL
    assert ev(cat, level, approved=True) == Outcome.ALLOW


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_live_trading_is_always_denied(level, approved):
    assert ev(Category.LIVE_TRADE, level, approved=approved) == Outcome.DENY


@pytest.mark.parametrize("cat,min_level", [
    (Category.READ, 0), (Category.DRAFT, 1), (Category.SIMULATION, 1),
    (Category.SANDBOX_CHANGE, 2), (Category.SPEND, 3),
])
def test_autonomy_levels(cat, min_level):
    for level in range(4):
        expected = Outcome.ALLOW if level >= min_level else Outcome.REQUIRE_APPROVAL
        assert ev(cat, level, amount=1) == expected


def test_spend_within_budget_at_level_3_is_allowed():
    assert ev(Category.SPEND, 3, amount=500, remaining=500) == Outcome.ALLOW


def test_spend_over_budget_needs_approval_even_at_level_3():
    assert ev(Category.SPEND, 3, amount=501, remaining=500) == Outcome.REQUIRE_APPROVAL
    assert ev(Category.SPEND, 3, amount=501, remaining=500, approved=True) == Outcome.ALLOW


def test_negative_amount_denied():
    assert ev(Category.SPEND, 3, amount=-1) == Outcome.DENY


def test_fingerprint_binds_approval_to_exact_action():
    t = uuid.uuid4()
    a = Action(Category.SPEND, "buy samples", 500)
    assert a.fingerprint(t) == Action(Category.SPEND, "buy samples", 500).fingerprint(t)
    assert a.fingerprint(t) != Action(Category.SPEND, "buy samples", 501).fingerprint(t)
    assert a.fingerprint(t) != Action(Category.SPEND, "buy ads", 500).fingerprint(t)
    assert a.fingerprint(t) != a.fingerprint(uuid.uuid4())
