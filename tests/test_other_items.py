"""
Other Items (#62): eBay listings outside Music > Vinyl Records, kept out of
every record figure, with their own sales line, Ship This and menu entry.
"""

import datetime

import pytest

import fbc
from fbc import db
from fbc.core import devmode
from fbc.ebay import api, sorting
from fbc.ebay.listings import (
    add_ebay_listing,
    find_relisted_listing,
    relist_ebay_listing,
)
from fbc.ebay.sorting import VINYL_RECORDS
from fbc.models.ebay import EbayListing, EbayOrder, EbayOrderItem
from fbc.models.other import OtherItem
from fbc.models.sales import SalesReceipt, earnings_totals
from fbc.models.supplies import Supply
from fbc.sales import index, ship, tasks
from fbc.sync import jobs
from test_ebay_saving import _ebay_listing_payload, _make_unit
from test_shipping import LABEL, SALE, _transactions

OTHER = "99999"


@pytest.fixture
def finances(monkeypatch):
    """Answer Finances API calls with a sale and a label."""
    import requests
    from types import SimpleNamespace

    from fbc.ebay import oauth

    monkeypatch.setattr(oauth, "oauth_token", lambda scope=oauth.MEDIA_SCOPE: "token")
    state = SimpleNamespace(reply=_transactions(SALE, LABEL))
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: state.reply)
    return state


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _other(item_id="L9", title="Transformers figure"):
    add_ebay_listing(
        _ebay_listing_payload(item_id=item_id, title=title), category=OTHER
    )
    return db.session.query(OtherItem).filter_by(ebay_listing_id=item_id).one()


def _order(item_id="L9", order_id="12-34567-89012", price=25.0):
    order = EbayOrder(
        order_id=order_id,
        created_time=datetime.datetime(2026, 10, 28, 15, 30),
        buyer="buyer1",
        price=price,
        title="Transformers figure",
        ebay_listing_id=item_id,
        items=[EbayOrderItem(ebay_listing_id=item_id, quantity=1, price=price)],
    )
    db.session.add(order)
    db.session.commit()
    return order


def test_a_listing_outside_vinyl_records_becomes_an_other_item(app):
    item = _other()

    assert item.name == "Transformers figure"
    assert item.cost is None
    assert item.sold is False
    assert db.session.get(EbayListing, "L9").category_id == OTHER


def test_a_vinyl_listing_stays_a_record(app):
    add_ebay_listing(_ebay_listing_payload(), category=VINYL_RECORDS)

    assert db.session.query(OtherItem).count() == 0
    assert db.session.get(EbayListing, "123456").category_id == VINYL_RECORDS


def test_a_listing_with_no_category_stays_a_record(app):
    add_ebay_listing(_ebay_listing_payload())

    assert db.session.query(OtherItem).count() == 0


def test_a_listing_with_a_unit_is_never_made_an_other_item(app):
    add_ebay_listing(_ebay_listing_payload())
    _make_unit(ebay_listing_id="123456")
    listing = db.session.get(EbayListing, "123456")

    assert sorting.sort_listing(listing, "1234") is None

    assert db.session.query(OtherItem).count() == 0


def test_sorting_twice_makes_one_other_item(app):
    _other()
    listing = db.session.get(EbayListing, "L9")

    sorting.sort_listing(listing, OTHER)
    db.session.commit()

    assert db.session.query(OtherItem).count() == 1


def test_category_comes_from_the_item_or_from_get_item(app, monkeypatch):
    asked = []
    monkeypatch.setattr(
        api, "item_category", lambda item_id: (asked.append(item_id) or "55", [])
    )

    assert (
        sorting.item_category({"ItemID": "1", "PrimaryCategory": {"CategoryID": 7}})
        == "7"
    )
    assert asked == []
    assert sorting.item_category({"ItemID": "2"}) == "55"
    assert asked == ["2"]


def test_a_category_that_cant_be_read_is_none(app, monkeypatch):
    monkeypatch.setattr(api, "item_category", lambda item_id: (None, ["down"]))

    assert sorting.item_category({"ItemID": "2"}) is None


