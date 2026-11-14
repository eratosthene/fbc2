import datetime
import json

import pytest

import fbc
from fbc import db, insertions, listing, sync
from fbc.models.ebay import EbayDraft, EbayListing
from fbc.util import mark_ended_listings
from test_listing import SETTINGS, _carnaval

D = datetime.datetime


# --- The month ---


def test_month_bounds_are_the_pacific_calendar_month_in_utc():
    # October is still daylight saving time (UTC-7); November is not (UTC-8).
    start, end = insertions.month_bounds(D(2026, 10, 15, 12))
    assert start == D(2026, 10, 1, 7)
    assert end == D(2026, 11, 1, 7)
    start, end = insertions.month_bounds(D(2026, 11, 15))
    # Daylight saving ends on Nov 1 itself, after midnight.
    assert start == D(2026, 11, 1, 7)
    assert end == D(2026, 12, 1, 8)


def test_a_utc_time_just_after_a_pacific_month_ends_is_still_in_it():
    # 03:00 UTC on Nov 1 is 20:00 on Oct 31 in Pacific time.
    start, _ = insertions.month_bounds(D(2026, 11, 1, 3))
    assert start == D(2026, 10, 1, 7)


def test_month_bounds_across_the_new_year():
    start, end = insertions.month_bounds(D(2026, 12, 20))
    assert (start, end) == (D(2026, 12, 1, 8), D(2027, 1, 1, 8))


def test_daylight_saving_runs_from_the_second_sunday_of_march():
    assert insertions.pacific_offset(D(2026, 3, 8, 9, 59)) == datetime.timedelta(
        hours=-8
    )
    assert insertions.pacific_offset(D(2026, 3, 8, 10)) == datetime.timedelta(hours=-7)
    assert insertions.pacific_offset(D(2026, 11, 1, 8, 59)) == datetime.timedelta(
        hours=-7
    )
    assert insertions.pacific_offset(D(2026, 11, 1, 9)) == datetime.timedelta(hours=-8)


# --- Counting ---

NOW = D(2026, 11, 15, 12)


def test_a_listing_started_this_month_is_one_insertion_and_renews_in_30_days():
    result = insertions.count_insertions([(D(2026, 11, 10), None)], NOW, 250)
    # Started Nov 10, renews Dec 10: not this month.
    assert (result["used"], result["due"], result["projected"]) == (1, 0, 1)


def test_a_listing_started_early_in_the_month_renews_before_the_end():
    result = insertions.count_insertions([(D(2026, 11, 2), None)], NOW, 250)
    # Nov 2 and Dec 2 (not this month).
    assert result["projected"] == 1
    result = insertions.count_insertions([(D(2026, 10, 20), None)], NOW, 250)
    # Oct 20, Nov 19 (due), Dec 19.
    assert (result["used"], result["due"]) == (0, 1)


def test_an_old_listing_renews_once_a_month():
    result = insertions.count_insertions([(D(2025, 1, 1), None)], NOW, 250)
    assert result["projected"] == 1


def test_a_renewal_before_now_is_used():
    result = insertions.count_insertions([(D(2026, 10, 10), None)], NOW, 250)
    # Oct 10 + 30 days is Nov 9.
    assert (result["used"], result["due"]) == (1, 0)


def test_an_ended_listing_still_counts_up_to_its_end():
    ended = (D(2026, 10, 1), D(2026, 11, 12))  # renewed Oct 31, then ended
    result = insertions.count_insertions([ended], NOW, 250)
    assert (result["used"], result["due"]) == (0, 0) or result["projected"] == 0
    renewed_in_month = (D(2026, 10, 5), D(2026, 11, 20 - 10))
    result = insertions.count_insertions([renewed_in_month], NOW, 250)
    # Oct 5, Nov 4 (before it ended Nov 10), so Nov 4 counts.
    assert result["used"] == 1


