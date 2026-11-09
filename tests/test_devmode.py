import datetime
import io
import json
import logging

import pytest
import requests
from flask import Flask

import fbc
from fbc import db, devmode, discogs, listing, shipping
from fbc.discogs import DiscogsAuthError
from fbc.listing import OAuthError
from fbc.models.discogs import DiscogsInstance, DiscogsOAuthToken
from fbc.models.ebay import EbayDraft, EbayOAuthToken
from test_listing import SETTINGS, _carnaval
from test_shipping import _order


@pytest.fixture
def dev_mode(monkeypatch):
    """Dev mode on, and any request that still gets out fails the test."""
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)

    def no_requests(*args, **kwargs):
        raise AssertionError("Dev mode let a request out.")

    monkeypatch.setattr(requests.Session, "send", no_requests)
    monkeypatch.setattr(requests, "get", no_requests)
    monkeypatch.setattr(requests, "post", no_requests)


@pytest.fixture
def client(monkeypatch):
    """A test client logged in as an Admin, with listing settings in place."""
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _fails(*args, **kwargs):
    raise AssertionError("Called in dev mode.")


def _saved_tokens():
    now = datetime.datetime(2026, 10, 1)
    db.session.add(EbayOAuthToken(refresh_token="live-refresh", created=now))
    db.session.add(
        DiscogsOAuthToken(token="live", secret="secret", username="me", created=now)
    )
    db.session.commit()


# --- Turning it on, and the config ---


def _app(**config):
    app = Flask("devmode-test")
    app.config.update(
        APP_NAME="FBC",
        EBAY_SETTINGS={
            "APP_ID": "app",
            "DEV_ID": "dev",
            "CERT_ID": "cert",
            "RUNAME": "runame",
            "OAUTH_REFRESH_TOKEN": "refresh",
            "LISTING": {"CATEGORY_ID": "176985"},
        },
        USPS_SETTINGS={"CLIENT_ID": "id", "CLIENT_SECRET": "secret"},
        DISCOGS_SETTINGS={
            "CONSUMER_KEY": "key",
            "CONSUMER_SECRET": "secret",
            "USER_TOKEN": "token",
            "USER_AGENT": "fbc/1.0",
        },
        **config,
    )
    return app


@pytest.fixture
def blocked_requests(monkeypatch):
    """Record block_external_requests() rather than patching requests for real."""
    calls = []
    monkeypatch.setattr(devmode, "block_external_requests", lambda: calls.append(1))
    monkeypatch.delenv(devmode.ENVIRONMENT_VARIABLE, raising=False)
    return calls


def test_dev_mode_empties_the_ebay_and_discogs_keys(blocked_requests):
    app = _app(DEV_MODE=True)
    original = app.config["EBAY_SETTINGS"]

    devmode.init_app(app)

    ebay = app.config["EBAY_SETTINGS"]
    assert all(ebay[k] == "" for k in devmode.EBAY_SECRETS)
    assert ebay["LISTING"] == {"CATEGORY_ID": "176985"}
    discogs_settings = app.config["DISCOGS_SETTINGS"]
    assert all(discogs_settings[k] == "" for k in devmode.DISCOGS_SECRETS)
    assert discogs_settings["USER_AGENT"] == "fbc/1.0"
    assert all(app.config["USPS_SETTINGS"][k] == "" for k in devmode.USPS_SECRETS)
    assert original["APP_ID"] == "app"
    assert app.config["APP_NAME"] == "FBC (dev)"
    assert blocked_requests == [1]


def test_the_environment_turns_dev_mode_on(blocked_requests, monkeypatch):
    monkeypatch.setenv(devmode.ENVIRONMENT_VARIABLE, "1")
    app = _app(DEV_MODE=False)

    devmode.init_app(app)

    assert app.config["DEV_MODE"] is True
    assert app.config["EBAY_SETTINGS"]["APP_ID"] == ""


def test_the_environment_cant_turn_off_dev_mode_set_in_config(
    blocked_requests, monkeypatch
):
    monkeypatch.setenv(devmode.ENVIRONMENT_VARIABLE, "0")
    app = _app(DEV_MODE=True)

    devmode.init_app(app)

    assert app.config["DEV_MODE"] is True


def test_without_dev_mode_nothing_changes(blocked_requests):
    app = _app()

    devmode.init_app(app)

    assert app.config["DEV_MODE"] is False
    assert app.config["EBAY_SETTINGS"]["APP_ID"] == "app"
    assert app.config["DISCOGS_SETTINGS"]["USER_TOKEN"] == "token"
    assert app.config["APP_NAME"] == "FBC"
    assert blocked_requests == []


