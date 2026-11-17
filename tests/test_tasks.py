"""The Tasks lists and the home page's To do banner."""

import datetime

import pytest

import fbc
from fbc import db
from fbc.sales import tasks
from fbc.models.discogs import DiscogsInstance, DiscogsListing, DiscogsRelease
from fbc.core import localtime
from fbc.models.ebay import EbayDraft, EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt


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


def _order(order_id, **fields):
    order = EbayOrder(
        order_id=order_id,
        created_time=datetime.datetime(2024, 1, 1),
        buyer="b",
        price=10.0,
        title="T " + order_id,
        **fields,
    )
    db.session.add(order)
    db.session.commit()
    return order


def _unit(name="Unit", **fields):
    fields.setdefault("sold", False)
    fields.setdefault("retail_price", 10.0)
    unit = Unit(name=name, unit_type='12" LP', pressing="", grading="", **fields)
    db.session.add(unit)
    db.session.commit()
    return unit


def _release(release_id, copies):
    release = DiscogsRelease(
        release_id=release_id,
        title="Rel %d" % release_id,
        year=2000,
        master_id=0,
        master_year=2000,
        released="2000",
        formats="[]",
        notes="[]",
        date_added=datetime.date(2024, 1, 1),
        discogs_instances=[
            DiscogsInstance(instance_id=release_id * 100 + i) for i in range(copies)
        ],
    )
    db.session.add(release)
    db.session.commit()
    return release


def _draft(unit, **fields):
    draft = EbayDraft(unit_id=unit.id, title="Draft", **fields)
    db.session.add(draft)
    db.session.commit()
    return draft


def _on_discogs(unit, status="Draft", listing_id=None, **fields):
    listing = DiscogsListing(
        listing_id=listing_id or 1000 + unit.id,
        unit_id=unit.id,
        status=status,
        **fields,
    )
    db.session.add(listing)
    db.session.commit()
    return listing


def _in_collection(name="Collectable", **fields):
    """A Unit that List on Discogs can list: a release and a collection copy."""
    release = _release(len(name) * 7 + db.session.query(Unit).count(), copies=1)
    return _unit(
        name,
        discogs_release_id=release.release_id,
        discogs_instance_id=release.discogs_instances[0].instance_id,
        **fields,
    )


def _ids(select):
    return sorted(db.session.scalars(select))


def test_an_order_is_a_task_until_it_has_a_sales_receipt():
    _order("A")
    shipped = _order("B")
    _order("C", archived=True)
    receipt = SalesReceipt(date=datetime.date(2024, 1, 2), sold_price=10, net_sold=9)
    receipt.ebay_orders = [shipped]
    db.session.add(receipt)
    db.session.commit()

    assert _ids(tasks.ids_orders_to_ship()) == ["A"]


def test_a_release_is_a_task_when_it_has_more_copies_than_unsold_units():
    new = _release(1, copies=1)
    second = _release(2, copies=2)
    done = _release(3, copies=1)
    sold_before = _release(4, copies=1)
    _unit("a", discogs_release_id=second.release_id)
    _unit("b", discogs_release_id=done.release_id)
    # The first copy sold (and left the collection); the new one has no Unit.
    _unit("c", discogs_release_id=sold_before.release_id, sold=True)

    assert _ids(tasks.ids_releases_needing_units()) == [
        new.release_id,
        second.release_id,
        sold_before.release_id,
    ]

    _unit("d", discogs_release_id=new.release_id)
    assert _ids(tasks.ids_releases_needing_units()) == [
        second.release_id,
        sold_before.release_id,
    ]


def test_a_unit_needs_a_draft_unless_cheap_listed_sold_or_drafted():
    needs = _unit("needs")
    _unit("cheap", retail_price=1.0)
    _unit("sold", sold=True)
    _unit("listed", ebay_listing_id=None)
    drafted = _unit("drafted")
    _draft(drafted)

    assert _ids(tasks.ids_units_needing_drafts()) == sorted([needs.id, 4])
    assert drafted.id not in _ids(tasks.ids_units_needing_drafts())


def test_stock_waiting_counts_each_unit_once():
    _unit("bare")
    _unit("cheap", retail_price=1.0)
    _unit("sold", sold=True)
    drafted = _unit("drafted")
    _draft(drafted)
    _draft(drafted)  # two Drafts, still one Unit
    _draft(_unit("done"), status="published")
    published = _unit("p")
    _draft(published, status="published", ebay_listing_id=None)

    assert tasks.stock_waiting() == {"units": 1, "drafts": 1, "total": 2}
    # Only "bare" has no Draft and "drafted" has an unpublished one. "done" and
    # "p" have published Drafts, so they aren't waiting.


def test_a_draft_is_a_task_until_it_is_published():
    waiting = _draft(_unit("a"))
    _draft(_unit("b"), status="published", ebay_listing_id=None)

    assert _ids(tasks.ids_drafts_to_list()) == [waiting.id]