def test_an_ended_listing_does_not_renew_after_its_end():
    ended = (D(2026, 10, 20), D(2026, 11, 1, 12))
    result = insertions.count_insertions([ended], NOW, 250)
    assert result["projected"] == 0


def test_a_listing_ended_the_day_it_started_counts_once():
    start = D(2026, 11, 3)
    result = insertions.count_insertions([(start, start)], NOW, 250)
    assert result["projected"] == 1


def test_a_listing_with_no_start_time_is_skipped():
    assert insertions.count_insertions([(None, None)], NOW, 250)["projected"] == 0


@pytest.mark.parametrize(
    "count, near, over", [(239, False, False), (240, True, False), (251, True, True)]
)
def test_warns_within_ten_of_the_limit_and_over_it(count, near, over):
    listings = [(D(2026, 11, 3), None)] * count
    result = insertions.count_insertions(listings, NOW, 250)
    assert (result["projected"], result["near"], result["over"]) == (count, near, over)


def test_publish_warning_counts_the_new_listing():
    base = {"synced": NOW, "limit": 250}
    assert insertions.publish_warning(dict(base, projected=249)) is None
    assert "251" in insertions.publish_warning(dict(base, projected=250))


def test_no_publish_warning_before_the_first_sync():
    assert (
        insertions.publish_warning({"synced": None, "limit": 1, "projected": 5}) is None
    )


def test_uncovered_insertion_fee_is_found_in_a_check_result():
    fees = [{"name": "InsertionFee", "amount": 0.35, "discount": 0.0, "net": 0.35}]
    assert (
        insertions.uncovered_insertion_fee({"source": "verify", "fees": fees}) == 0.35
    )
    covered = [{"name": "InsertionFee", "amount": 0.35, "discount": 0.35, "net": 0.0}]
    assert (
        insertions.uncovered_insertion_fee({"source": "verify", "fees": covered})
        is None
    )
    assert (
        insertions.uncovered_insertion_fee({"source": "publish", "fees": fees}) is None
    )
    assert insertions.uncovered_insertion_fee(None) is None


# --- Marking listings ended ---


def _listing(item_id, start=D(2026, 10, 1), **kw):
    row = EbayListing(item_id=item_id, title=item_id, price=1.0, start_time=start, **kw)
    db.session.add(row)
    db.session.commit()
    return row


def test_a_listing_missing_from_the_active_list_is_marked_ended(app):
    gone, kept = _listing("1"), _listing("2")
    previous = D(2026, 11, 1)

    assert mark_ended_listings({"2"}, previous) == 1

    assert (gone.ended, gone.end_time) == (True, previous)
    assert (kept.ended, kept.end_time) == (False, None)


def test_end_time_is_the_start_with_no_earlier_sync_or_one_before_the_start(app):
    first = _listing("1", start=D(2026, 10, 5))
    late = _listing("2", start=D(2026, 11, 5))

    mark_ended_listings(set(), None)
    assert first.end_time == D(2026, 10, 5)

    late.ended, late.end_time = False, None
    mark_ended_listings(set(), D(2026, 11, 1))
    assert late.end_time == D(2026, 11, 5)


def test_a_listing_back_in_the_active_list_is_not_ended(app):
    row = _listing("1", ended=True, end_time=D(2026, 10, 9))

    mark_ended_listings({"1"}, None)

    assert (row.ended, row.end_time) == (False, None)


def test_an_already_ended_listing_keeps_its_end_time(app):
    row = _listing("1", ended=True, end_time=D(2026, 10, 9))

    assert mark_ended_listings(set(), D(2026, 11, 1)) == 0
    assert row.end_time == D(2026, 10, 9)


# --- Reading eBay's active list ---


def _page(items, page, pages, total):
    return {
        "ActiveList": {
            "PaginationResult": {
                "TotalNumberOfPages": str(pages),
                "TotalNumberOfEntries": str(total),
            },
            "ItemArray": {"Item": items} if items else None,
        }
    }


