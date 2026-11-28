"""ADR-053: Amazon (SP-API) and eBay (Browse) product research. Prices, ranks, FBA fees
and margins only from real responses; anything missing stays missing. APIs are faked."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state

from jackson_api import connections as C
from jackson_api.connectors import marketplaces as M
from jackson_api.marketplace_research import margin
from jackson_api.worker import Worker

CID, SEC, REF = "amzn1.application-oa2-client.x", "amzsec" + "s" * 30, "Atzr|" + "r" * 40
EID, ESEC = "Steady-Jackson-PRD-x", "PRD-" + "e" * 30


class FakeMarkets:
    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        h, p = req.url.host, req.url.path
        if h == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "Atza|tok", "expires_in": 3600})
        if h == "sellingpartnerapi-na.amazon.com":
            assert req.headers["x-amz-access-token"] == "Atza|tok"
            if p == "/sellers/v1/marketplaceParticipations":
                return httpx.Response(200, json={"payload": [
                    {"marketplace": {"countryCode": "US"}}]})
            if p == "/catalog/2022-04-01/items":
                assert req.url.params["marketplaceIds"] == M.US
                return httpx.Response(200, json={"items": [
                    {"asin": "B0MAG1", "summaries": [{"itemName": "Magnetic bit holder",
                                                      "brand": "GripCo"}],
                     "salesRanks": [{"displayGroupRanks": [{"title": "Tools", "rank": 812}]}]},
                    {"asin": "B0NOPRICE", "summaries": [{"itemName": "Odd holder"}]}]})
            if p == "/products/pricing/v0/items/B0MAG1/offers":
                return httpx.Response(200, json={"payload": {"Summary": {
                    "BuyBoxPrices": [{"LandedPrice": {"Amount": 24.99}}],
                    "LowestPrices": [{"LandedPrice": {"Amount": 22.5}}],
                    "TotalOfferCount": 7}}})
            if p == "/products/pricing/v0/items/B0NOPRICE/offers":
                return httpx.Response(200, json={"payload": {"Summary": {}}})
            if p == "/products/fees/v0/items/B0MAG1/feesEstimate":
                body = json.loads(req.content)["FeesEstimateRequest"]
                assert body["PriceToEstimateFees"]["ListingPrice"]["Amount"] == 24.99
                return httpx.Response(200, json={"payload": {"FeesEstimateResult": {
                    "FeesEstimate": {"TotalFeesEstimate": {"Amount": 8.12}}}}})
        if h == "api.ebay.com" and p.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "v^1.ebay", "expires_in": 7200})
        if h == "api.ebay.com" and p.endswith("/item_summary/search"):
            assert req.headers["x-ebay-c-marketplace-id"] == "EBAY_US"
            return httpx.Response(200, json={"total": 340, "itemSummaries": [
                {"title": "Mag holder", "price": {"value": "19.99", "currency": "USD"},
                 "itemWebUrl": "https://www.ebay.com/itm/1"},
                {"title": "Mag holder 2", "price": {"value": "25.00", "currency": "USD"},
                 "itemWebUrl": "https://www.ebay.com/itm/2"},
                {"title": "Mag holder 3", "price": {"value": "30.00", "currency": "USD"},
                 "itemWebUrl": "https://www.ebay.com/itm/3"}]})
        return httpx.Response(404, json={"errors": [{"message": "nope"}]})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeMarkets()
    for k, v in {"AMAZON_SP_CLIENT_ID": CID, "AMAZON_SP_CLIENT_SECRET": SEC,
                 "AMAZON_SP_REFRESH_TOKEN": REF, "EBAY_CLIENT_ID": EID,
                 "EBAY_CLIENT_SECRET": ESEC}.items():
        monkeypatch.setenv(k, v)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(M, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_margin_only_from_real_numbers():
    assert margin(2499, 812, 600) == {"profit_cents": 1087, "margin_pct": 43.5}
    assert margin(None, 812, 600) is None and margin(2499, 812, None) is None


def test_connection_tests(client, world, alice, live):
    for cid in ("amazon", "ebay"):
        r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/{cid}/test", headers=alice)
        assert r.json()["ok"], r.text
        assert SEC not in r.text and REF not in r.text and ESEC not in r.text


def test_research_prices_fees_margins_and_ebay(client, world, alice, maker, owner_db, drain,
                                               live):
    r = client.post(f"/tenants/{world.tenant_a}/ecommerce/research", headers=alice,
                    json={"keywords": ["magnetic bit holder"], "landed_cost_cents": 600,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["sources"] == ["amazon", "ebay"]
    assert out["best_margins"] == [{"profit_cents": 1087, "title": "Magnetic bit holder",
                                    "url": "https://www.amazon.com/dp/B0MAG1"}]
    from jackson_api.artifacts import LocalArtifactStore
    from jackson_api.models import Artifact
    art = owner_db.get(Artifact, uuid.UUID(out["artifact_id"]))
    data = json.loads(LocalArtifactStore().get(art.storage_key))
    row = data["results"][0]
    odd = [i for i in row["amazon"] if i["asin"] == "B0NOPRICE"][0]
    assert odd["fba_fees_cents"] is None and odd["margin"] is None  # nothing invented
    assert row["ebay"]["median_asking_cents"] == 2500 and row["ebay"]["total_listings"] == 340


def test_not_connected(client, world, alice, monkeypatch):
    for v in ("AMAZON_SP_CLIENT_ID", "EBAY_CLIENT_ID"):
        monkeypatch.delenv(v, raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/ecommerce/research", headers=alice,
                    json={"keywords": ["x"], "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "AMAZON_SP_CLIENT_ID" in r.text
