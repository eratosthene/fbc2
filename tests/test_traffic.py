"""Views per listing from eBay's Sell Analytics API (#208): the reader, the
job that keeps them, and the re-read before End and park."""

import datetime
import logging

import pytest
import requests
from test_insertions import D, NOW, _active, _listing, _over, _ready_draft, client  # noqa: F401
from test_listing import FakeHTTPResponse

import fbc
from fbc import db
from fbc.core import localtime
from fbc.ebay import insertions, oauth, publish, traffic
from fbc.models.sync import SyncRecord
from fbc.sync import jobs

HOUR = datetime.timedelta(hours=1)


@pytest.fixture(autouse=True)
def info_logs(caplog):
    caplog.set_level(logging.INFO)


def _body(counts, warn=()):
    """A getTrafficReport reply, as eBay sent it on the live account."""
    return {
        "header": {
            "dimensionKeys": [{"key": "LISTING_ID", "dataType": "STRING"}],
            "metrics": [{"key": "LISTING_VIEWS_TOTAL", "dataType": "NUMBER"}],
        },
        "records": [
            {
                "dimensionValues": [{"value": item, "applicable": True}],
                "metricValues": [{"value": n, "applicable": True}],
            }
            for item, n in counts.items()
        ],
        "warnings": [
            {
                "errorId": 50030,
                "message": "The data for the listing Ids " + i + " could not be found.",
            }
            for i in warn
        ],
        "startDate": "2026-10-18T07:00:00.000Z",
        "endDate": "2026-11-17T07:59:59.000Z",
        "lastUpdatedDate": "2026-11-17T16:46:03.252Z",
    }


class FakeEbay:
    """requests.get and post faked for eBay: the token endpoint and the traffic
    report, which answers from `views` and warns about any other id."""

    def __init__(self, monkeypatch, views=None, status=200, errors=None):
        self.views = views or {}
        self.status = status
        self.errors = errors
        self.calls = []
        self.scopes = []
        monkeypatch.setitem(
            fbc.app.config["EBAY_SETTINGS"], "OAUTH_REFRESH_TOKEN", "refresh"
        )
        monkeypatch.setattr(oauth, "_oauth", {})
        monkeypatch.setattr(requests, "post", self.post)
        monkeypatch.setattr(requests, "get", self.get)

    def post(self, url, **kwargs):
        self.scopes.append(kwargs["data"]["scope"])
        return FakeHTTPResponse(200, {"access_token": "tok", "expires_in": 7200})

    def get(self, url, headers=None, params=None, timeout=None):
        assert url == traffic.TRAFFIC_URL
        assert headers["Authorization"] == "Bearer tok"
        self.calls.append(params)
        if self.status != 200:
            return FakeHTTPResponse(
                self.status, {"errors": [{"message": self.errors or "No."}]}
            )
        flt = params["filter"]
        ids = flt.split("listing_ids:{")[1].rstrip("}").split("|")
        found = {i: self.views[i] for i in ids if i in self.views}
        return FakeHTTPResponse(
            200, _body(found, warn=[i for i in ids if i not in self.views])
        )


def test_the_date_range_is_thirty_days_up_to_yesterday_in_pacific():
    # 03:00 UTC on the 17th is still the 16th in Pacific, so yesterday is the 15th.
    assert traffic.date_range(D(2026, 11, 17, 3)) == "20261017..20261115"
    assert traffic.date_range(D(2026, 11, 17, 16, 46)) == "20261018..20261116"


def test_views_asks_for_the_scope_dimension_metric_and_ids(app, monkeypatch):
    fake = FakeEbay(monkeypatch, {"1": 4, "2": 0})

    counts, errors = traffic.views(["1", "2"], D(2026, 11, 17, 16))

    assert (counts, errors) == ({"1": 4, "2": 0}, [])
    assert fake.scopes == [oauth.ANALYTICS_SCOPE]
    assert fake.calls == [
        {
            "dimension": "LISTING",
            "metric": "LISTING_VIEWS_TOTAL",
            "filter": "marketplace_ids:{EBAY_US},date_range:[20261018..20261116],"
            "listing_ids:{1|2}",
        }
    ]


def test_views_goes_in_chunks(app, monkeypatch):
    ids = [str(n) for n in range(250)]
    fake = FakeEbay(monkeypatch, {i: int(i) for i in ids})

    counts, errors = traffic.views(ids)

    assert errors == [] and counts == {i: int(i) for i in ids}
    sizes = [len(c["filter"].split("listing_ids:{")[1].split("|")) for c in fake.calls]
    assert sizes == [100, 100, 50]


def test_a_listing_ebay_has_no_data_for_is_missing_not_zero(app, monkeypatch):
    FakeEbay(monkeypatch, {"1": 0})

    counts, errors = traffic.views(["1", "unknown"])

    assert counts == {"1": 0} and errors == []


def test_a_reply_that_does_not_say_how_many_is_not_read_as_zero():
    body = _body({"1": 3})
    body["records"].append(
        {"dimensionValues": [{"value": "2"}], "metricValues": [{"applicable": False}]}
    )
    body["records"].append(
        {"dimensionValues": [{"value": "3"}], "metricValues": [{"value": True}]}
    )
    assert traffic._counts(body) == {"1": 3}
    assert traffic._counts({}) == {}


