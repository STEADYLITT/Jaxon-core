"""Product unit economics (spec vol. 03 "E-commerce pipeline", phase 14, ADR-024).

Pure and exact (Decimal arithmetic; money rounded half-up to cents only for display).
Every default is reported as an assumption, so nothing hides inside the result. These
are ESTIMATES from the inputs given, not verified results.

Per order:
  landed cost/unit = unit cost + inbound shipping + duty x (unit cost + inbound) + packaging
  net revenue      = price x units x (1 - return rate)
  product cost     = landed x units - recovered returns
                     (recovered = return rate x resellable share x landed x units)
  contribution before ads = net revenue - product cost - outbound shipping
                            - payment fees (rate x gross + fixed) - platform fees (rate x gross)
                            - return handling (return rate x cost per return)
  contribution after ads  = contribution before ads - CAC (ad cost per order)
"""

import math
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, model_validator

DEFAULTS = {  # used only when the caller leaves a field out; always reported back
    "inbound_shipping_per_unit": 0, "duty_rate": 0, "packaging_per_unit": 0,
    "outbound_shipping_per_order": 0, "units_per_order": 1,
    "payment_fee_rate": 0.029, "payment_fee_fixed": 0.30, "platform_fee_rate": 0,
    "return_rate": 0.05, "return_cost_per_return": 0, "resellable_rate": 0.5,
    "cac": 0, "monthly_fixed_costs": 0, "target_monthly_profit": 0,
}


class EconomicsIn(BaseModel):
    price: float = Field(gt=0, description="retail price per unit, USD")
    unit_cost: float = Field(ge=0, description="supplier cost per unit, USD")
    inbound_shipping_per_unit: float | None = Field(default=None, ge=0)
    duty_rate: float | None = Field(default=None, ge=0, le=1)
    packaging_per_unit: float | None = Field(default=None, ge=0)
    outbound_shipping_per_order: float | None = Field(default=None, ge=0)
    units_per_order: int | None = Field(default=None, ge=1, le=1000)
    payment_fee_rate: float | None = Field(default=None, ge=0, le=1)
    payment_fee_fixed: float | None = Field(default=None, ge=0)
    platform_fee_rate: float | None = Field(default=None, ge=0, le=1)
    return_rate: float | None = Field(default=None, ge=0, le=1)
    return_cost_per_return: float | None = Field(default=None, ge=0)
    resellable_rate: float | None = Field(default=None, ge=0, le=1)
    cac: float | None = Field(default=None, ge=0, description="ad spend per order, USD")
    monthly_fixed_costs: float | None = Field(default=None, ge=0)
    target_monthly_profit: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _sane(self):
        if self.unit_cost > 100 * self.price:
            raise ValueError("unit_cost is over 100x the price; check the units")
        return self


def D(x) -> Decimal:  # noqa: N802
    return Decimal(str(x))


def cents(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _resolve(inp: EconomicsIn) -> tuple[dict, list[str]]:
    vals, assumed = {}, []
    for k, v in inp.model_dump().items():
        if v is None:
            vals[k] = D(DEFAULTS[k])
            assumed.append(f"{k} = {DEFAULTS[k]} (default, not provided)")
        else:
            vals[k] = D(v)
    return vals, assumed


def _core(v: dict) -> dict:
    units = v["units_per_order"]
    landed = (v["unit_cost"] + v["inbound_shipping_per_unit"]
              + v["duty_rate"] * (v["unit_cost"] + v["inbound_shipping_per_unit"])
              + v["packaging_per_unit"])
    gross = v["price"] * units
    net_rev = gross * (1 - v["return_rate"])
    recovered = v["return_rate"] * v["resellable_rate"] * landed * units
    product = landed * units - recovered
    payment = v["payment_fee_rate"] * gross + v["payment_fee_fixed"]
    platform = v["platform_fee_rate"] * gross
    returns = v["return_rate"] * v["return_cost_per_return"]
    before_ads = net_rev - product - v["outbound_shipping_per_order"] - payment - platform \
        - returns
    after_ads = before_ads - v["cac"]
    return {"landed": landed, "gross": gross, "net_rev": net_rev, "product": product,
            "payment": payment, "platform": platform, "returns": returns,
            "before_ads": before_ads, "after_ads": after_ads}


def calculate(inp: EconomicsIn) -> dict:
    v, assumed = _resolve(inp)
    c = _core(v)
    margin_pct = c["after_ads"] / c["gross"]
    fixed_plus_target = v["monthly_fixed_costs"] + v["target_monthly_profit"]
    orders_needed = (math.ceil(fixed_plus_target / c["after_ads"])
                     if c["after_ads"] > 0 else None)
    breakeven_cac = c["before_ads"]

    def margin_with(**changes) -> float:
        cc = _core({**v, **{k: D(x) for k, x in changes.items()}})
        return round(float(cc["after_ads"] / cc["gross"]), 4)

    p, cac, rr = v["price"], v["cac"], v["return_rate"]
    warnings = []
    if c["after_ads"] <= 0:
        warnings.append("loses money on every order after ad spend at these inputs")
    elif margin_pct < D("0.10"):
        warnings.append("thin margin (<10% after ads): little room for error")
    return {
        "per_order": {
            "landed_cost_per_unit": cents(c["landed"]),
            "gross_revenue": cents(c["gross"]),
            "net_revenue_after_returns": cents(c["net_rev"]),
            "product_cost_net_of_recovered_returns": cents(c["product"]),
            "payment_fees": cents(c["payment"]),
            "platform_fees": cents(c["platform"]),
            "return_handling": cents(c["returns"]),
            "contribution_before_ads": cents(c["before_ads"]),
            "ad_cost": cents(cac),
            "contribution_after_ads": cents(c["after_ads"]),
        },
        "contribution_margin_pct": round(float(margin_pct), 4),
        "breakeven_cac": cents(breakeven_cac),
        "breakeven_roas": (round(float(c["gross"] / breakeven_cac), 3)
                           if breakeven_cac > 0 else None),
        "orders_per_month_for_target": orders_needed,
        "sensitivity_margin_pct": {
            "price_-10%": margin_with(price=p * D("0.9")),
            "price_+10%": margin_with(price=p * D("1.1")),
            "cac_+20%": margin_with(cac=cac * D("1.2")),
            "cac_-20%": margin_with(cac=cac * D("0.8")),
            "returns_+50%": margin_with(return_rate=min(rr * D("1.5"), D(1))),
        },
        "assumptions": assumed,
        "warnings": warnings,
        "label": "estimate from the inputs given; not verified",
    }


def attach(db, opp, inputs: EconomicsIn, by: str) -> dict:
    """Save an economics snapshot on an opportunity and rescore its margin."""
    from datetime import UTC, datetime

    from .opportunities import brief, score

    result = calculate(inputs)
    opp.economics = {"inputs": inputs.model_dump(), "result": result, "by": by,
                     "calculated_at": datetime.now(UTC).isoformat(),
                     "contribution_margin_pct": result["contribution_margin_pct"]}
    s = score(list(opp.signals or []), opp.economics)
    opp.scores, opp.score, opp.uncertainty = s, s["score"], s["uncertainty"]
    opp.brief = brief(opp.title, s, list(opp.signals or []), opp.summary)
    db.flush()
    return result