def test_item_category_reads_get_items_reply(app, monkeypatch):
    reply = {"Item": {"PrimaryCategory": {"CategoryID": "176985"}}}
    monkeypatch.setattr(api, "_call", lambda verb, data: (reply, {"errors": []}))

    assert api.item_category("1") == ("176985", [])
    monkeypatch.setattr(api, "_call", lambda verb, data: (None, {"errors": ["no"]}))
    assert api.item_category("1") == (None, ["no"])


def test_the_listings_sync_sorts_new_listings_by_category(app, monkeypatch):
    items = [
        dict(
            _ebay_listing_payload("R1", title="A record"),
            PrimaryCategory={"CategoryID": "176985"},
        ),
        dict(
            _ebay_listing_payload("O1", title="Stereo"),
            PrimaryCategory={"CategoryID": "14990"},
        ),
    ]
    monkeypatch.setattr(jobs, "fetch_active_items", lambda: items)

    jobs.sync_ebay_listings()

    assert [i.name for i in db.session.query(OtherItem)] == ["Stereo"]
    assert db.session.get(EbayListing, "R1").category_id == VINYL_RECORDS


def test_the_listings_sync_sorts_an_old_listing_whose_item_carries_a_category(
    app, monkeypatch
):
    add_ebay_listing(_ebay_listing_payload("O1", title="Stereo"))
    item = dict(
        _ebay_listing_payload("O1", title="Stereo"),
        PrimaryCategory={"CategoryID": "14990"},
    )
    monkeypatch.setattr(jobs, "fetch_active_items", lambda: [item])

    jobs.sync_ebay_listings()

    assert db.session.query(OtherItem).count() == 1


def test_a_relisted_other_item_stays_one_item(app):
    item = _other(item_id="OLD", title="Stereo")
    new = _ebay_listing_payload("NEW", title="Stereo")
    remote = {"NEW"}

    old = find_relisted_listing(new, remote)
    assert old.item_id == "OLD"
    relist_ebay_listing(old, new, OTHER)

    items = db.session.query(OtherItem).all()
    assert [(i.id, i.ebay_listing_id) for i in items] == [(item.id, "NEW")]
    assert db.session.get(EbayListing, "NEW").category_id == OTHER


def test_the_record_lists_leave_out_other_items(client):
    _other()
    add_ebay_listing(_ebay_listing_payload("R1", title="A Fine Record"), VINYL_RECORDS)
    _order()
    _order(item_id="R1", order_id="R-ORDER")
    db.session.add(EbayListing(item_id="R1b", title="x", price=1, url="u"))
    db.session.add(
        EbayOrder(
            order_id="NOLISTING",
            buyer="b",
            price=1,
            title="NoListing",
            created_time=datetime.datetime(2026, 1, 1),
        )
    )
    db.session.commit()

    listings = client.get("/ebaylistingmodelview/list/").get_data(as_text=True)
    orders = client.get("/ebayordermodelview/list/").get_data(as_text=True)
    to_ship = client.get("/orderstoshipview/list/").get_data(as_text=True)
    others = client.get("/otheritemmodelview/list/").get_data(as_text=True)

    assert "A Fine Record" in listings and "Transformers figure" not in listings
    assert "R-ORDER" in orders and "12-34567-89012" not in orders
    assert "NoListing" in orders
    # Still to ship, so still on the task list.
    assert "12-34567-89012" in to_ship
    assert "Transformers figure" in others


def test_the_other_items_menu_entry_is_under_sales(client):
    page = client.get("/otheritemmodelview/list/").get_data(as_text=True)

    assert "Other Items" in page


def test_an_other_item_can_be_added_and_given_a_cost_by_hand(client):
    client.post("/otheritemmodelview/add", data={"name": "Estate lamp", "cost": ""})
    item = db.session.query(OtherItem).one()
    assert item.cost is None

    client.post(
        "/otheritemmodelview/edit/" + str(item.id),
        data={"name": "Estate lamp", "cost": "4.50"},
    )

    assert db.session.get(OtherItem, item.id).cost == 4.5


def test_other_items_have_a_link_to_ship_this_then_their_receipt(client):
    item = _other()
    order = _order()

    assert "/ebayordermodelview/ship/" + order.order_id in str(item.links())

    receipt = SalesReceipt(
        date=datetime.date(2026, 10, 28),
        sold_price=25,
        net_sold=20,
        ebay_orders=[order],
    )
    db.session.add(receipt)
    db.session.flush()
    item.sales_receipt_id = receipt.id
    db.session.commit()

    assert "/salesreceiptmodelview/show/" in str(item.links())