def test_no_ids_makes_no_call(app, monkeypatch):
    fake = FakeEbay(monkeypatch)
    assert traffic.views([]) == ({}, [])
    assert fake.calls == [] and fake.scopes == []


def test_a_failing_call_keeps_what_came_before_and_stops(app, monkeypatch):
    ids = [str(n) for n in range(150)]
    fake = FakeEbay(monkeypatch, {i: 1 for i in ids})
    real = fake.get

    def second_fails(url, headers=None, params=None, timeout=None):
        if fake.calls:
            fake.status, fake.errors = 500, "eBay is down"
        return real(url, headers, params, timeout)

    monkeypatch.setattr(requests, "get", second_fails)

    counts, errors = traffic.views(ids)

    assert len(counts) == 100
    assert errors == ["Getting listing views: eBay is down"]


def test_a_rate_limit_is_said_plainly(app, monkeypatch):
    FakeEbay(monkeypatch, status=429)
    assert traffic.views(["1"])[1] == [
        "eBay says too many requests for listing views; try later."
    ]


def test_a_401_drops_the_cached_token(app, monkeypatch):
    FakeEbay(monkeypatch, status=401, errors="Invalid access token")
    traffic.views(["1"])
    assert oauth.ANALYTICS_SCOPE not in oauth._oauth


def test_a_connection_failure_is_an_error_not_a_crash(app, monkeypatch):
    FakeEbay(monkeypatch)

    def down(*args, **kwargs):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(requests, "get", down)
    counts, errors = traffic.views(["1"])
    assert counts == {} and "Couldn't reach eBay" in errors[0]


def test_a_token_saved_before_the_permission_says_to_connect_again(app, monkeypatch):
    FakeEbay(monkeypatch)

    def refused(url, **kwargs):
        return FakeHTTPResponse(400, {"error_description": "invalid scope"})

    monkeypatch.setattr(requests, "post", refused)

    counts, errors = traffic.views(["1"])

    assert counts == {} and "Connect eBay" in errors[0]


def test_dev_mode_refuses_before_any_request(app, monkeypatch):
    fake = FakeEbay(monkeypatch, {"1": 1})
    monkeypatch.setitem(app.config, "DEV_MODE", True)

    counts, errors = traffic.views(["1"])

    assert counts == {} and "Dev Mode" in errors[0]
    assert fake.calls == [] and fake.scopes == []


# --- Known, stale and unknown ---


def test_known_is_a_count_checked_within_a_few_days(app):
    row = _listing("1", start=NOW)
    assert traffic.known(row, NOW) is None
    row.views_30d = 0
    assert traffic.known(row, NOW) is None
    row.views_checked = NOW - datetime.timedelta(days=3)
    assert traffic.known(row, NOW) == 0
    row.views_checked = NOW - datetime.timedelta(days=3, minutes=1)
    assert traffic.known(row, NOW) is None


def test_age_text():
    assert traffic.age_text(NOW - 10 * HOUR / 60, NOW) == "less than an hour ago"
    assert traffic.age_text(NOW - HOUR, NOW) == "1 hour ago"
    assert traffic.age_text(NOW - 6 * HOUR, NOW) == "6 hours ago"
    assert traffic.age_text(NOW - 24 * HOUR, NOW) == "1 day ago"
    assert traffic.age_text(NOW - 50 * HOUR, NOW) == "2 days ago"


# --- The job ---


def _pool_listing(item_id, day=20, **kw):
    """A listing renewing later this month, as of real now."""
    now = localtime.utcnow()
    row = _listing(item_id, start=now - datetime.timedelta(days=40), **kw)
    row.renews_at = insertions.month_bounds(now)[1] - HOUR
    db.session.commit()
    return row


def test_the_job_saves_views_for_the_pool_only_and_records_finishing(
    app, monkeypatch, caplog
):
    inside = _pool_listing("111")
    unseen = _pool_listing("222")
    ended = _pool_listing("333", ended=True)
    fake = FakeEbay(monkeypatch, {"111": 7, "333": 99})

    jobs.sync_ebay_views()
    db.session.expire_all()

    assert inside.views_30d == 7 and inside.views_checked is not None
    assert unseen.views_30d is None and unseen.views_checked is None
    assert ended.views_30d is None
    ids = fake.calls[0]["filter"].split("listing_ids:{")[1].rstrip("}").split("|")
    assert sorted(ids) == ["111", "222"]
    assert SyncRecord.last_finished(jobs.VIEWS_SYNC) is not None
    assert "Saved views for 1 listings; eBay had none for 1." in caplog.text


def test_a_listing_ebay_no_longer_answers_for_keeps_its_old_count(app, monkeypatch):
    row = _pool_listing("111")
    row.views_30d, row.views_checked = 5, localtime.utcnow() - 2 * HOUR
    db.session.commit()
    FakeEbay(monkeypatch, {})

    jobs.sync_ebay_views()
    db.session.expire_all()

    assert row.views_30d == 5