def test_a_unit_needs_a_discogs_draft_when_it_can_be_listed_and_isnt():
    needs = _in_collection("needs")
    _in_collection("cheap", retail_price=1.0)
    _in_collection("sold", sold=True)
    _in_collection("archived", archived=True)
    _unit("not in the collection")
    drafted = _in_collection("drafted")
    _on_discogs(drafted, "Draft")
    on_sale = _in_collection("on sale")
    _on_discogs(on_sale, "For Sale")

    assert _ids(tasks.ids_units_needing_discogs_drafts()) == [needs.id]


def test_a_unit_whose_discogs_listing_is_over_needs_a_new_draft():
    sold = _in_collection("sold on discogs")
    _on_discogs(sold, "Sold", ended=True)
    gone = _in_collection("deleted")
    _on_discogs(gone, "For Sale", ended=True)
    expired = _in_collection("expired")
    _on_discogs(expired, "Expired")

    assert _ids(tasks.ids_units_needing_discogs_drafts()) == sorted(
        [sold.id, gone.id, expired.id]
    )


def test_a_listing_on_discogs_that_has_no_unit_doesnt_hide_the_others():
    needs = _in_collection("needs")
    db.session.add(DiscogsListing(listing_id=5, unit_id=None, status="Draft"))
    db.session.commit()

    assert _ids(tasks.ids_units_needing_discogs_drafts()) == [needs.id]


def test_a_unit_on_ebay_still_needs_a_discogs_draft():
    on_ebay = _in_collection("on ebay")
    _draft(on_ebay)

    assert _ids(tasks.ids_units_needing_discogs_drafts()) == [on_ebay.id]


def test_a_discogs_listing_is_a_task_while_it_is_a_draft():
    waiting = _on_discogs(_unit("a"), "Draft")
    _on_discogs(_unit("b"), "For Sale")
    _on_discogs(_unit("c"), "Draft", ended=True)

    assert _ids(tasks.ids_discogs_drafts_to_list()) == [waiting.listing_id]


def test_counts_count_each_task():
    _order("A")
    _release(1, copies=1)
    _draft(_unit("a"))

    assert tasks.counts() == {
        "orders": 1,
        "discogs_orders": 0,
        "failed_endings": 0,
        "releases": 1,
        "units": 0,
        "drafts": 1,
        "parked": 0,
        "discogs_units": 0,
        "discogs_drafts": 0,
    }


def test_the_home_page_banner_links_to_the_tasks(client):
    _order("A")
    _release(1, copies=1)
    _unit("x")
    _draft(_unit("y"))

    html = client.get("/").get_data(as_text=True)

    assert "1 eBay order to ship" in " ".join(html.split())
    assert "/orderstoshipview/list/" in html
    assert "/releasesneedingunitsview/list/" in html
    assert "/unitsneedingdraftsview/list/" in html
    assert "_flt_3_status=draft" in html


def test_the_home_page_banner_counts_the_discogs_tasks(client):
    _on_discogs(_unit("drafted"), "Draft")
    _in_collection("needs")

    html = client.get("/").get_data(as_text=True)

    text = " ".join(html.split())
    assert "1 Unit that need a Discogs Draft" in text
    assert "1 Discogs Draft to list" in text
    assert "/unitsneedingdiscogsdraftsview/list/" in html
    assert "_flt_3_status=Draft" in html and "_flt_1_ended=y" in html


def test_the_tasks_menu_names_the_ebay_and_discogs_lists(client):
    html = client.get("/").get_data(as_text=True)

    for name in (
        "Units to Draft (eBay)",
        "Drafts to List (eBay)",
        "Units to Draft (Discogs)",
        "Drafts to List (Discogs)",
    ):
        assert name in html
    assert ">Units to Draft<" not in html and ">Drafts to List<" not in html


def test_the_home_page_has_no_banner_when_there_is_nothing_to_do(client):
    html = client.get("/").get_data(as_text=True)

    assert "To do:" not in html


def test_each_task_list_shows_only_its_tasks(client):
    _order("TODO-ORDER")
    shipped = _order("DONE-ORDER")
    receipt = SalesReceipt(date=datetime.date(2024, 1, 2), sold_price=10, net_sold=9)
    receipt.ebay_orders = [shipped]
    db.session.add(receipt)
    _release(7, copies=1)
    _release(8, copies=1)
    _unit("done", discogs_release_id=8)
    _unit("Needs A Draft")
    _draft(_unit("Has A Draft"))
    db.session.commit()

    orders = client.get("/orderstoshipview/list/").get_data(as_text=True)
    assert "TODO-ORDER" in orders and "DONE-ORDER" not in orders
    releases = client.get("/releasesneedingunitsview/list/").get_data(as_text=True)
    assert "Rel 7" in releases and "Rel 8" not in releases
    units = client.get("/unitsneedingdraftsview/list/").get_data(as_text=True)
    assert "Needs A Draft" in units and "Has A Draft" not in units
    drafts = client.get("/ebaydraftmodelview/list/?_flt_3_status=draft").get_data(
        as_text=True
    )
    assert "Draft" in drafts


