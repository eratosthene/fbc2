"""Price Research on Add Unit, with Discogs and eBay mocked: no test reaches them."""

from types import SimpleNamespace

import pytest
import requests

import fbc
from fbc import discogs, listing, pricing


def price(value, currency="USD"):
    return SimpleNamespace(value=value, currency=currency)


def fake_release(suggestions=None, lowest=price(25.0), num=4, have=900, want=300):
    stats = SimpleNamespace(lowest_price=lowest, num_for_sale=num)
    return SimpleNamespace(
        price_suggestions=suggestions,
        marketplace_stats=stats,
        community=SimpleNamespace(
            have=have, want=want, rating=SimpleNamespace(average=4.5, count=10)
        ),
    )


@pytest.fixture(autouse=True)
def fresh_cache():
    pricing.clear_cache()
    yield
    pricing.clear_cache()


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)


@pytest.fixture
def discogs_calls(monkeypatch):
    calls = []

    def make_client(release):
        def client():
            calls.append("client")
            return SimpleNamespace(release=lambda rid: release)

        monkeypatch.setattr(discogs, "client", client)
        return calls

    return make_client


def test_discogs_gives_suggestions_stats_and_community(discogs_calls):
    release = fake_release(suggestions=SimpleNamespace(mint=price(40), good=price(10)))
    discogs_calls(release)

    r = pricing.discogs_research(123)

    assert r["ok"]
    assert r["suggestions"] == [("Mint (M)", 40.0, "USD"), ("Good (G)", 10.0, "USD")]
    assert r["lowest"] == (25.0, "USD")
    assert r["num_for_sale"] == 4
    assert (r["have"], r["want"], r["rating"]) == (900, 300, (4.5, 10))


def test_discogs_without_seller_settings_still_gives_the_rest(discogs_calls):
    from discogs_client.exceptions import HTTPError

    class Refusing:
        def __getattr__(self, name):
            raise HTTPError("You must fill out your seller settings first.", 403)

    discogs_calls(fake_release(suggestions=Refusing()))

    r = pricing.discogs_research(123)

    assert r["ok"] and r["suggestions"] == []
    assert "seller settings" in r["suggestions_note"]
    assert r["lowest"] == (25.0, "USD")


def test_discogs_answers_are_cached(discogs_calls):
    calls = discogs_calls(fake_release(suggestions=SimpleNamespace(mint=price(40))))

    pricing.discogs_research(123)
    pricing.discogs_research(123)

    assert calls == ["client"]


def test_discogs_errors_are_shown_not_raised_or_cached(monkeypatch):
    def refuse():
        raise discogs.DiscogsAuthError("Connect fbc to Discogs first.")

    monkeypatch.setattr(discogs, "client", refuse)

    assert pricing.discogs_research(123) == {
        "ok": False,
        "error": "Connect fbc to Discogs first.",
    }
    assert not pricing._cache


def test_discogs_network_failure_is_shown(monkeypatch):
    def boom():
        raise requests.ConnectionError("down")

    monkeypatch.setattr(discogs, "client", boom)

    r = pricing.discogs_research(123)

    assert not r["ok"] and "Couldn't reach Discogs" in r["error"]


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.reason = ""
        self.text = ""

    def json(self):
        return self._body


@pytest.fixture
def ebay(monkeypatch):
    sent = []
    items = [
        {"condition": "Used", "price": {"value": "20.00", "currency": "USD"}},
        {"condition": "Used", "price": {"value": "30.00", "currency": "USD"}},
        {"condition": "Used", "price": {"value": "40.00", "currency": "USD"}},
        {"condition": "New", "price": {"value": "60.00", "currency": "USD"}},
        {"condition": "New", "price": {"value": "9.00", "currency": "EUR"}},
    ]
    response = FakeResponse(body={"itemSummaries": items})
    monkeypatch.setattr(listing, "oauth_token", lambda scope=None: "tok")

    def get(url, **kwargs):
        sent.append((url, kwargs))
        return response

    monkeypatch.setattr(requests, "get", get)
    return SimpleNamespace(sent=sent, response=response)


