"""Phase 14: product unit economics (pure math + API + tool + opportunity rescoring)."""

import uuid

import pytest
from conftest import auth, run_state
from pydantic import ValidationError

from jackson_api.economics import EconomicsIn, calculate
from jackson_api.worker import TaskResult, Worker

FULL = dict(price=30, unit_cost=6, inbound_shipping_per_unit=1, duty_rate=0.1,
            packaging_per_unit=0.5, outbound_shipping_per_order=5, units_per_order=1,
            payment_fee_rate=0.029, payment_fee_fixed=0.30, platform_fee_rate=0.02,
            return_rate=0.05, return_cost_per_return=6, resellable_rate=0.5, cac=8,
            monthly_fixed_costs=100, target_monthly_profit=1000)


def test_worked_example_matches_hand_calculation():
    """landed 6+1+0.7+0.5 = 8.20; net rev 28.50; product 8.20-0.205 = 7.995;
    fees 1.17 + 0.60; returns 0.30; before ads 13.435; after ads 5.435;
    margin 18.12%; breakeven ROAS 30/13.435 = 2.233; orders ceil(1100/5.435) = 203."""
    r = calculate(EconomicsIn(**FULL))
    po = r["per_order"]
    assert po["landed_cost_per_unit"] == 8.20
    assert po["net_revenue_after_returns"] == 28.50
    assert po["payment_fees"] == 1.17 and po["platform_fees"] == 0.60
    assert po["contribution_before_ads"] == 13.44 and po["contribution_after_ads"] == 5.44
    assert r["contribution_margin_pct"] == 0.1812
    assert r["breakeven_cac"] == 13.44 and r["breakeven_roas"] == 2.233
    assert r["orders_per_month_for_target"] == 203
    assert r["assumptions"] == [] and r["warnings"] == []


def test_defaults_are_reported_as_assumptions():
    r = calculate(EconomicsIn(price=20, unit_cost=5))
    assert any(a.startswith("payment_fee_rate = 0.029") for a in r["assumptions"])
    assert any(a.startswith("return_rate = 0.05") for a in r["assumptions"])
    assert r["label"].startswith("estimate")


def test_money_losing_product_is_flagged_and_never_hits_target():
    r = calculate(EconomicsIn(**{**FULL, "cac": 20}))
    assert r["per_order"]["contribution_after_ads"] < 0
    assert r["orders_per_month_for_target"] is None
    assert "loses money" in r["warnings"][0]


def test_sensitivity_moves_the_right_way():
    s = calculate(EconomicsIn(**FULL))["sensitivity_margin_pct"]
    base = calculate(EconomicsIn(**FULL))["contribution_margin_pct"]
    assert s["price_-10%"] < base < s["price_+10%"]
    assert s["cac_+20%"] < base < s["cac_-20%"]
    assert s["returns_+50%"] < base


def test_multi_unit_orders_spread_shipping():
    one = calculate(EconomicsIn(**{**FULL, "units_per_order": 1}))
    two = calculate(EconomicsIn(**{**FULL, "units_per_order": 2}))
    assert two["contribution_margin_pct"] > one["contribution_margin_pct"]


def test_exact_decimal_math_no_float_drift():
    r = calculate(EconomicsIn(price=0.1, unit_cost=0.02, payment_fee_rate=0, payment_fee_fixed=0,
                              return_rate=0))
    assert r["per_order"]["contribution_before_ads"] == 0.08


@pytest.mark.parametrize("bad", [{"price": 0}, {"unit_cost": -1}, {"return_rate": 1.5},
                                 {"units_per_order": 0}, {"price": 1, "unit_cost": 500}])
def test_invalid_inputs_rejected(bad):
    with pytest.raises(ValidationError):
        EconomicsIn(**{"price": 30, "unit_cost": 6, **bad})


# --- API + opportunity rescoring + agent tool ------------------------------------------------

@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_calculator_endpoint_saves_nothing(client, world, make_token):
    r = client.post(f"/tenants/{world.tenant_a}/economics/calculate", json=FULL,
                    headers=auth(make_token, world.carol))  # viewers may calculate
    assert r.status_code == 200 and r.json()["per_order"]["contribution_after_ads"] == 5.44
    assert client.post(f"/tenants/{world.tenant_a}/economics/calculate", json={"price": -1},
                       headers=auth(make_token, world.carol)).status_code == 422


def test_saving_economics_rescores_the_opportunity(client, world, alice, make_token):
    src = "https://shop.example/item"
    opp = client.post(f"/tenants/{world.tenant_a}/opportunities", headers=alice, json={
        "title": f"{uuid.uuid4().hex[:6]} car phone mount",
        "signals": [{"type": "avg_price", "value": 30, "source_url": src},
                    {"type": "est_unit_cost", "value": 6, "source_url": src},
                    {"type": "search_interest", "value": 70, "source_url": src}]}).json()
    assert opp["scores"]["margin_basis"].startswith("price vs. unit cost")
    url = f"/tenants/{world.tenant_a}/opportunities/{opp['id']}/economics"
    assert client.post(url, json=FULL, headers=auth(make_token, world.carol)).status_code == 403
    saved = client.post(url, json=FULL, headers=alice).json()
    assert saved["economics"]["contribution_margin_pct"] == 0.1812
    assert saved["scores"]["margin_basis"] == "unit economics"
    assert saved["scores"]["components"]["margin"] == round(0.1812 / 0.5, 3)
    assert saved["score"] < opp["score"]  # real costs make it less rosy than gross margin
    audit = client.get(f"/tenants/{world.tenant_a}/audit-events", headers=alice).json()
    assert any(e["action"] == "opportunity.economics" for e in audit)


def test_agents_get_the_math_as_a_tool(client, world, alice, maker, owner_db, drain):
    client.post(f"/tenants/{world.tenant_a}/tool-grants", headers=alice,
                json={"tool_name": "finance.unit_economics"})
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=alice, json={
        "type": "PLAN", "objective": "Price check", "idempotency_key": uuid.uuid4().hex})
    out = {}

    def h(ctx):
        out.update(ctx.use_tool("finance.unit_economics", FULL))
        return TaskResult({})

    Worker(maker, handlers={"plan": h}, retry_base_seconds=0).run_once()
    assert run_state(owner_db, r.json()["id"])[0].status == "succeeded"
    assert out["output"]["orders_per_month_for_target"] == 203