def test_a_refused_job_logs_the_error_and_does_not_record_finishing(
    app, monkeypatch, caplog
):
    _pool_listing("111")
    FakeEbay(monkeypatch, status=403, errors="Insufficient permissions")

    jobs.sync_ebay_views()

    assert "Insufficient permissions" in caplog.text
    assert SyncRecord.last_finished(jobs.VIEWS_SYNC) is None


def test_the_job_with_an_empty_pool_finishes_without_calling_ebay(app, monkeypatch):
    fake = FakeEbay(monkeypatch)
    jobs.sync_ebay_views()
    assert fake.calls == []
    assert SyncRecord.last_finished(jobs.VIEWS_SYNC) is not None


def test_the_views_job_does_nothing_in_dev_mode(app, monkeypatch, caplog):
    from fbc.core import devmode

    monkeypatch.setitem(app.config, "DEV_MODE", True)
    monkeypatch.setattr(jobs, "sync_ebay_views", lambda: pytest.fail("ran"))

    job = jobs.ebay_views_job()

    assert job.kind == jobs.VIEWS_SYNC
    job.fn()
    assert devmode.message("external syncing") in caplog.text


def test_the_command_runs_the_job(app, monkeypatch):
    _pool_listing("111")
    FakeEbay(monkeypatch, {"111": 2})

    result = app.test_cli_runner().invoke(args=["sync", "ebay-views"])

    assert result.exit_code == 0, result.output
    assert "eBay views sync finished." in result.output


def test_the_sync_button_starts_the_job(client, inline_sync, monkeypatch):  # noqa: F811
    _pool_listing("111")
    FakeEbay(monkeypatch, {"111": 2})

    response = client.get("/syncebayviews", headers={"Accept": "application/json"})
    job = client.get(response.get_json()["url"]).get_json()

    assert job["status"] == "done"
    assert SyncRecord.last_finished(jobs.VIEWS_SYNC) is not None


def test_the_listings_list_has_the_button_and_the_last_synced_note(client):  # noqa: F811
    page = client.get("/ebaylistingmodelview/list/").get_data(as_text=True)
    assert "/syncebayviews" in page and "eBay listing views" in page


# --- Re-reading before End and park ---


def _end_post(client, draft, item_id, shown):  # noqa: F811
    return client.post(
        "/ebaydraftmodelview/endandpark/" + str(draft.id) + "/" + item_id,
        data={"views_shown": shown},
        follow_redirects=True,
    )


def _end_ok(monkeypatch):
    monkeypatch.setattr(
        publish, "end_listing", lambda item_id: (True, "Ended on eBay.")
    )


def test_end_and_park_says_when_views_changed_and_still_ends(client, monkeypatch):  # noqa: F811
    draft = _ready_draft(client, monkeypatch)
    row = _active("cand", 20)
    _end_ok(monkeypatch)
    FakeEbay(monkeypatch, {"cand": 9})

    page = " ".join(
        _end_post(client, draft, "cand", "2").get_data(as_text=True).split()
    )
    db.session.expire_all()

    assert "Its views changed since the list was made: 2 then, 9 now." in page
    assert "Ended on eBay." in page
    assert row.ended and row.parked_at and row.views_30d == 9


def test_end_and_park_says_nothing_when_views_are_the_same(client, monkeypatch):  # noqa: F811
    draft = _ready_draft(client, monkeypatch)
    _active("cand", 20)
    _end_ok(monkeypatch)
    FakeEbay(monkeypatch, {"cand": 2})

    page = _end_post(client, draft, "cand", "2").get_data(as_text=True)

    assert "views changed" not in page and "Ended on eBay." in page


def test_end_and_park_says_when_views_were_unknown(client, monkeypatch):  # noqa: F811
    draft = _ready_draft(client, monkeypatch)
    _active("cand", 20)
    _end_ok(monkeypatch)
    FakeEbay(monkeypatch, {"cand": 4})

    page = _end_post(client, draft, "cand", "").get_data(as_text=True)

    assert "views were unknown when the list was made; they are 4 now." in page


def test_a_failed_re_read_does_not_block_ending(client, monkeypatch):  # noqa: F811
    draft = _ready_draft(client, monkeypatch)
    row = _active("cand", 20)
    _end_ok(monkeypatch)
    FakeEbay(monkeypatch, status=500, errors="eBay is down")

    page = _end_post(client, draft, "cand", "2").get_data(as_text=True)
    db.session.expire_all()

    assert "Couldn&#39;t re-read its views before ending it" in page
    assert row.ended and row.parked_at


def test_end_and_park_does_not_re_read_in_dev_mode(client, monkeypatch):  # noqa: F811
    from fbc.core import devmode

    draft = _ready_draft(client, monkeypatch)
    _active("cand", 20)
    calls = []
    monkeypatch.setattr(traffic, "recheck", lambda *a, **k: calls.append(a) or "x")
    monkeypatch.setattr(devmode, "on", lambda: True)

    page = _end_post(client, draft, "cand", "2").get_data(as_text=True)

    assert calls == []
    assert "Dev Mode" in page
