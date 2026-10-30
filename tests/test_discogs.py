from types import SimpleNamespace

import discogs_client
import pytest
from discogs_client.exceptions import HTTPError

import fbc
from fbc import db, discogs
from fbc.models.discogs import DiscogsOAuthToken


class FakeClient:
    """Stands in for discogs_client.Client, recording how it was made."""

    made = []

    def __init__(self, user_agent, **kwargs):
        self.kwargs = kwargs
        FakeClient.made.append(self)

    def get_authorize_url(self, callback_url):
        self.callback_url = callback_url
        return (
            "req-token",
            "req-secret",
            "https://discogs.com/oauth/authorize?oauth_token=req-token",
        )

    def get_access_token(self, verifier):
        if verifier != "good":
            raise HTTPError("Invalid response from access token URL.", 401)
        return "access-token", "access-secret"

    def identity(self):
        return SimpleNamespace(username="crate-digger")


@pytest.fixture
def fake_client(monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(discogs_client, "Client", FakeClient)
    return FakeClient


@pytest.fixture
def app_keys(monkeypatch):
    settings = fbc.app.config["DISCOGS_SETTINGS"]
    monkeypatch.setitem(settings, "CONSUMER_KEY", "ckey")
    monkeypatch.setitem(settings, "CONSUMER_SECRET", "csecret")


def _save_token():
    db.session.add(DiscogsOAuthToken(token="t", secret="s", username="crate-digger"))
    db.session.commit()


def test_client_uses_the_personal_token_until_connected(fake_client, app_keys):
    discogs.client()

    assert fake_client.made[0].kwargs == {"user_token": "test-discogs-token"}


def test_client_uses_the_saved_oauth_token(fake_client, app_keys):
    _save_token()

    discogs.client()

    assert fake_client.made[0].kwargs == {
        "consumer_key": "ckey",
        "consumer_secret": "csecret",
        "token": "t",
        "secret": "s",
    }


def test_client_without_any_token_says_to_connect(fake_client, monkeypatch):
    monkeypatch.setitem(fbc.app.config["DISCOGS_SETTINGS"], "USER_TOKEN", "")

    with pytest.raises(discogs.DiscogsAuthError, match="Connect Discogs"):
        discogs.client()


def test_saved_token_without_app_keys_says_to_set_them(fake_client):
    _save_token()

    with pytest.raises(discogs.DiscogsAuthError, match="CONSUMER_KEY"):
        discogs.client()


def test_verifier_from_reads_an_address_a_query_or_a_bare_verifier():
    assert discogs.verifier_from(
        "http://fbc/connectdiscogs/?oauth_token=abc&oauth_verifier=xyz"
    ) == ("abc", "xyz")
    assert discogs.verifier_from("oauth_token=abc&oauth_verifier=xyz") == (
        "abc",
        "xyz",
    )
    assert discogs.verifier_from(" xyz ") == (None, "xyz")
    assert discogs.verifier_from("") == (None, None)


def test_exchange_verifier_saves_the_access_token(fake_client, app_keys):
    saved = discogs.exchange_verifier("req-token", "req-secret", "good")

    assert fake_client.made[0].kwargs["token"] == "req-token"
    assert fake_client.made[0].kwargs["secret"] == "req-secret"
    assert (saved.token, saved.secret, saved.username) == (
        "access-token",
        "access-secret",
        "crate-digger",
    )
    assert discogs.saved_oauth_token() == saved


def test_exchange_verifier_refused(fake_client, app_keys):
    with pytest.raises(discogs.DiscogsAuthError, match="didn't accept"):
        discogs.exchange_verifier("req-token", "req-secret", "bad")
    assert discogs.saved_oauth_token() is None


@pytest.fixture
def client(monkeypatch, fake_client, app_keys):
    """A test client logged in as an Admin."""
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def test_connect_page_offers_sign_in(client):
    html = client.get("/connectdiscogs/").get_data(as_text=True)

    assert "Not connected yet" in html
    assert 'href="/connectdiscogs/start"' in html


def test_start_sends_the_browser_to_discogs(client, fake_client):
    page = client.get("/connectdiscogs/start")

    assert page.status_code == 302
    assert page.headers["Location"].startswith("https://discogs.com/oauth/authorize")
    assert fake_client.made[0].callback_url == "http://localhost/connectdiscogs/"
    with client.session_transaction() as s:
        assert s["discogs_oauth_request"] == ["req-token", "req-secret"]


def test_discogs_redirecting_back_connects(client):
    client.get("/connectdiscogs/start")

    client.get("/connectdiscogs/?oauth_token=req-token&oauth_verifier=good")

    assert discogs.saved_oauth_token().username == "crate-digger"
    html = client.get("/connectdiscogs/").get_data(as_text=True)
    assert "Connected as crate-digger" in html


def test_pasted_address_connects(client):
    client.get("/connectdiscogs/start")

    client.post(
        "/connectdiscogs/",
        data={"address": "http://elsewhere/?oauth_token=req-token&oauth_verifier=good"},
    )

    assert discogs.saved_oauth_token() is not None


def test_a_different_sign_in_is_refused(client):
    client.get("/connectdiscogs/start")

    client.get("/connectdiscogs/?oauth_token=other&oauth_verifier=good")

    assert discogs.saved_oauth_token() is None


def test_cancelled_sign_in_forgets_the_request(client):
    client.get("/connectdiscogs/start")

    client.get("/connectdiscogs/?denied=req-token")

    with client.session_transaction() as s:
        assert "discogs_oauth_request" not in s


def test_connect_discogs_is_in_the_discogs_menu(client):
    html = client.get("/connectdiscogs/").get_data(as_text=True)

    assert 'href="/connectdiscogs/"' in html