def _answer(monkeypatch, pages):
    calls = iter(pages)

    def fake_call(verb, data):
        page = next(calls)
        if isinstance(page, str):
            return None, {"errors": [page], "warnings": [], "fees": []}
        return page, {"errors": [], "warnings": [], "fees": []}

    monkeypatch.setattr(listing, "_call", fake_call)


def test_fetch_reads_every_page(monkeypatch):
    _answer(
        monkeypatch,
        [_page([{"ItemID": "1"}], 1, 2, 2), _page({"ItemID": "2"}, 2, 2, 2)],
    )
    assert [i["ItemID"] for i in listing.fetch_active_items()] == ["1", "2"]


def test_fetch_accepts_no_listings_when_ebay_says_zero(monkeypatch):
    _answer(monkeypatch, [_page(None, 1, 0, 0)])
    assert listing.fetch_active_items() == []


def test_fetch_fails_when_a_page_fails(monkeypatch):
    _answer(monkeypatch, [_page([{"ItemID": "1"}], 1, 2, 2), "HTTP 500"])
    with pytest.raises(listing.ActiveListError, match="HTTP 500"):
        listing.fetch_active_items()


def test_fetch_fails_on_a_short_list(monkeypatch):
    _answer(monkeypatch, [_page([{"ItemID": "1"}], 1, 1, 3)])
    with pytest.raises(listing.ActiveListError, match="says there are 3"):
        listing.fetch_active_items()


def test_fetch_fails_when_the_reply_has_no_count(monkeypatch):
    _answer(monkeypatch, [{"Ack": "Success"}])
    with pytest.raises(listing.ActiveListError):
        listing.fetch_active_items()


# --- The listings sync ---


def _sync(client):
    response = client.get("/syncebaylistings", headers={"Accept": "application/json"})
    return client.get(response.get_json()["url"]).get_json()


def _active_item(item_id):
    return {
        "ItemID": item_id,
        "Title": "Title " + item_id,
        "BuyItNowPrice": {"value": "9.99"},
        "ListingDetails": {
            "ViewItemURL": "https://www.ebay.com/itm/" + item_id,
            "StartTime": "2026-11-02T10:00:00.000Z",
        },
    }


def test_the_sync_marks_ended_and_records_when_it_finished(
    app, inline_sync, monkeypatch
):
    gone = _listing("1")
    monkeypatch.setattr("fbc.syncjobs.fetch_active_items", lambda: [_active_item("2")])

    job = _sync(app.test_client())

    assert job["status"] == "done"
    db.session.expire_all()
    assert gone.ended is True
    assert db.session.get(EbayListing, "2").ended is False
    assert sync.last_finished(sync.LISTINGS_SYNC) is not None


def test_a_failed_sync_marks_nothing_and_records_nothing(app, inline_sync, monkeypatch):
    row = _listing("1")

    def fail():
        raise listing.ActiveListError(
            "Read 0 active listings but eBay says there are 5."
        )

    monkeypatch.setattr("fbc.syncjobs.fetch_active_items", fail)

    job = _sync(app.test_client())

    assert job["status"] == "failed"
    db.session.expire_all()
    assert row.ended is False
    assert sync.last_finished(sync.LISTINGS_SYNC) is None


def test_the_second_sync_ends_a_listing_from_the_first_syncs_time(
    app, inline_sync, monkeypatch
):
    first = D(2026, 11, 1)
    sync.record_finished(sync.LISTINGS_SYNC, first)
    row = _listing("1")
    monkeypatch.setattr("fbc.syncjobs.fetch_active_items", lambda: [])

    _sync(app.test_client())

    db.session.expire_all()
    assert row.end_time == first


# --- The home page and publishing ---


def test_the_home_page_asks_for_a_sync_before_the_first_one(app):
    html = app.test_client().get("/").get_data(as_text=True)
    assert "Run" in html and "the eBay listings sync" in html
    assert "Free insertions this month" not in html


