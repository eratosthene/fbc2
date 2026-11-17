"""Re-listing from the Parked Listings Task, with eBay faked (#208 part B)."""

import datetime

import pytest

import fbc
from fbc import db
from fbc.core import devmode, localtime
from fbc.ebay import api
from fbc.models.ebay import EbayDraft, EbayListing
from fbc.models.sync import SyncRecord
from fbc.sales import relisting, tasks
from test_listing import SETTINGS, _carnaval
from test_tasks import _parked, _unit


@pytest.fixture
def client():
    """A test client logged in as an Admin."""
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


@pytest.fixture(autouse=True)
def listing_settings(monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))


def _ebay(monkeypatch, errors=None, item_id="5000"):
    """Fake eBay's reply to the re-list; returns the list of calls made."""
    calls = []

    def fake_call(verb, data):
        calls.append((verb, data))
        summary = {"errors": errors or [], "warnings": [], "fees": []}
        if errors:
            return None, summary
        return {"ItemID": item_id, "StartTime": "2026-11-17T10:00:00.000Z"}, summary

    monkeypatch.setattr(api, "_call", fake_call)
    return calls


def _post(client, item_id):
    return client.post("/parkedlistingsview/relist/" + item_id)


def _make_draft(unit, **fields):
    fields.setdefault("title", "Draft")
    draft = EbayDraft(unit_id=unit.id, **fields)
    db.session.add(draft)
    db.session.commit()
    return draft


def _old_draft(unit, **fields):
    return _make_draft(
        unit,
        title="Old Title",
        price=19.0,
        description="<p>Old text</p>",
        item_specifics="Genre: Rock",
        status="published",
        **fields,
    )


def test_a_relist_inside_the_window_links_the_new_listing_at_once(client, monkeypatch):
    calls = _ebay(monkeypatch)
    listing = _parked("100", days_ago=3)
    unit = db.session.query(type(_unit())).filter_by(ebay_listing_id="100").one()
    draft = _old_draft(unit, ebay_listing_id="100")

    response = _post(client, "100")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/parkedlistingsview/list/")
    assert calls == [("RelistFixedPriceItem", {"Item": {"ItemID": "100"}})]
    new = db.session.get(EbayListing, "5000")
    assert new.title == listing.title and new.price == 12.5
    assert new.start_time == datetime.datetime(2026, 11, 17, 10, 0)
    assert unit.ebay_listing_id == "5000"
    assert draft.ebay_listing_id == "5000" and draft.status == "published"
    assert db.session.get(EbayListing, "100").parked_at is None
    assert tasks.counts()["parked"] == 0


def test_ebay_refusing_the_relist_makes_a_draft_from_the_old_one(client, monkeypatch):
    calls = _ebay(monkeypatch, errors=["The item can't be relisted."])
    listing = _parked("100", days_ago=3)
    unit = db.session.query(type(_unit())).filter_by(ebay_listing_id="100").one()
    _old_draft(unit, ebay_listing_id="100")

    response = _post(client, "100")

    new = db.session.query(EbayDraft).filter_by(status="draft").one()
    assert response.headers["Location"].endswith("/show/" + str(new.id))
    assert [c[0] for c in calls] == ["RelistFixedPriceItem"]
    assert (new.title, new.price, new.description, new.item_specifics) == (
        "Old Title",
        19.0,
        "<p>Old text</p>",
        "Genre: Rock",
    )
    assert new.unit_id == unit.id and new.ebay_listing_id is None
    # Nothing was changed on the parked listing: it is still queued.
    assert db.session.get(EbayListing, "100").parked_at is not None
    assert unit.ebay_listing_id == listing.item_id
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "eBay refused the re-list: The item can&#39;t be relisted." in page


def test_outside_the_window_it_never_calls_ebay(client, monkeypatch):
    calls = _ebay(monkeypatch)
    _parked("100", days_ago=95)

    response = _post(client, "100")

    assert calls == []
    assert db.session.query(EbayDraft).filter_by(status="draft").count() == 1
    assert "/show/" in response.headers["Location"]
    assert db.session.get(EbayListing, "100").parked_at is not None


def test_the_window_follows_the_setting(client, monkeypatch):
    calls = _ebay(monkeypatch)
    monkeypatch.setitem(fbc.app.config, "EBAY_RELIST_DAYS", 120)
    _parked("100", days_ago=100)

    _post(client, "100")

    assert len(calls) == 1


