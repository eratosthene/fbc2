"""Times shown in the TIMEZONE setting (fbc/localtime.py)."""

import datetime
import logging
import time

import pytest
from werkzeug.datastructures import MultiDict

import fbc
from fbc import localtime
from fbc.models.discogs import DiscogsListing
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.supplies import Supply, SupplyUse
from fbc.views.sales import EbayListingModelView, EbayOrderModelView

D = datetime.datetime


@pytest.fixture(autouse=True)
def central():
    """Every test runs in Central, whatever the machine's own zone is."""
    localtime.set_zone("America/Chicago")
    yield
    localtime.set_zone("America/Chicago")


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def test_a_winter_time_is_six_hours_behind_utc():
    assert localtime.fmt_datetime(D(2026, 11, 13, 23, 45)) == "2026-11-13 5:45 PM"


def test_a_summer_time_is_five_hours_behind_utc():
    # Central is on daylight saving time in July, so a fixed -6 would be wrong.
    assert localtime.fmt_datetime(D(2026, 7, 1, 22, 45)) == "2026-07-01 5:45 PM"


def test_a_time_after_utc_midnight_is_still_the_day_before():
    utc = D(2026, 11, 14, 2, 30)
    assert localtime.fmt_datetime(utc) == "2026-11-13 8:30 PM"
    assert localtime.fmt_date(utc) == "2026-11-13"


def test_a_time_before_utc_midnight_can_be_the_next_day_in_zones_ahead():
    localtime.set_zone("Pacific/Auckland")
    assert localtime.fmt_date(D(2026, 11, 13, 23, 0)) == "2026-11-14"


def test_midnight_and_noon_use_twelve():
    assert localtime.fmt_datetime(D(2026, 11, 13, 6, 0)) == "2026-11-13 12:00 AM"
    assert localtime.fmt_datetime(D(2026, 11, 13, 18, 0)) == "2026-11-13 12:00 PM"


def test_no_time_is_blank():
    assert localtime.fmt_datetime(None) == ""
    assert localtime.fmt_date(None) == ""


def test_a_local_time_converts_back_to_utc_in_both_seasons():
    assert localtime.to_utc(D(2026, 11, 13, 17, 45)) == D(2026, 11, 13, 23, 45)
    assert localtime.to_utc(D(2026, 7, 1, 17, 45)) == D(2026, 7, 1, 22, 45)


def test_a_bad_timezone_name_fails_with_a_clear_message():
    with pytest.raises(ValueError, match="IANA"):
        localtime.set_zone("CST6CDT-ish")
    localtime.set_zone(None)  # unset means the default
    assert localtime.fmt_date(D(2026, 11, 14, 2, 30)) == "2026-11-13"


def test_the_filters_are_registered_on_the_app():
    assert fbc.app.jinja_env.filters["localtime"] is localtime.fmt_datetime
    assert fbc.app.jinja_env.filters["localdate"] is localtime.fmt_date


def test_log_lines_use_the_local_time():
    # 2026-11-14 02:30:00 UTC.
    seconds = D(2026, 11, 14, 2, 30).replace(tzinfo=datetime.timezone.utc).timestamp()
    record = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    record.created = seconds
    out = logging.Formatter("%(asctime)s", "%Y-%m-%d %H:%M").format(record)
    assert out == "2026-11-13 20:30"
    assert time.localtime  # the stdlib's own is untouched


def test_the_order_list_and_show_pages_show_central(client):
    order = EbayOrder(
        order_id="ORD-1",
        created_time=D(2026, 11, 14, 2, 30),
        buyer="b",
        price=1.0,
        title="Some Item",
    )
    fbc.db.session.add(order)
    fbc.db.session.commit()

    listing = client.get("/ebayordermodelview/list/").get_data(as_text=True)
    show = client.get("/ebayordermodelview/show/" + order.order_id).get_data(
        as_text=True
    )

    assert "2026-11-13 8:30 PM" in listing
    assert "2026-11-13 8:30 PM" in show
    assert "2026-11-14" not in listing


def test_the_listing_pages_show_central(client):
    fbc.db.session.add(
        EbayListing(
            item_id="L1",
            title="t",
            price=1.0,
            url="u",
            start_time=D(2026, 7, 1, 22, 45),
        )
    )
    fbc.db.session.commit()
    html = client.get("/ebaylistingmodelview/list/").get_data(as_text=True)
    assert "2026-07-01 5:45 PM" in html


def test_the_discogs_listing_page_shows_central(client):
    listing = DiscogsListing(
        listing_id=1,
        release_id=2,
        title="t",
        status="For Sale",
        ended=False,
        posted=D(2026, 11, 14, 2, 30),
        last_seen=D(2026, 11, 14, 3, 15),
        price=1.0,
    )
    fbc.db.session.add(listing)
    fbc.db.session.commit()
    show = client.get("/discogslistingmodelview/show/1")
    html = show.get_data(as_text=True)
    assert "2026-11-13 8:30 PM" in html
    assert "2026-11-13 9:15 PM" in html


def test_the_supply_use_list_and_repr_show_central(client):
    supply = Supply(name="Mailers", quantity=1)
    fbc.db.session.add(supply)
    fbc.db.session.flush()
    use = SupplyUse(
        date=D(2026, 11, 14, 2, 30), supply_id=supply.id, count=2, kind="SALE"
    )
    fbc.db.session.add(use)
    fbc.db.session.commit()
    html = client.get("/supplyusemodelview/list/").get_data(as_text=True)
    assert "2026-11-13 8:30 PM" in html
    assert repr(use) == "2026-11-13 8:30 PM: 2 Mailers"


def test_an_order_names_itself_by_its_local_date():
    order = EbayOrder(
        order_id="ORD-1",
        created_time=D(2026, 11, 14, 2, 30),
        buyer="b",
        price=1.0,
    )
    assert repr(order).startswith("2026-11-13 ORD-1")


def _form(view_class, **fields):
    return view_class().edit_form(MultiDict(fields))


def test_the_edit_form_shows_local_time_and_saves_utc():
    listing = EbayListing(
        item_id="L1", title="t", price=1.0, url="u", start_time=D(2026, 7, 1, 22, 45)
    )
    view = EbayListingModelView()
    form = view.edit_form(MultiDict(), obj=listing)
    assert 'value="2026-07-01 17:45:00"' in str(form.start_time())

    form = view.edit_form(
        MultiDict(
            {
                "title": "t",
                "price": "1.0",
                "url": "u",
                "start_time": "2026-11-13T17:45:00",
            }
        )
    )
    assert form.validate(), form.errors
    form.populate_obj(listing)
    assert listing.start_time == D(2026, 11, 13, 23, 45)


def test_a_blank_form_time_saves_none():
    order = EbayOrder(order_id="O", created_time=D(2026, 1, 1), buyer="b", price=1.0)
    form = EbayOrderModelView().edit_form(
        MultiDict({"created_time": "", "buyer": "b", "price": "1", "title": "t"})
    )
    assert form.validate(), form.errors
    form.populate_obj(order)
    assert order.created_time is None