def test_the_home_page_shows_listings_and_insertions(app):
    sync.record_finished(sync.LISTINGS_SYNC, sync.utcnow())
    _listing("1", start=sync.utcnow())
    _listing("2", start=sync.utcnow(), ended=True, end_time=sync.utcnow())

    html = app.test_client().get("/").get_data(as_text=True)

    assert "<strong>1</strong> active on eBay" in html
    assert "<strong>2</strong> free insertions used this month" in html
    assert "<strong>248</strong> of 250 left" in html
    assert "Last synced" in html


def test_the_home_page_says_when_the_listings_last_synced(app):
    # 2026-11-13 01:45 UTC is 2026-11-12 5:45 PM in Pacific (PST).
    sync.record_finished(sync.LISTINGS_SYNC, D(2026, 11, 13, 1, 45))

    html = " ".join(app.test_client().get("/").get_data(as_text=True).split())

    assert "Last synced 2026-11-12 5:45 PM." in html


def test_the_home_page_never_shows_fewer_than_none_left(app, monkeypatch):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "LISTING", {"FREE_INSERTIONS": 1}
    )
    sync.record_finished(sync.LISTINGS_SYNC, sync.utcnow())
    _listing("1", start=sync.utcnow())
    _listing("2", start=sync.utcnow())

    html = app.test_client().get("/").get_data(as_text=True)

    assert "<strong>0</strong> of 1 left" in html


def test_the_home_page_warns_when_close_to_the_limit(app, monkeypatch):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "LISTING", {"FREE_INSERTIONS": 2}
    )
    sync.record_finished(sync.LISTINGS_SYNC, sync.utcnow())
    _listing("1", start=sync.utcnow())

    html = app.test_client().get("/").get_data(as_text=True)

    assert "alert-warning" in html and "Only 1" in html


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _ready_draft(client, monkeypatch):
    def fake_call(verb, data):
        resp = {"ItemID": "123", "StartTime": "2026-11-11T16:00:00.000Z"}
        return resp, listing.summarize_response(resp)

    monkeypatch.setattr(listing, "_call", fake_call)
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    draft.set_picture_list(["https://i.ebayimg.com/a.jpg"])
    db.session.commit()
    return draft


def _fill_month(monkeypatch, listings=1, limit=1):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"]["LISTING"], "FREE_INSERTIONS", limit
    )
    sync.record_finished(sync.LISTINGS_SYNC, sync.utcnow())
    for n in range(listings):
        _listing("old" + str(n), start=sync.utcnow())


def test_publishing_past_the_limit_needs_publish_anyway(client, monkeypatch):
    draft = _ready_draft(client, monkeypatch)
    _fill_month(monkeypatch)

    page = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )
    client.post("/ebaydraftmodelview/publish/" + str(draft.id))
    db.session.expire_all()

    assert "Publish anyway" in page
    assert draft.status == "draft"


def test_publish_anyway_goes_through(client, monkeypatch):
    draft = _ready_draft(client, monkeypatch)
    _fill_month(monkeypatch)

    client.post(
        "/ebaydraftmodelview/publish/" + str(draft.id), data={"publish_anyway": "1"}
    )
    db.session.expire_all()

    assert draft.status == "published"


def test_publishing_under_the_limit_needs_no_confirming(client, monkeypatch):
    draft = _ready_draft(client, monkeypatch)
    _fill_month(monkeypatch, limit=5)

    page = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )
    client.post("/ebaydraftmodelview/publish/" + str(draft.id))
    db.session.expire_all()

    assert "Publish anyway" not in page
    assert draft.status == "published"


def test_the_draft_page_shows_an_insertion_fee_eBay_did_not_cover(client, monkeypatch):
    draft = _ready_draft(client, monkeypatch)
    fees = [
        {
            "name": "InsertionFee",
            "label": "Insertion fee",
            "amount": 0.35,
            "discount": 0.0,
            "net": 0.35,
        }
    ]
    draft.messages = json.dumps(
        {"errors": [], "warnings": [], "fees": fees, "source": "verify"}
    )
    db.session.commit()

    page = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )

    assert "insertion fee of $0.35" in page