def _shipping_supplies():
    label = Supply(
        name="Label Roll", quantity=5, used_for="shipping", per_units=0, pack_size=1
    )
    tape = Supply(name="Packing Tape", quantity=3, used_for="shipping", per_units=0)
    db.session.add_all([label, tape])
    db.session.commit()
    return label, tape


def test_ship_this_for_an_other_item_records_the_receipt_and_the_supplies(
    client, finances
):
    item = _other()
    order = _order()
    label, tape = _shipping_supplies()

    page = client.get("/ebayordermodelview/ship/" + order.order_id).get_data(
        as_text=True
    )
    assert "Label Roll" in page and "Packing Tape" in page
    assert 'value="0"' in page

    client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={
            "net_sold": "16.50",
            "supply-" + str(label.id): "1",
            "supply-" + str(tape.id): "2",
        },
    )

    receipt = db.session.query(SalesReceipt).one()
    assert receipt.net_sold == 16.5
    assert receipt.ebay_orders == [order]
    item = db.session.get(OtherItem, item.id)
    assert item.sold is True and item.sales_receipt_id == receipt.id
    assert (label.quantity, tape.quantity) == (4, 1)


def test_ship_this_for_an_other_item_needs_a_net_sold_and_enough_stock(
    client, finances
):
    _other()
    order = _order()
    label, _ = _shipping_supplies()

    client.post("/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "lots"})
    client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "5", "supply-" + str(label.id): "50"},
    )

    assert db.session.query(SalesReceipt).count() == 0
    assert db.session.query(OtherItem).one().sold is False


