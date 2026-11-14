"""Price Research on Add Unit, with Discogs and eBay mocked: no test reaches them."""

from types import SimpleNamespace

import pytest
import requests
from fake_discogs import FakeDiscogs, api_error, failing

import fbc
from fbc import discogs, listing, pricing


def price(value, currency="USD"):
    return {"value": value, "currency": currency}


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
    """Make Discogs answer for release 123 (and master 5, if given one)."""

    def make(
        suggestions=None,
        lowest=price(25.0),
        num=4,
        have=900,
        want=300,
        genres=("Jazz",),
        styles=("Fusion", "Latin Jazz"),
        master=None,
        fail=None,
    ):
        release = {
            "id": 123,
            "genres": list(genres),
            "styles": list(styles),
            "community": {
                "have": have,
                "want": want,
                "rating": {"average": 4.5, "count": 10},
            },
        }
        if master is not None:
            release["master_id"] = 5

        class AnyRelease(dict):
            def __missing__(self, key):
                return release

        return FakeDiscogs(
            releases=AnyRelease(),
            masters={5: master},
            suggestions=suggestions,
            stats={"lowest_price": lowest, "num_for_sale": num},
            fail=fail,
        ).install(monkeypatch)

    return make


def test_discogs_gives_suggestions_stats_and_community(discogs_calls):
    discogs_calls(suggestions={"Mint (M)": price(40), "Good (G)": price(10)})

    r = pricing.discogs_research(123)

    assert r["ok"]
    assert r["suggestions"] == [("Mint (M)", 40.0, "USD"), ("Good (G)", 10.0, "USD")]
    assert r["lowest"] == (25.0, "USD")
    assert r["num_for_sale"] == 4
    assert (r["have"], r["want"], r["rating"]) == (900, 300, (4.5, 10))
    assert r["genres"] == ["Jazz"]
    assert r["styles"] == ["Fusion", "Latin Jazz"]


def test_discogs_gives_the_masters_genres_and_styles(discogs_calls):
    discogs_calls(master={"genres": ["Jazz", "Latin"], "styles": ["Bossa Nova"]})

    r = pricing.discogs_research(123)

    assert r["master_genres"] == ["Jazz", "Latin"]
    assert r["master_styles"] == ["Bossa Nova"]


def test_discogs_without_a_master_has_no_master_genres(discogs_calls):
    fake = discogs_calls()

    r = pricing.discogs_research(123)

    assert r["master_genres"] == [] and r["master_styles"] == []
    assert "master" not in fake.names()


def test_discogs_master_failing_keeps_the_release_answer(discogs_calls):
    discogs_calls(master={}, fail=failing("master", api_error(404, "Not found")))

    r = pricing.discogs_research(123)

    assert r["ok"] and r["genres"] == ["Jazz"] and r["master_genres"] == []


def test_discogs_release_without_genres_or_styles(discogs_calls):
    discogs_calls(genres=(), styles=())

    r = pricing.discogs_research(123)

    assert r["ok"] and r["genres"] == [] and r["styles"] == []


def test_discogs_without_seller_settings_still_gives_the_rest(discogs_calls):
    discogs_calls(
        fail=failing(
            "price_suggestions",
            api_error(403, "You must fill out your seller settings first."),
        )
    )

    r = pricing.discogs_research(123)

    assert r["ok"] and r["suggestions"] == []
    assert "seller settings" in r["suggestions_note"]
    assert r["lowest"] == (25.0, "USD")


def test_discogs_answers_are_cached(discogs_calls):
    fake = discogs_calls(suggestions={"Mint (M)": price(40)})

    pricing.discogs_research(123)
    pricing.discogs_research(123)

    assert fake.names().count("release") == 1


def test_discogs_errors_are_shown_not_raised_or_cached(monkeypatch):
    def refuse(release_id):
        raise discogs.DiscogsAuthError("Connect fbc to Discogs first.")

    monkeypatch.setattr(discogs, "release", refuse)

    assert pricing.discogs_research(123) == {
        "ok": False,
        "error": "Connect fbc to Discogs first.",
    }
    assert not pricing._cache


def test_discogs_refusing_the_release_is_shown(discogs_calls):
    discogs_calls(fail=failing("release", api_error(404, "Release not found.")))

    r = pricing.discogs_research(123)

    assert r == {"ok": False, "error": "Discogs said no: Release not found."}


def test_discogs_network_failure_is_shown(monkeypatch):
    def boom(release_id):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(discogs, "release", boom)

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

    monkeypatch.setattr(discogs, "release", reached)
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
    discogs_calls(suggestions={"Near Mint (NM or M-)": price(35)})

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

    monkeypatch.setattr(discogs, "release", down)
    monkeypatch.setattr(listing, "oauth_token", lambda scope=None: "tok")
    monkeypatch.setattr(requests, "get", down)

    page = client.get("/unitmodelview/pricing?release=1&name=Carnaval")

    assert page.status_code == 200
    assert "Couldn&#39;t reach" in page.get_data(as_text=True)


def test_pricing_view_links_to_the_discogs_release(client, dev_mode):
    html = client.get("/unitmodelview/pricing?release=3095222&name=Carnaval").get_data(
        as_text=True
    )

    assert 'href="https://www.discogs.com/release/3095222"' in html
    assert 'href="https://www.discogs.com/sell/history/3095222"' in html