# --- Calibrating with the eBay app's number ---


def _synced_month(count=3):
    sync.record_finished(sync.LISTINGS_SYNC, sync.utcnow())
    for n in range(count):
        _listing("c" + str(n), start=sync.utcnow())


def test_calibrate_adds_the_difference_to_used(app):
    _synced_month(3)

    # eBay says 250 - 181 = 69 used; fbc counts 3.
    assert insertions.calibrate(181) == 66

    summary = insertions.listing_summary()
    assert summary["used"] == 69
    assert summary["projected"] == 69
    assert summary["calibration"]["adjustment"] == 66


def test_a_new_listing_after_calibrating_adds_to_the_count(app):
    _synced_month(3)
    insertions.calibrate(181)

    _listing("later", start=sync.utcnow())

    assert insertions.listing_summary()["used"] == 70


def test_calibrating_again_replaces_the_last_entry(app):
    _synced_month(3)
    insertions.calibrate(181)
    insertions.calibrate(200)

    assert insertions.listing_summary()["used"] == 50


def test_calibration_does_not_carry_into_the_next_month(app):
    _synced_month(1)
    last_month = sync.utcnow() - datetime.timedelta(days=40)
    insertions.calibrate(100, now=last_month)

    assert insertions.listing_summary()["calibration"] is None
    assert insertions.listing_summary()["used"] == 1


def test_calibrate_route_saves_the_number_and_the_home_page_uses_it(app):
    _synced_month(3)
    client = app.test_client()

    response = client.post("/calibrateinsertions", data={"remaining": "181"})
    html = client.get("/").get_data(as_text=True)

    assert response.status_code == 302
    assert insertions.listing_summary()["calibration"]["adjustment"] == 66
    assert "<strong>69</strong> free insertions used this month" in html


@pytest.mark.parametrize("value", ["", "abc", "-1", "251"])
def test_calibrate_route_rejects_a_bad_number(app, value):
    _synced_month(3)
    client = app.test_client()

    client.post("/calibrateinsertions", data={"remaining": value})

    assert insertions.listing_summary()["calibration"] is None


# --- Renewals from eBay's Time Left ---

FETCHED = D(2026, 11, 12, 12)


def test_renewal_time_adds_the_time_left_to_when_the_list_was_read():
    item = {"TimeLeft": "P29DT3H2M1S"}
    assert insertions.renewal_time(item, FETCHED) == D(2026, 12, 11, 15, 2, 1)
    assert insertions.renewal_time({"TimeLeft": "PT5H"}, FETCHED) == D(2026, 11, 12, 17)


def test_renewal_time_falls_back_to_the_end_time():
    item = {"ListingDetails": {"EndTime": "2026-12-01T10:00:00.000Z"}}
    assert insertions.renewal_time(item, FETCHED) == D(2026, 12, 1, 10)


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"TimeLeft": "soon"},
        {"TimeLeft": "P"},
        {"TimeLeft": "P90D"},
        {"ListingDetails": {"EndTime": "2099-12-31T00:00:00.000Z"}},
    ],
)
def test_renewal_time_is_none_without_a_usable_date(item):
    assert insertions.renewal_time(item, FETCHED) is None


def test_renewals_follow_the_saved_date_not_the_start_time():
    start = D(2026, 10, 3)
    # By the start time it renewed Nov 2.
    result = insertions.count_insertions([(start, None)], NOW, 250)
    assert (result["used"], result["due"]) == (1, 0)
    # eBay says the next renewal is Nov 20, so it renews then instead.
    result = insertions.count_insertions([(start, None, D(2026, 11, 20))], NOW, 250)
    assert (result["used"], result["due"]) == (0, 1)


def test_a_saved_date_in_the_month_is_used_even_when_past():
    result = insertions.count_insertions(
        [(D(2026, 8, 1), None, D(2026, 11, 3))], NOW, 250
    )
    assert (result["used"], result["due"]) == (1, 0)


