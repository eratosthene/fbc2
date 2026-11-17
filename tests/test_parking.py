"""`flask park-ended-listings`: parking listings that were ended by hand."""

import datetime

import fbc
from fbc import db
from fbc.core import localtime
from fbc.models.ebay import EbayDraft, EbayListing, EbayOrder, EbayOrderItem
from fbc.models.other import OtherItem
from fbc.models.inventory import StorageBox
from fbc.sales import parking, tasks
from test_tasks import _unit


def _ended(item_id, days_ago=10, unit=None, **fields):
    """A listing ended by hand (ended, no parked_at), linked to `unit`
    (a new unsold one by default; False for none)."""
    fields.setdefault("ended", True)
    fields.setdefault(
        "end_time", localtime.utcnow() - datetime.timedelta(days=days_ago)
    )
    listing = EbayListing(
        item_id=item_id,
        title="Ended " + item_id,
        price=9.0,
        url="https://www.ebay.com/itm/" + item_id,
        **fields,
    )
    db.session.add(listing)
    if unit is None:
        unit = _unit("Unit " + item_id)
    if unit:
        unit.ebay_listing_id = item_id
    db.session.commit()
    return listing


def _ids():
    return [x.item_id for x in parking.candidates()]


def _run(*args):
    result = fbc.app.test_cli_runner().invoke(args=["park-ended-listings", *args])
    assert result.exception is None, result.output
    return result.output


def test_an_unsold_ended_listing_with_one_unit_is_a_candidate():
    _ended("1")
    assert _ids() == ["1"]


def test_what_doesnt_fit_the_queue_is_left_out():
    _ended("sold", unit=_unit("s", sold=True))
    _ended("archived-unit", unit=_unit("a", archived=True))
    _ended("no-unit", unit=False)
    _ended("active", ended=False)
    _ended("archived", archived=True)
    _ended("parked", parked_at=localtime.utcnow())
    _ended("no-end-time", end_time=None)
    two = _ended("two")
    _unit("second").ebay_listing_id = two.item_id
    db.session.commit()
    box = StorageBox(name="Box")
    db.session.add(box)
    db.session.commit()
    _ended("mystery", unit=False, pick_from_box_id=box.id, pick_count=2)
    other = _ended("other", unit=False)
    db.session.add(OtherItem(name="Thing", ebay_listing_id=other.item_id))
    db.session.commit()
    assert _ids() == []


def test_a_listing_that_ended_with_an_order_is_left_out():
    _ended("ordered")
    db.session.add(
        EbayOrder(order_id="o1", created_time=localtime.utcnow(), buyer="b", price=1.0)
    )
    db.session.add(EbayOrderItem(ebay_order_id="o1", ebay_listing_id="ordered"))
    db.session.commit()
    assert _ids() == []


def test_a_unit_already_re_listed_is_left_out():
    unit = _unit("u")
    _ended("old", unit=unit)
    _ended("new", unit=False, ended=False, end_time=None)
    db.session.add(EbayDraft(unit_id=unit.id, title="D", ebay_listing_id="new"))
    db.session.commit()
    assert _ids() == []


def test_a_unit_that_moved_to_a_new_listing_is_left_out():
    unit = _unit("u")
    _ended("old", unit=unit)
    unit.ebay_listing_id = _ended("new", ended=False, end_time=None, unit=False).item_id
    db.session.commit()
    assert _ids() == []


def test_the_dry_run_changes_nothing_and_says_what_it_would_do():
    _ended("1", days_ago=5)
    _ended("2", days_ago=200)
    out = _run()
    assert "Would park 2 listings, 1 past the re-list window of 90 days" in out
    assert "inside the re-list window" in out and "past the re-list window (" in out
    assert "5 days ago" in out and "200 days ago" in out
    assert "noticed the listing was gone" in out and "dry run" in out
    assert (
        db.session.query(EbayListing).filter(EbayListing.parked_at.isnot(None)).count()
        == 0
    )


def test_apply_parks_from_the_end_time_and_is_idempotent():
    listing = _ended("1", days_ago=5)
    when = listing.end_time
    assert "Parked 1 listing," in _run("--apply")
    db.session.expire_all()
    assert listing.parked_at == when
    assert set(db.session.scalars(tasks.ids_parked_listings())) == {"1"}
    assert "Parked 0 listings" in _run("--apply")
    db.session.expire_all()
    assert listing.parked_at == when


def test_limit_takes_the_oldest_first():
    _ended("new", days_ago=2)
    _ended("old", days_ago=50)
    _ended("mid", days_ago=20)
    _run("--apply", "--limit", "2")
    parked = {
        x.item_id
        for x in db.session.query(EbayListing).filter(EbayListing.parked_at.isnot(None))
    }
    assert parked == {"old", "mid"}


def test_parked_ones_show_in_the_queue_longest_out_first():
    _ended("b", days_ago=20)
    _ended("a", days_ago=200)
    _ended("c", days_ago=3)
    _run("--apply")
    assert tasks.first_parked().item_id == "a"
    assert set(db.session.scalars(tasks.ids_parked_listings())) == {"a", "b", "c"}
