"""The home page and the Sync buttons' routes need signing in."""

import pytest
from auth import signed_in_client

from fbc import sync

ROUTES = [
    ("get", "/"),
    ("post", "/calibrateinsertions"),
    ("get", "/syncjob/" + "0" * 32),
    ("get", "/syncdiscogs"),
    ("get", "/syncdiscogslistings"),
    ("get", "/syncebaylistings"),
    ("get", "/syncebayorders"),
    ("get", "/syncebayordersdeep"),
]


@pytest.fixture
def started(monkeypatch):
    """Every sync job that gets started (none is run)."""
    jobs = []
    monkeypatch.setattr(sync, "_spawn", lambda target: jobs.append(target))
    return jobs


@pytest.mark.parametrize("method, path", ROUTES)
def test_signed_out_is_sent_to_the_login_page(app, started, method, path):
    response = getattr(app.test_client(), method)(path)

    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]
    assert started == []


def test_signed_out_shows_none_of_the_shops_figures(app):
    html = app.test_client().get("/", follow_redirects=True).get_data(as_text=True)

    assert "Capital" not in html and "Profit" not in html


def test_signed_out_cant_calibrate_the_insertions_count(app):
    from fbc import db
    from fbc.models.ebay import InsertionCalibration

    app.test_client().post("/calibrateinsertions", data={"remaining": "5"})

    assert db.session.query(InsertionCalibration).count() == 0


@pytest.mark.parametrize(
    "path",
    ["/syncdiscogs", "/syncdiscogslistings", "/syncebaylistings", "/syncebayorders"],
)
def test_signed_in_starts_the_job(app, started, path):
    client = signed_in_client(app)

    response = client.get(path, headers={"Accept": "application/json"})

    assert response.status_code == 200
    assert "id" in response.get_json()
    assert len(started) == 1


def test_signed_in_sees_the_home_page(app):
    assert signed_in_client(app).get("/").status_code == 200


def test_signed_in_reads_a_job_log(app, started):
    client = signed_in_client(app)
    job = client.get("/syncdiscogs", headers={"Accept": "application/json"}).get_json()

    assert client.get(job["url"]).status_code == 200


def test_the_login_page_stays_public(app):
    assert app.test_client().get("/login/").status_code == 200


def test_a_signed_in_non_admin_without_the_permission_is_refused(app, started):
    """Only a role given the permission gets in; it isn't enough to be signed in."""
    import fbc

    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_role("Nobody")
    sm.add_user("nobody", "N", "Body", "n@example.com", sm.find_role("Nobody"), "pw")
    client = app.test_client()
    client.post("/login/", data={"username": "nobody", "password": "pw"})

    assert client.get("/syncdiscogs").status_code == 403
    assert started == []