def test_an_ended_listing_uses_its_saved_date_up_to_its_end():
    ended = (D(2026, 10, 1), D(2026, 11, 10), D(2026, 11, 5))
    assert insertions.count_insertions([ended], NOW, 250)["used"] == 1
    ended = (D(2026, 10, 1), D(2026, 11, 3), D(2026, 11, 5))
    assert insertions.count_insertions([ended], NOW, 250)["used"] == 0


def test_the_sync_saves_when_each_listing_renews(app, inline_sync, monkeypatch):
    item = dict(_active_item("2"), TimeLeft="P10D")
    monkeypatch.setattr("fbc.syncjobs.fetch_active_items", lambda: [item])

    _sync(app.test_client())

    db.session.expire_all()
    renews = db.session.get(EbayListing, "2").renews_at
    assert abs(
        (renews - sync.utcnow()) - datetime.timedelta(days=10)
    ) < datetime.timedelta(minutes=5)


# --- Monthly renewals, from three real listings ---


@pytest.mark.parametrize(
    "start, time_left, renews",
    [
        # Read Nov 12 2026 00:26:54 UTC. Each renews on its start's day of
        # the month at its start's Pacific time, in standard time by then.
        ("2024-08-22T23:40:43", (11, 0, 13, 49), D(2026, 11, 23, 0, 40, 43)),
        ("2025-03-07T02:51:30", (25, 2, 24, 36), D(2026, 12, 7, 2, 51, 30)),
        ("2025-04-10T20:23:20", (28, 20, 56, 26), D(2026, 12, 10, 21, 23, 20)),
    ],
)
def test_real_listings_renew_monthly_on_their_start_day_in_pacific_time(
    start, time_left, renews
):
    fetched = D(2026, 11, 12, 0, 26, 54)
    days, hours, minutes, seconds = time_left
    item = {"TimeLeft": f"P{days}DT{hours}H{minutes}M{seconds}S"}
    assert insertions.renewal_time(item, fetched) == renews
    began = D.fromisoformat(start)
    # With no saved date, the count must put the November renewal where
    # eBay's own Time Left puts it, when that is in November.
    if renews.month == 11:
        result = insertions.count_insertions([(began, None)], fetched, 250)
        assert (result["used"], result["due"]) == (0, 1)
        with_saved = insertions.count_insertions([(began, None, renews)], fetched, 250)
        assert with_saved == result


def test_a_listing_renews_exactly_once_a_month_whatever_the_month_length():
    for now in (D(2026, 2, 20), D(2026, 3, 20), D(2026, 12, 20)):
        result = insertions.count_insertions([(D(2025, 5, 10, 18), None)], now, 250)
        assert result["projected"] == 1


def test_a_listing_started_on_the_31st_renews_on_the_last_day_of_a_short_month():
    start = D(2025, 12, 31, 20)  # Dec 31 12:00 Pacific
    result = insertions.count_insertions([(start, None)], D(2026, 2, 10), 250)
    assert (result["used"], result["due"]) == (0, 1)
    result = insertions.count_insertions([(start, None)], D(2026, 3, 1, 12), 250)
    assert result["due"] == 1


def test_a_renewal_keeps_its_pacific_time_across_daylight_saving():
    start = D(2025, 7, 15, 19)  # 12:00 PDT
    result = insertions.count_insertions([(start, None)], D(2026, 1, 10), 250)
    # Jan 15 12:00 PST is 20:00 UTC, so it's due on Jan 15 at 20:00, not 19:00.
    assert result["due"] == 1
    assert (
        insertions.count_insertions([(start, None)], D(2026, 1, 15, 19, 30), 250)["due"]
        == 1
    )
    assert (
        insertions.count_insertions([(start, None)], D(2026, 1, 15, 20, 30), 250)[
            "used"
        ]
        == 1
    )