def test_the_discogs_task_lists_show_only_their_tasks(client):
    needs = _in_collection("Needs A Discogs Draft")
    _on_discogs(_in_collection("Has A Discogs Draft"), "Draft", title="DRAFT-ROW")
    _on_discogs(_in_collection("Is For Sale"), "For Sale", title="SALE-ROW")
    _on_discogs(_in_collection("Is Over"), "Draft", title="ENDED-ROW", ended=True)
    assert needs

    units = client.get("/unitsneedingdiscogsdraftsview/list/").get_data(as_text=True)
    assert "Needs A Discogs Draft" in units
    assert "Has A Discogs Draft" not in units and "Is For Sale" not in units
    # The link on the Tasks menu is the Discogs Listings list, filtered.
    from fbc.views.discogs import DISCOGS_DRAFTS_TO_LIST_FILTERS
    from urllib.parse import urlencode

    link = "/discogslistingmodelview/list/?" + urlencode(DISCOGS_DRAFTS_TO_LIST_FILTERS)
    drafts = client.get(link).get_data(as_text=True)
    assert "DRAFT-ROW" in drafts
    assert "SALE-ROW" not in drafts and "ENDED-ROW" not in drafts


def _parked(item_id, unit=None, days_ago=1, title=None, **fields):
    """An ended, parked listing, linked to `unit` unless it's False."""
    fields.setdefault("ended", True)
    fields.setdefault(
        "parked_at", localtime.utcnow() - datetime.timedelta(days=days_ago)
    )
    listing = EbayListing(
        item_id=item_id,
        title=title or "Parked " + item_id,
        price=12.5,
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


def test_a_parked_listing_is_a_task_while_its_unit_is_unsold_and_unlisted():
    _parked("1")
    _parked("2", unit=_unit("sold", sold=True))
    _parked("3", unit=False)
    _parked("4", ended=False)
    # Ended in the ordinary way, not parked.
    _parked("5", parked_at=None)
    # Re-listed: the Unit moved to a new, active listing.
    old = _parked("6")
    db.session.add(EbayListing(item_id="7", title="New", price=1.0, url="u"))
    db.session.query(Unit).filter_by(
        ebay_listing_id=old.item_id
    ).one().ebay_listing_id = "7"
    db.session.commit()

    assert _ids(tasks.ids_parked_listings()) == ["1"]
    assert tasks.counts()["parked"] == 1


def test_the_parked_list_is_longest_out_first_with_the_marker_and_dates(client):
    _parked("100", days_ago=2, title="Newest Park")
    _parked("200", days_ago=25, title="Oldest Park")
    _parked("300", days_ago=10, title="Middle Park")

    html = client.get("/parkedlistingsview/list/").get_data(as_text=True)

    assert (
        html.index("Oldest Park")
        < html.index("Middle Park")
        < html.index("Newest Park")
    )
    assert "25 days" in html and "2 days" in html
    assert "Unit 200" in html and "$12.50" in html
    # 25 days out of 30 is 5 days from the re-list by date: soon.
    row = html[html.index("Oldest Park") : html.index("Middle Park")]
    assert "re-list soon" in row
    assert "re-list soon" not in html[html.index("Middle Park") :]
    # Reading only: no re-list button yet.
    assert "Re-list</" not in html and "/relist" not in html


def test_the_re_list_window_is_a_setting_with_a_default_of_30_days(client):
    from fbc.core import relist

    ended = datetime.datetime(2026, 1, 1)
    assert relist.relist_by(ended) == datetime.datetime(2026, 1, 31)
    old = fbc.app.config.get("EBAY_RELIST_DAYS")
    try:
        fbc.app.config["EBAY_RELIST_DAYS"] = 90
        assert relist.relist_by(ended) == datetime.datetime(2026, 4, 1)
        for bad in (0, -5, "30", None, True):
            fbc.app.config["EBAY_RELIST_DAYS"] = bad
            assert relist.window_days() == 30
    finally:
        fbc.app.config.pop("EBAY_RELIST_DAYS", None)
        if old is not None:
            fbc.app.config["EBAY_RELIST_DAYS"] = old


def test_re_list_soon_is_within_a_week_of_the_re_list_by_date_or_past_it():
    from fbc.core import relist

    now = datetime.datetime(2026, 6, 1)
    day = datetime.timedelta(days=1)
    assert not relist.is_soon(now - 22 * day, now)
    assert relist.is_soon(now - 23 * day, now)
    assert relist.is_soon(now - 40 * day, now)
    assert not relist.is_soon(None, now)
    assert relist.days_out(now - 3 * day, now) == 3


def test_the_home_page_banner_counts_parked_listings(client):
    _parked("1")
    _parked("2")

    html = client.get("/").get_data(as_text=True)

    assert "2 parked listings" in " ".join(html.split())
    assert "/parkedlistingsview/list/" in html
    assert "Parked Listings" in html