def test_the_test_suite_runs_without_dev_mode(app):
    assert app.config["DEV_MODE"] is False


# --- The request backstop ---


@pytest.mark.parametrize(
    "url, blocked",
    [
        ("https://api.ebay.com/ws/api.dll", True),
        ("https://apiz.ebay.com/sell/finances/v1/transaction", True),
        ("https://ebay.com/", True),
        ("https://api.discogs.com/oauth/identity", True),
        ("https://www.discogs.com/", True),
        ("https://notebay.com/", False),
        ("http://127.0.0.1:5555/", False),
        ("https://pypi.org/", False),
    ],
)
def test_requests_to_ebay_and_discogs_are_refused(url, blocked):
    sent = []
    send = devmode.guard_send(lambda session, request, **kwargs: sent.append(request))
    request = requests.Request("GET", url).prepare()

    if blocked:
        with pytest.raises(devmode.ExternalRequestBlocked) as e:
            send(requests.Session(), request)
        # Callers that already handle a failed request handle this too.
        assert isinstance(e.value, requests.RequestException)
        assert sent == []
    else:
        send(requests.Session(), request)
        assert sent == [request]


# --- The eBay and Discogs entry points ---


def test_no_ebay_token_is_used_in_dev_mode(app, dev_mode, monkeypatch):
    _saved_tokens()
    monkeypatch.setattr(
        listing,
        "_oauth",
        {
            listing.MEDIA_SCOPE: {
                "token": "minted-before",
                "expires": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            }
        },
    )

    with pytest.raises(OAuthError, match="Dev Mode is turned on"):
        listing.oauth_token()
    with pytest.raises(OAuthError, match="Dev Mode is turned on"):
        listing.trading()
    with pytest.raises(OAuthError, match="Dev Mode is turned on"):
        listing.exchange_code("abc")


def test_no_discogs_client_is_made_in_dev_mode(app, dev_mode):
    _saved_tokens()

    with pytest.raises(DiscogsAuthError, match="Dev Mode is turned on"):
        discogs.client()
    with pytest.raises(DiscogsAuthError, match="Dev Mode is turned on"):
        discogs.authorize_url("http://localhost/connectdiscogs/")


def test_new_draft_skips_the_discogs_lookup_in_dev_mode(app, dev_mode, monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    unit = _carnaval()
    unit.discogs_release.labels = None

    draft = listing.new_draft(unit)

    assert draft.title
    assert unit.discogs_release.labels is None


# --- Syncs ---


@pytest.mark.parametrize(
    "path, sync",
    [
        ("/syncdiscogs", "dosyncdiscogs"),
        ("/syncebaylistings", "dosyncebaylistings"),
        ("/syncebayorders", "dosyncebayorders"),
        ("/syncebayordersdeep", "dosyncebayorders"),
    ],
)
def test_syncs_do_nothing_and_succeed_in_dev_mode(
    app, dev_mode, inline_sync, monkeypatch, caplog, path, sync
):
    from fbc.index import MyIndexView

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(MyIndexView, sync, _fails)
    client = app.test_client()

    response = client.get(path, headers={"Accept": "application/json"})

    job = client.get(response.get_json()["url"]).get_json()
    assert job["status"] == "done"
    assert job["errors"] == 0
    assert "Dev Mode is turned on, external syncing disabled." in job["log"]


# --- eBay Drafts ---


def _draft():
    draft = EbayDraft(
        unit=_carnaval(),
        title="Carnaval",
        price=25.0,
        pictures=json.dumps(["https://i.ebayimg.com/a.jpg"]),
        status="draft",
    )
    db.session.add(draft)
    db.session.commit()
    return draft


def test_draft_page_turns_off_uploads_checking_and_publishing(client, dev_mode):
    draft = _draft()

    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )

    assert "Upload to eBay" not in html
    assert "picture uploads disabled" in html
    assert "disabled>Check with eBay" in html
    assert "disabled>Publish to eBay" in html