def test_ebay_groups_active_listings_by_condition(ebay):
    r = pricing.ebay_research("Carnaval - Spyro Gyra")

    assert r["ok"]
    used = next(g for g in r["groups"] if g["condition"] == "Used")
    assert (used["count"], used["low"], used["median"], used["high"]) == (
        3,
        20.0,
        30.0,
        40.0,
    )
    # The euro listing is left out rather than mixed into dollars.
    assert next(g for g in r["groups"] if g["condition"] == "New")["count"] == 1
    url, kwargs = ebay.sent[0]
    assert url == pricing.BROWSE_URL
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["params"]["q"] == "Carnaval - Spyro Gyra"


def test_ebay_answers_are_cached(ebay):
    pricing.ebay_research("Carnaval")
    pricing.ebay_research("carnaval")

    assert len(ebay.sent) == 1


def test_ebay_http_error_is_shown(ebay):
    ebay.response.status_code = 403
    ebay.response._body = {"errors": [{"message": "Insufficient permissions"}]}

    r = pricing.ebay_research("Carnaval")

    assert not r["ok"] and "Insufficient permissions" in r["error"]
    assert not pricing._cache


def test_ebay_without_a_connection_says_so(monkeypatch):
    def refuse(scope=None):
        raise listing.OAuthError("Connect fbc to eBay first.")

    monkeypatch.setattr(listing, "oauth_token", refuse)

    assert pricing.ebay_research("Carnaval") == {
        "ok": False,
        "error": "Connect fbc to eBay first.",
    }


def test_sold_search_link_is_eBays_own_sold_filter():
    url = pricing.ebay_sold_search_url("Spyro Gyra & Co")

    assert "LH_Sold=1" in url and "LH_Complete=1" in url
    assert "Spyro+Gyra+%26+Co" in url


def test_dev_mode_reaches_nothing(dev_mode, monkeypatch):
    def reached(*args, **kwargs):
        raise AssertionError("reached out in dev mode")

    monkeypatch.setattr(discogs, "client", reached)
    monkeypatch.setattr(listing, "oauth_token", reached)
    monkeypatch.setattr(requests, "get", reached)

    r = pricing.research(123, "456", "Carnaval")

    assert r["dev_mode"]
    assert r["discogs"] == {"ok": False, "error": "Not available in Dev Mode."}
    assert r["ebay"] == {"ok": False, "error": "Not available in Dev Mode."}


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def test_add_unit_page_has_the_block_but_fetches_nothing(client, monkeypatch):
    def reached(*args, **kwargs):
        raise AssertionError("the page fetched prices")

    monkeypatch.setattr(pricing, "research", reached)

    html = client.get("/unitmodelview/add").get_data(as_text=True)

    assert "Price Research" in html
    assert "/unitmodelview/pricing" in html


def test_pricing_view_says_not_available_in_dev_mode(client, dev_mode):
    html = client.get("/unitmodelview/pricing?release=1&name=Carnaval").get_data(
        as_text=True
    )

    assert html.count("Not available in Dev Mode.") == 2
    assert "ebay.com" not in html


def test_pricing_view_labels_and_offers_prices(client, discogs_calls, ebay):
    discogs_calls(fake_release(suggestions=SimpleNamespace(near_mint=price(35))))

    html = client.get("/unitmodelview/pricing?release=1&name=Carnaval").get_data(
        as_text=True
    )

    assert "Discogs: asking prices, not sold prices" in html
    assert "Near Mint (NM or M-)" in html
    assert 'data-use-price="35.00"' in html
    assert "eBay: asking prices, not sold prices" in html
    assert 'data-use-price="30.00"' in html
    assert "LH_Sold=1" in html


def test_pricing_view_survives_both_sites_failing(client, monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(discogs, "client", down)
    monkeypatch.setattr(listing, "oauth_token", lambda scope=None: "tok")
    monkeypatch.setattr(requests, "get", down)

    page = client.get("/unitmodelview/pricing?release=1&name=Carnaval")

    assert page.status_code == 200
    assert "Couldn&#39;t reach" in page.get_data(as_text=True)