def test_ship_this_wont_mix_records_and_other_items(client, finances):
    _other()
    add_ebay_listing(_ebay_listing_payload("R1"), VINYL_RECORDS)
    _make_unit(ebay_listing_id="R1")
    order = _order()
    order.items.append(EbayOrderItem(ebay_listing_id="R1", quantity=1, price=5.0))
    db.session.commit()

    items, problems = ship.other_items(order)
    assert len(items) == 1 and problems

    client.post("/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "5"})
    assert db.session.query(SalesReceipt).count() == 0


def test_an_order_of_records_has_no_other_items(app):
    add_ebay_listing(_ebay_listing_payload("R1"), VINYL_RECORDS)
    order = _order(item_id="R1")

    assert ship.other_items(order) == ([], [])


def test_other_items_sales_are_a_line_of_their_own_and_never_record_figures(app):
    item = _other()
    order = _order(price=25.0)
    receipt = SalesReceipt(
        date=datetime.date(2026, 10, 28),
        sold_price=25,
        net_sold=20,
        ebay_orders=[order],
    )
    db.session.add(receipt)
    db.session.flush()
    item.sold = True
    item.sales_receipt_id = receipt.id
    db.session.commit()

    totals = index.home_totals()

    assert totals["other_sales"] == {
        "sold": 1,
        "gross": 25.0,
        "net": 20.0,
        "supplies": 0.0,
        "costed": 0,
        "profit": None,
    }
    assert sum(m["receipts"] for m in totals["marketplace_sales"]) == 0
    assert earnings_totals()["receipts"] == 0
    assert earnings_totals(other_items=True)["receipts"] == 1
    assert totals["totals"]["gross"] == totals["totals"]["net"] == 0
    assert totals["totals"]["profit"] == 0


def test_profit_counts_only_items_that_have_a_cost(app):
    costed = _other("L1", "Costed")
    free = _other("L2", "No cost")
    for item, price, net in ((costed, 30, 24), (free, 10, 8)):
        receipt = SalesReceipt(
            date=datetime.date(2026, 10, 28), sold_price=price, net_sold=net
        )
        db.session.add(receipt)
        db.session.flush()
        item.sold, item.sales_receipt_id = True, receipt.id
    costed.cost = 10.0
    db.session.commit()

    from fbc.sales import other

    assert other.sales() == {
        "sold": 2,
        "gross": 40.0,
        "net": 32.0,
        "supplies": 0.0,
        "costed": 1,
        "profit": 14.0,
    }
    free.cost = 2.0
    db.session.commit()
    assert other.sales()["profit"] == 20.0


def test_a_receipt_of_two_items_has_profit_only_when_both_have_a_cost(app):
    a = _other("L1", "A")
    b = _other("L2", "B")
    receipt = SalesReceipt(date=datetime.date(2026, 10, 28), sold_price=30, net_sold=24)
    db.session.add(receipt)
    db.session.flush()
    for item in (a, b):
        item.sold, item.sales_receipt_id = True, receipt.id
    a.cost = 5.0
    db.session.commit()
    from fbc.sales import other

    assert other.sales()["profit"] is None
    assert other.sales()["gross"] == 30.0
    b.cost = 5.0
    db.session.commit()
    assert other.sales()["profit"] == 14.0


def test_the_home_page_shows_the_other_items_line(client):
    item = _other()
    receipt = SalesReceipt(date=datetime.date(2026, 10, 28), sold_price=25, net_sold=20)
    db.session.add(receipt)
    db.session.flush()
    item.sold, item.sales_receipt_id = True, receipt.id
    db.session.commit()

    page = client.get("/").get_data(as_text=True)

    assert "Other items:" in page and "$25.00" in page


def test_the_home_page_has_no_other_items_line_without_sales(client):
    _other()

    assert "Other items:" not in client.get("/").get_data(as_text=True)


def test_other_item_orders_stay_in_orders_to_ship(app):
    _other()
    order = _order()

    assert tasks.counts()["orders"] == 1
    assert order.order_id in {
        r[0] for r in db.session.execute(tasks.ids_orders_to_ship())
    }


def test_sorting_existing_listings_is_safe_to_repeat(app, monkeypatch):
    add_ebay_listing(_ebay_listing_payload("O1", title="Stereo"))
    add_ebay_listing(_ebay_listing_payload("R1", title="A record"))
    add_ebay_listing(_ebay_listing_payload("U1", title="Mislabelled record"))
    add_ebay_listing(_ebay_listing_payload("X1", title="Unreadable"))
    _make_unit(ebay_listing_id="U1")
    order = _order("O1")
    receipt = SalesReceipt(
        date=datetime.date(2026, 10, 28),
        sold_price=25,
        net_sold=20,
        ebay_orders=[order],
    )
    db.session.add(receipt)
    db.session.commit()
    answers = {
        "O1": ("14990", []),
        "R1": (VINYL_RECORDS, []),
        "U1": ("14990", []),
        "X1": (None, ["gone"]),
    }
    monkeypatch.setattr(api, "item_category", lambda item_id: answers[item_id])
    said = []

    dry = sorting.sort_existing(echo=said.append)
    assert dry == {"records": 1, "other": 1, "kept": 1, "unknown": 1, "claimed": 0}
    assert db.session.query(OtherItem).count() == 0

    applied = sorting.sort_existing(apply=True, echo=said.append)
    assert applied == {"records": 1, "other": 1, "kept": 1, "unknown": 1, "claimed": 1}
    item = db.session.query(OtherItem).one()
    assert (item.name, item.cost, item.sold, item.sales_receipt_id) == (
        "Stereo",
        None,
        True,
        receipt.id,
    )
    assert db.session.get(EbayListing, "X1").category_id is None

    again = sorting.sort_existing(apply=True, echo=said.append)
    assert again["other"] == 0 and db.session.query(OtherItem).count() == 1


def test_a_receipt_with_units_is_not_claimed_by_an_other_item(app):
    item = _other()
    order = _order()
    unit = _make_unit(sold=True)
    receipt = SalesReceipt(
        date=datetime.date(2026, 10, 28),
        sold_price=25,
        net_sold=20,
        ebay_orders=[order],
    )
    db.session.add(receipt)
    db.session.flush()
    unit.sales_receipt_id = receipt.id
    db.session.commit()

    assert sorting.claim_receipts() == []
    assert db.session.get(OtherItem, item.id).sold is False


def test_sorting_is_off_in_dev_mode(app, monkeypatch):
    add_ebay_listing(_ebay_listing_payload("O1"))
    monkeypatch.setattr(devmode, "on", lambda: True)
    monkeypatch.setattr(api, "item_category", lambda i: pytest.fail("reached eBay"))
    said = []

    counts = sorting.sort_existing(apply=True, echo=said.append)

    assert counts["other"] == 0 and "Dev Mode" in said[0]
    assert sorting.item_category({"ItemID": "1"}) is None


def test_the_migration_adds_the_table_and_column():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "d4a8c2e7b915_add_other_item.py"
    )
    spec = importlib.util.spec_from_file_location("other_item_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sa.Table(
        "ebay_listing", meta, sa.Column("item_id", sa.String(20), primary_key=True)
    )
    sa.Table("sales_receipt", meta, sa.Column("id", sa.Integer, primary_key=True))
    with engine.begin() as conn:
        meta.create_all(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        assert "category_id" in {
            c["name"] for c in inspector.get_columns("ebay_listing")
        }
        assert {c["name"] for c in inspector.get_columns("other_item")} == {
            "id",
            "name",
            "cost",
            "ebay_listing_id",
            "sales_receipt_id",
            "sold",
        }
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "other_item" not in sa.inspect(conn).get_table_names()


def test_supplies_used_on_other_items_leave_the_record_supply_cost(app):
    from fbc.models.supplies import SHIPPING, PurchaseOrder

    item = _other()
    label = Supply(name="Label", quantity=10, used_for="shipping", cost_per_use=0.5)
    tape = Supply(name="Tape", quantity=10, used_for="shipping")
    db.session.add_all(
        [label, tape, PurchaseOrder(date=datetime.date(2026, 1, 1), price=20.0)]
    )
    receipt = SalesReceipt(date=datetime.date(2026, 10, 28), sold_price=25, net_sold=20)
    db.session.add(receipt)
    db.session.flush()
    label.use(3, SHIPPING, receipt=receipt)
    tape.use(1, SHIPPING, receipt=receipt)
    item.sold, item.sales_receipt_id, item.cost = True, receipt.id, 5.0
    db.session.commit()

    totals = index.home_totals()

    # Only the supply with a Cost Per Use is counted: 3 x $0.50.
    assert totals["other_sales"]["supplies"] == 1.5
    assert totals["other_sales"]["profit"] == 13.5
    assert totals["supply_total"] == 18.5


def test_ship_this_offers_to_make_an_other_item_of_an_order_with_no_units(
    client, finances
):
    add_ebay_listing(_ebay_listing_payload("L9", title="Old stereo"))
    order = _order()
    url = "/ebayordermodelview/ship/" + order.order_id

    page = client.get(url).get_data(as_text=True)
    assert "Make Other Item and ship" in page
    assert db.session.query(OtherItem).count() == 0

    response = client.post(url, data={"action": "make_other"})
    assert response.status_code == 302
    item = db.session.query(OtherItem).one()
    assert (item.ebay_listing_id, item.cost, item.sold) == ("L9", None, False)

    client.post(url, data={"net_sold": "16.50"})
    assert db.session.get(OtherItem, item.id).sold is True
    assert db.session.query(SalesReceipt).one().net_sold == 16.5


def test_ship_this_doesnt_offer_other_items_for_a_listing_with_a_unit(client, finances):
    add_ebay_listing(_ebay_listing_payload("L9"))
    _make_unit(ebay_listing_id="L9")
    order = _order()

    page = client.get(
        "/ebayordermodelview/ship/" + order.order_id, follow_redirects=True
    ).get_data(as_text=True)

    assert "Make Other Item and ship" not in page
    assert ship.other_item_candidates(order) == []


def test_an_old_order_with_no_listing_gets_one_and_an_other_item(client, finances):
    order = _order(item_id="GONE")
    order.ebay_listing_id = None
    db.session.commit()
    url = "/ebayordermodelview/ship/" + order.order_id

    assert ship.other_item_candidates(order) == [("GONE", "Transformers figure")]
    client.post(url, data={"action": "make_other"})

    listing = db.session.get(EbayListing, "GONE")
    assert (listing.ended, listing.archived, listing.price) == (True, True, 25.0)
    assert listing.end_time == order.created_time
    item = db.session.query(OtherItem).one()
    assert item.ebay_listing_id == "GONE"

    client.post(url, data={"net_sold": "16.50"})
    assert db.session.get(OtherItem, item.id).sold is True
    assert index.home_totals()["other_sales"]["sold"] == 1