def test_pictures_arent_uploaded_in_dev_mode(client, dev_mode, monkeypatch):
    monkeypatch.setattr("fbc.views.listing.upload_picture", _fails)
    draft = _draft()

    page = client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={"pictures": [(io.BytesIO(b"front"), "front.jpg")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "Dev Mode is turned on, picture uploads disabled." in page.get_data(
        as_text=True
    )
    db.session.expire_all()
    assert draft.picture_list() == ["https://i.ebayimg.com/a.jpg"]


@pytest.mark.parametrize(
    "action, function, text",
    [
        ("verify", "verify_draft", "checking listings with eBay disabled"),
        ("publish", "publish_draft", "publishing to eBay disabled"),
    ],
)
def test_drafts_arent_checked_or_published_in_dev_mode(
    client, dev_mode, monkeypatch, action, function, text
):
    monkeypatch.setattr("fbc.views.listing." + function, _fails)
    draft = _draft()

    page = client.post(
        "/ebaydraftmodelview/" + action + "/" + str(draft.id), follow_redirects=True
    )

    assert text in page.get_data(as_text=True)
    db.session.expire_all()
    assert draft.status == "draft"
    assert draft.messages is None


def test_policies_page_doesnt_ask_ebay_in_dev_mode(client, dev_mode, monkeypatch):
    monkeypatch.setattr("fbc.views.listing.seller_profiles", _fails)
    monkeypatch.setattr("fbc.views.listing.listing_details", _fails)

    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)

    assert "Dev Mode is turned on, eBay lookups disabled." in html
    assert "Business policies on the account" not in html


# --- Connect eBay / Connect Discogs ---


def test_connect_ebay_is_turned_off_in_dev_mode(client, dev_mode, monkeypatch):
    monkeypatch.setattr("fbc.views.connect.exchange_code", _fails)
    _saved_tokens()

    html = client.get("/connectebay/").get_data(as_text=True)
    pasted = client.post("/connectebay/", data={"address": "https://x/?code=abc"})
    callback = client.get("/connectebay/?state=s&code=abc")

    assert "Dev Mode is turned on, connecting to eBay disabled." in html
    assert "auth.ebay.com" not in html
    assert "Connected 2026-10-01" in html
    assert pasted.status_code == 302
    assert callback.status_code == 302


def test_connect_discogs_is_turned_off_in_dev_mode(client, dev_mode, monkeypatch):
    monkeypatch.setattr("fbc.views.connect.authorize_url", _fails)
    monkeypatch.setattr("fbc.views.connect.exchange_verifier", _fails)
    _saved_tokens()

    html = client.get("/connectdiscogs/").get_data(as_text=True)
    start = client.get("/connectdiscogs/start")
    pasted = client.post(
        "/connectdiscogs/", data={"address": "https://x/?oauth_verifier=abc"}
    )

    assert "Dev Mode is turned on, connecting to Discogs disabled." in html
    assert "Sign in to Discogs" not in html
    assert "Connected as me" in html
    assert start.status_code == 302
    assert start.headers["Location"].endswith("/connectdiscogs/")
    assert pasted.status_code == 302


# --- Ship This ---


def test_order_earnings_ask_for_net_sold_in_dev_mode(app, dev_mode):
    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["net"] is None
    assert earnings["errors"] == []
    assert earnings["warnings"] == [
        "Dev Mode is turned on, eBay earnings lookup disabled. Enter Net Sold yourself."
    ]


def test_remove_from_discogs_only_removes_it_from_fbc_in_dev_mode(
    app, dev_mode, monkeypatch
):
    monkeypatch.setattr(shipping, "_discogs", _fails)
    _, unit = _order()

    message, category = shipping.remove_from_discogs(unit)

    assert category == "warning"
    assert message.startswith("Dev Mode is turned on, Discogs removal disabled.")
    assert "from fbc only" in message
    assert db.session.get(DiscogsInstance, 1002) is None
    assert db.session.get(DiscogsInstance, 1001) is not None


def test_shipping_in_dev_mode_records_the_receipt(client, dev_mode, monkeypatch):
    monkeypatch.setattr(shipping, "_discogs", _fails)
    order, unit = _order()

    page = client.get("/ebayordermodelview/ship/" + order.order_id)
    assert "(Dev Mode: from fbc only)" in page.get_data(as_text=True)
    shipped = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "remove_discogs-" + str(unit.id): "1"},
        follow_redirects=True,
    )

    html = shipped.get_data(as_text=True)
    assert "Recorded a Sales Receipt for $16.50" in html
    assert "Discogs removal disabled" in html
    db.session.expire_all()
    assert db.session.get(type(unit), unit.id).sold


# --- The banner ---


def test_every_page_shows_the_dev_mode_banner(client, dev_mode):
    html = client.get("/unitmodelview/list/").get_data(as_text=True)

    assert "<strong>Dev Mode:</strong>" in html


def test_no_banner_without_dev_mode(client):
    html = client.get("/unitmodelview/list/").get_data(as_text=True)

    assert "Dev Mode" not in html