def test_with_no_old_draft_the_draft_is_built_from_the_unit(client, monkeypatch):
    _ebay(monkeypatch, errors=["No."])
    unit = _carnaval()
    _parked("100", unit=unit, days_ago=3)

    _post(client, "100")

    draft = db.session.query(EbayDraft).one()
    assert draft.unit_id == unit.id and draft.status == "draft"
    assert draft.title and draft.item_specifics


def test_an_open_draft_is_reused_not_doubled(client, monkeypatch):
    _ebay(monkeypatch, errors=["No."])
    listing = _parked("100", days_ago=3)
    unit = relisting.unit_of(listing)
    open_draft = _make_draft(unit, title="Open")

    _post(client, "100")

    assert db.session.query(EbayDraft).one().id == open_draft.id


def test_dev_mode_refuses_the_relist_and_changes_nothing(client, monkeypatch):
    calls = _ebay(monkeypatch)
    monkeypatch.setattr(devmode, "on", lambda: True)
    _parked("100", days_ago=3)

    response = _post(client, "100")

    assert calls == []
    assert db.session.query(EbayDraft).count() == 0
    assert db.session.get(EbayListing, "100").parked_at is not None
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Dev Mode is turned on, re-listing on eBay disabled." in page


def test_the_publish_call_itself_is_refused_in_dev_mode(app, monkeypatch):
    from fbc.ebay import publish

    fbc.app.config["DEV_MODE"] = True
    try:
        listing = _parked("100", days_ago=3)
        item_id, summary = publish.relist_listing(listing)
    finally:
        fbc.app.config["DEV_MODE"] = False
    assert item_id is None and summary["errors"]
    assert db.session.get(EbayListing, "100").parked_at is not None


def test_only_a_queued_listing_can_be_relisted(client, monkeypatch):
    calls = _ebay(monkeypatch)
    _parked("1", unit=_unit("sold", sold=True))
    _parked("2", parked_at=None)
    db.session.add(EbayListing(item_id="3", title="Live", price=1.0, url="u"))
    db.session.commit()

    for item_id in ("1", "2", "3", "nope"):
        assert _post(client, item_id).status_code == 404
    assert calls == []


def test_each_row_has_a_confirming_relist_button(client):
    _parked("100", days_ago=3)

    html = client.get("/parkedlistingsview/list/").get_data(as_text=True)

    assert "/parkedlistingsview/relist/100" in html
    assert "confirm(" in html and "Re-list</button>" in html


def test_a_listing_with_a_unit_is_the_one_a_relist_moves(client, monkeypatch):
    _ebay(monkeypatch)
    listing = _parked("100", days_ago=3)
    other = _unit("Other")

    relisting.relist_or_draft(listing)

    assert other.ebay_listing_id is None


def test_the_suggestion_is_the_longest_out_and_only_when_room_beats_waiting():
    _parked("100", days_ago=2)
    oldest = _parked("200", days_ago=20)

    assert tasks.nothing_better_to_list(5, 4) == oldest
    assert tasks.nothing_better_to_list(5, 5) is None
    assert tasks.nothing_better_to_list(0, 0) is None
    assert tasks.nothing_better_to_list(-3, 0) is None
    assert tasks.nothing_better_to_list(3, 0) == oldest


def test_the_suggestion_is_none_with_an_empty_queue():
    assert tasks.nothing_better_to_list(10, 0) is None


def _synced():
    SyncRecord.record_finished(SyncRecord.LISTINGS_SYNC, localtime.utcnow())


def test_the_home_page_suggests_the_queue_only_with_room_over_waiting(client):
    _synced()
    _parked("100", days_ago=9, title="Queued Record")

    html = client.get("/").get_data(as_text=True)

    assert "Nothing better to list" in html and "Queued Record" in html

    # New stock first: a Unit waiting for each of the 250 free insertions.
    for n in range(250):
        _unit("Waiting %d" % n)
    html = client.get("/").get_data(as_text=True)
    assert "Nothing better to list" not in html


def test_the_home_page_does_not_suggest_before_the_first_listings_sync(client):
    _parked("100", days_ago=9)

    assert "Nothing better to list" not in client.get("/").get_data(as_text=True)
