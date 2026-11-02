import datetime
import re
from types import SimpleNamespace

import pytest
from discogs_client.exceptions import HTTPError

import fbc
from fbc import db
from fbc import listing
from fbc import shipping
from fbc.discogs import DiscogsAuthError
from fbc.models.discogs import DiscogsInstance, DiscogsRelease
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt, SalesReceiptLine, earnings_totals
from fbc.models.supplies import Supply, SupplyUse
from fbc.util import forget_discogs_instance
from test_listing import FakeHTTPResponse, _carnaval


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


def _order(**unit_kwargs):
    """
    An order for the Carnaval Unit, which is linked to the second of two
    copies of the release in the Discogs collection.
    """
    ebay_listing = EbayListing(
        item_id="L1", title="Carnaval", price=25.0, url="https://ebay.com/itm/L1"
    )
    unit = _carnaval(ebay_listing=ebay_listing, **unit_kwargs)
    release = unit.discogs_release
    release.discogs_instances = [
        DiscogsInstance(instance_id=1001),
        DiscogsInstance(instance_id=1002),
    ]
    unit.discogs_instance_id = 1002
    order = EbayOrder(
        order_id="12-34567-89012",
        created_time=datetime.datetime(2026, 10, 28, 15, 30),
        buyer="buyer1",
        price=25.0,
        title="Carnaval",
        ebay_listing=ebay_listing,
    )
    db.session.add(order)
    db.session.commit()
    return order, unit


def _transactions(*transactions):
    return FakeHTTPResponse(
        200, {"total": len(transactions), "transactions": list(transactions)}
    )


SALE = {
    "transactionType": "SALE",
    "bookingEntry": "CREDIT",
    "amount": {"value": "21.13", "currency": "USD"},
    "orderLineItems": [{"lineItemId": "1"}],
}
LABEL = {
    "transactionType": "SHIPPING_LABEL",
    "bookingEntry": "DEBIT",
    "amount": {"value": "4.63", "currency": "USD"},
}


@pytest.fixture
def finances(monkeypatch):
    """Answer Finances API calls with `finances.reply`, recording each call."""
    import requests

    monkeypatch.setattr(
        listing, "oauth_token", lambda scope=listing.MEDIA_SCOPE: "access-" + scope[-8:]
    )
    state = SimpleNamespace(reply=_transactions(SALE, LABEL), calls=[])

    def fake_get(url, **kwargs):
        state.calls.append((url, kwargs))
        return state.reply

    monkeypatch.setattr(requests, "get", fake_get)
    return state


class FakeDiscogs:
    """A Discogs collection holding `instances` of each release id."""

    def __init__(self, instances, error=None):
        self.instances = instances
        self.error = error
        self.deleted = []

    def identity(self):
        return self

    def fetch(self, key):
        assert key == "resource_url"
        return "https://api.discogs.com/users/me"

    def collection_items(self, release_id):
        if self.error:
            raise self.error
        if release_id not in self.instances:
            raise HTTPError("The requested resource was not found.", 404)
        return self.instances[release_id]

    def _delete(self, url):
        self.deleted.append(url)


@pytest.fixture
def discogs(monkeypatch):
    fake = FakeDiscogs(
        {
            1: [
                SimpleNamespace(id=1, instance_id=1001, folder_id=1),
                SimpleNamespace(id=1, instance_id=1002, folder_id=7),
            ]
        }
    )
    monkeypatch.setattr(shipping, "_discogs", lambda: fake)
    return fake


def test_order_earnings_adds_up_the_sale_and_the_label(app, finances):
    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["net"] == 16.50
    assert [
        (line["kind"], line["label"], line["amount"]) for line in earnings["lines"]
    ] == [
        ("SALE", "Sale, less the taxes eBay collects", 21.13),
        ("SHIPPING_LABEL", "Shipping label", -4.63),
    ]
    assert {line["ebay_order_id"] for line in earnings["lines"]} == {"12-34567-89012"}
    assert earnings["items"] == 1
    assert earnings["errors"] == earnings["warnings"] == []
    url, kwargs = finances.calls[0]
    assert url == shipping.FINANCES_URL
    assert kwargs["params"]["filter"] == "orderId:{12-34567-89012}"
    assert kwargs["headers"]["Authorization"] == "Bearer access-finances"


def test_order_earnings_warns_when_no_label_is_bought(app, finances):
    finances.reply = _transactions(SALE)

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["net"] == 21.13
    assert "no eBay shipping label" in earnings["warnings"][0]


def test_order_earnings_are_unknown_without_a_sale(app, finances):
    finances.reply = _transactions()

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["net"] is None
    assert "doesn't show a sale" in earnings["warnings"][0]


def test_order_earnings_reports_ebay_errors(app, finances):
    finances.reply = FakeHTTPResponse(
        403,
        {"errors": [{"message": "Insufficient permissions to fulfill the request."}]},
    )

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["net"] is None
    assert earnings["errors"] == [
        "Getting the order's earnings: Insufficient permissions to fulfill the request."
    ]


def test_finances_token_is_minted_for_its_own_scope(app, monkeypatch):
    import requests

    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "OAUTH_REFRESH_TOKEN", "refresh"
    )
    monkeypatch.setattr(listing, "_oauth", {})
    scopes = []

    def fake_post(url, **kwargs):
        scopes.append(kwargs["data"]["scope"])
        return FakeHTTPResponse(
            200, {"access_token": "t" + str(len(scopes)), "expires_in": 7200}
        )

    monkeypatch.setattr(requests, "post", fake_post)

    assert listing.oauth_token() == "t1"
    assert listing.oauth_token(listing.FINANCES_SCOPE) == "t2"
    assert listing.oauth_token() == "t1"
    # A refresh token from before Connect eBay asked for finances keeps
    # working for pictures, since each scope is asked for on its own.
    assert scopes == [listing.MEDIA_SCOPE, listing.FINANCES_SCOPE]


def test_ship_this_is_offered_until_there_is_a_sales_receipt(app):
    order, unit = _order()

    assert "Ship&nbsp;This" in str(order.links())

    db.session.add(
        SalesReceipt(
            date=datetime.date(2026, 10, 28),
            ebay_orders=[order],
            sold_price=25.0,
            net_sold=16.5,
        )
    )
    db.session.commit()

    links = str(order.links())
    assert "Ship&nbsp;This" not in links
    receipt = order.sales_receipts[0]
    assert (
        "/salesreceiptmodelview/show/" + str(receipt.id) + '">Sales&nbsp;Receipt'
        in links
    )


def test_ship_this_says_how_to_ship_by_hand_when_the_unit_is_unclear(client):
    order, unit = _order()
    db.session.add(Unit(name="Another copy", ebay_listing_id="L1"))
    db.session.commit()

    page = client.get(
        "/ebayordermodelview/ship/" + order.order_id, follow_redirects=True
    )

    html = page.get_data(as_text=True)
    assert shipping.order_unit(order) is None
    assert "Listing L1 has 2 unsold Units on it, and 1 was bought." in html
    assert "ship it by hand" in html
    assert "/salesreceiptmodelview/add?sold_price=25.0&amp;ebay_orders=" in html
    assert db.session.query(SalesReceipt).count() == 0


def test_discogs_plan_needs_the_unit_linked_to_an_instance(app):
    order, unit = _order()
    unit.discogs_instance_id = None
    db.session.commit()

    instance, problem = shipping.discogs_plan(unit)

    assert instance is None
    assert "isn't linked to a Discogs instance" in problem


def test_discogs_plan_leaves_an_instance_shared_with_another_unit(app):
    order, unit = _order()
    db.session.add(Unit(name="Mislinked", discogs_instance_id=1002))
    db.session.commit()

    instance, problem = shipping.discogs_plan(unit)

    assert instance is None
    assert "linked to other Units" in problem


def test_remove_from_discogs_deletes_only_the_units_instance(app, discogs):
    order, unit = _order()

    message, category = shipping.remove_from_discogs(unit)

    assert category == "success"
    assert discogs.deleted == [
        "https://api.discogs.com/users/me/collection/folders/7/releases/1/instances/1002"
    ]
    db.session.expire_all()
    release = db.session.get(DiscogsRelease, 1)
    assert [i.instance_id for i in release.discogs_instances] == [1001]
    assert db.session.get(DiscogsInstance, 1002) is None
    assert unit.discogs_instance_id is None
    assert unit.discogs_release_id == 1


def test_remove_from_discogs_cleans_up_an_instance_already_gone(app, discogs):
    order, unit = _order()
    discogs.instances = {}

    message, category = shipping.remove_from_discogs(unit)

    assert category == "success"
    assert "already gone" in message
    assert discogs.deleted == []
    assert db.session.get(DiscogsInstance, 1002) is None


def test_remove_from_discogs_keeps_fbc_as_is_when_discogs_fails(app, discogs):
    order, unit = _order()
    discogs.error = HTTPError("Server error", 500)

    message, category = shipping.remove_from_discogs(unit)

    assert category == "danger"
    assert "Remove it by hand" in message
    assert db.session.get(DiscogsInstance, 1002) is not None


def _not_connected():
    raise DiscogsAuthError("Connect fbc to Discogs first (Discogs > Connect Discogs).")


def test_remove_from_discogs_says_so_when_discogs_isnt_connected(app, monkeypatch):
    order, unit = _order()
    monkeypatch.setattr(shipping, "_discogs", _not_connected)

    message, category = shipping.remove_from_discogs(unit)

    assert category == "danger"
    assert "Connect fbc to Discogs first (Discogs > Connect Discogs). Remove" in message
    assert db.session.get(DiscogsInstance, 1002) is not None


def test_forgetting_the_last_instance_removes_the_release(app):
    order, unit = _order()
    release = unit.discogs_release
    release.discogs_instances = [
        i for i in release.discogs_instances if i.instance_id == 1002
    ]
    db.session.commit()

    forget_discogs_instance(db.session.get(DiscogsInstance, 1002))

    db.session.expire_all()
    assert db.session.get(DiscogsRelease, 1) is None
    assert unit.discogs_release_id is None
    assert unit.discogs_instance_id is None


def test_ship_page_shows_the_earnings_and_the_instance(client, finances):
    order, unit = _order()

    page = client.get("/ebayordermodelview/ship/" + order.order_id)
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert 'value="16.50"' in html
    assert "&minus;$4.63" in html
    assert "(instance 1002)" in html


def test_ship_page_blocks_an_order_of_several_items(client, finances):
    order, unit = _order()
    finances.reply = _transactions(dict(SALE, orderLineItems=[{}, {}]), LABEL)

    html = client.get("/ebayordermodelview/ship/" + order.order_id).get_data(
        as_text=True
    )

    assert "eBay says this order has\n2 items, and fbc only knows of 1" in html
    assert "disabled>Ship</button>" in html


def test_shipping_records_the_receipt_marks_sold_and_removes_from_discogs(
    client, discogs
):
    order, unit = _order()

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "remove_discogs-" + str(unit.id): "1"},
    )

    assert page.status_code == 302
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert (
        receipt.date,
        receipt.order_ids(),
        receipt.sold_price,
        receipt.net_sold,
    ) == (
        datetime.date(2026, 10, 28),
        ["12-34567-89012"],
        25.0,
        16.5,
    )
    unit = db.session.get(Unit, unit.id)
    assert unit.sold is True
    assert unit.sales_receipt_id == receipt.id
    assert len(discogs.deleted) == 1

    again = client.get("/ebayordermodelview/ship/" + order.order_id)
    assert again.status_code == 302
    assert db.session.query(SalesReceipt).count() == 1


def test_shipping_without_discogs_connected_still_records_the_receipt(
    client, monkeypatch
):
    order, unit = _order()
    monkeypatch.setattr(shipping, "_discogs", _not_connected)

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "remove_discogs-" + str(unit.id): "1"},
    )

    assert page.status_code == 302
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert db.session.get(Unit, unit.id).sales_receipt_id == receipt.id
    assert db.session.get(DiscogsInstance, 1002) is not None
    listed = client.get(page.headers["Location"]).get_data(as_text=True)
    assert "Connect fbc to Discogs first" in listed


def test_shipping_can_leave_discogs_alone(client, discogs):
    order, unit = _order()

    client.post(
        "/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "16.50"}
    )

    assert discogs.deleted == []
    assert db.session.get(DiscogsInstance, 1002) is not None


def test_shipping_needs_a_net_sold(client, discogs):
    order, unit = _order()

    client.post("/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "lots"})

    assert db.session.query(SalesReceipt).count() == 0


def _shipping_supplies():
    """The shipping supplies, as the migration sets up the usual ones."""
    supplies = {
        "mailers": Supply(
            name="LP Mailers", quantity=10, used_for="shipping", per_units=5
        ),
        "sleeves 12": Supply(
            name='Vinyl Outer Sleeves, 12"',
            quantity=20,
            used_for="shipping",
            record_size='12"',
            per_units=1,
        ),
        "sleeves 7": Supply(
            name='Vinyl Outer Sleeves, 7"',
            quantity=20,
            used_for="shipping",
            record_size='7"',
            per_units=1,
        ),
        "labels": Supply(
            name="Shipping Label Roll",
            quantity=2,
            used_for="shipping",
            per_units=0,
            pack_size=220,
            pack_used=219,
        ),
        "tape": Supply(name="Packing Tape Rolls", quantity=3, used_for="shipping"),
        "inner": Supply(name='Vinyl Inner Sleeves, 12"', quantity=50, used_for="unit"),
    }
    db.session.add_all(supplies.values())
    db.session.commit()
    return supplies


def test_supplies_plan_is_a_normal_shipment_of_the_units(app):
    order, unit = _order()
    _shipping_supplies()

    plan = [(s.name, n) for s, n in shipping.supplies_plan([unit])]

    assert plan == [
        ("LP Mailers", 1),
        ("Packing Tape Rolls", 0),
        ("Shipping Label Roll", 1),
        ('Vinyl Outer Sleeves, 12"', 1),
        ('Vinyl Outer Sleeves, 7"', 0),
    ]


@pytest.mark.parametrize(
    "unit_type, formats, size",
    [
        ('12" LP', None, '12"'),
        ('7" Single', None, '7"'),
        ("LP", None, '12"'),
        ('12" LP', '[{"name": "Vinyl", "descriptions": ["7\\"", "45 RPM"]}]', '7"'),
    ],
)
def test_record_size(unit_type, formats, size):
    release = DiscogsRelease(release_id=9, formats=formats) if formats else None
    assert (
        listing.record_size(Unit(unit_type=unit_type, discogs_release=release)) == size
    )


def test_ship_page_offers_the_shipping_supplies(client, finances):
    order, unit = _order()
    supplies = _shipping_supplies()

    html = client.get("/ebayordermodelview/ship/" + order.order_id).get_data(
        as_text=True
    )

    mailers = supplies["mailers"]
    assert 'name="supply-' + str(mailers.id) + '" value="1"' in html
    assert 'name="supply-' + str(supplies["tape"].id) + '" value="0"' in html
    assert "1 of 220 left on the one in use" in html
    assert "supply-" + str(supplies["inner"].id) not in html


def test_ship_page_says_when_no_supplies_are_used_for_shipping(client, finances):
    order, unit = _order()

    html = client.get("/ebayordermodelview/ship/" + order.order_id).get_data(
        as_text=True
    )

    assert "No supplies are set as Used For shipping" in html


def test_shipping_takes_the_supplies_used_out_of_stock(client, discogs):
    order, unit = _order()
    supplies = _shipping_supplies()
    ids = {k: str(s.id) for k, s in supplies.items()}

    client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={
            "net_sold": "16.50",
            "supply-" + ids["mailers"]: "1",
            "supply-" + ids["sleeves 12"]: "2",
            "supply-" + ids["sleeves 7"]: "0",
            "supply-" + ids["labels"]: "1",
            "supply-" + ids["tape"]: "1",
            # Not a shipping supply, so it's left alone.
            "supply-" + ids["inner"]: "1",
        },
    )

    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 1
    stock = {k: (s.quantity, s.pack_used) for k, s in supplies.items()}
    assert stock == {
        "mailers": (9, 0),
        "sleeves 12": (18, 0),
        "sleeves 7": (20, 0),
        "labels": (1, 0),
        "tape": (2, 0),
        "inner": (50, 0),
    }
    receipt = db.session.query(SalesReceipt).one()
    uses = {
        (u.supply.name, u.count, u.kind, u.sales_receipt, u.unit)
        for u in db.session.query(SupplyUse)
    }
    assert uses == {
        ("LP Mailers", 1, "shipping", receipt, None),
        ('Vinyl Outer Sleeves, 12"', 2, "shipping", receipt, None),
        ("Shipping Label Roll", 1, "shipping", receipt, None),
        ("Packing Tape Rolls", 1, "shipping", receipt, None),
    }


def test_stock_problems_names_each_supply_short_of_what_its_used_for(app):
    supplies = _shipping_supplies()

    problems = shipping.stock_problems(
        {supplies["mailers"]: 10, supplies["sleeves 12"]: 21, supplies["labels"]: 222}
    )

    assert len(problems) == 2
    assert (
        problems[0]
        .striptags()
        .startswith(
            "There aren't enough Vinyl Outer Sleeves, 12\" in stock for this: it uses"
            " 21, and the Supply List has 20 left."
        )
    )
    # The label roll has the last label of one roll and all of another.
    assert "it uses 222, and the Supply List has 221 left" in problems[1]
    # Each links to Reconcile Supplies with its own supply picked out.
    for problem, supply in zip(problems, [supplies["sleeves 12"], supplies["labels"]]):
        assert (
            '<a href="/reconcilesupplies/?supply={0}#supply-{0}">'.format(supply.id)
            in problem
        )


def test_ship_page_warns_when_a_supply_is_short(client, finances):
    order, unit = _order()
    supplies = _shipping_supplies()
    supplies["sleeves 12"].quantity = 0
    db.session.commit()

    html = client.get("/ebayordermodelview/ship/" + order.order_id).get_data(
        as_text=True
    )

    assert html.count("Not enough for this") == 1
    sleeves = supplies["sleeves 12"].id
    assert "/reconcilesupplies/?supply={0}#supply-{0}".format(sleeves) in html


def test_shipping_is_blocked_when_a_supply_would_run_out(client, discogs):
    order, unit = _order()
    supplies = _shipping_supplies()
    ids = {k: str(s.id) for k, s in supplies.items()}

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={
            "net_sold": "16.50",
            "supply-" + ids["mailers"]: "1",
            "supply-" + ids["sleeves 12"]: "21",
        },
    )

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "There aren't enough Vinyl Outer Sleeves, 12&#34; in stock" in html
    # The link survives being flashed.
    assert '<a href="/reconcilesupplies/?supply=' + ids["sleeves 12"] in html
    assert 'name="supply-' + ids["sleeves 12"] + '" value="21"' in html
    # Remove from Discogs was left unticked, and stays that way.
    assert re.search(r'name="remove_discogs-' + str(unit.id) + r'" value="1"\s*>', html)
    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 0
    assert (supplies["mailers"].quantity, supplies["sleeves 12"].quantity) == (10, 20)
    assert discogs.deleted == []


def test_shipping_can_use_up_the_last_of_a_supply(client, discogs):
    order, unit = _order()
    supplies = _shipping_supplies()
    ids = {k: str(s.id) for k, s in supplies.items()}

    client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "supply-" + ids["labels"]: "221"},
    )

    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 1
    assert (supplies["labels"].quantity, supplies["labels"].pack_used) == (0, 0)


@pytest.mark.parametrize("count", ["-1", "two", "1.5"])
def test_shipping_needs_whole_supply_counts(client, discogs, count):
    order, unit = _order()
    supplies = _shipping_supplies()

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "supply-" + str(supplies["mailers"].id): count},
    )

    # The page comes back with what was entered, to fix and ship again.
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Enter how many LP Mailers were used as a whole number." in html
    assert 'value="16.50"' in html
    assert 'name="supply-' + str(supplies["mailers"].id) + '" value="' + count in html
    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 0
    assert supplies["mailers"].quantity == 10
    assert discogs.deleted == []


SALE_WITH_FEES = {
    "transactionId": "T-SALE",
    "transactionDate": "2026-10-28T15:31:02.000Z",
    "transactionType": "SALE",
    "bookingEntry": "CREDIT",
    "amount": {"value": "21.13", "currency": "USD"},
    "totalFeeAmount": {"value": "4.17", "currency": "USD"},
    "orderLineItems": [
        {
            "lineItemId": "1",
            "marketplaceFees": [
                {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "3.27"}},
                {
                    "feeType": "FINAL_VALUE_FEE_FIXED_PER_ORDER",
                    "amount": {"value": "0.40"},
                },
                {"feeType": "REGULATORY_OPERATING_FEE", "amount": {"value": "0.10"}},
            ],
        }
    ],
}
AD_FEE = {
    "transactionType": "NON_SALE_CHARGE",
    "bookingEntry": "DEBIT",
    "feeType": "AD_FEE",
    "amount": {"value": "1.25", "currency": "USD"},
}


def _lines(earnings):
    return [(line["kind"], line["label"], line["amount"]) for line in earnings["lines"]]


def test_order_earnings_split_out_the_fees_in_the_sale(app, finances):
    finances.reply = _transactions(SALE_WITH_FEES, LABEL, AD_FEE)

    earnings = shipping.order_earnings("12-34567-89012")

    assert _lines(earnings) == [
        ("SALE", "Sale, less the taxes eBay collects", 25.30),
        ("FEE", "Final value fee", -3.27),
        ("FEE", "Final value fee per order", -0.40),
        ("FEE", "Regulatory operating fee", -0.10),
        # totalFeeAmount has 40 cents the per-fee list doesn't.
        ("FEE", "eBay fees", -0.40),
        ("SHIPPING_LABEL", "Shipping label", -4.63),
        ("NON_SALE_CHARGE", "Ad fee", -1.25),
    ]
    assert earnings["net"] == 15.25
    sale = earnings["lines"][0]
    assert sale["transaction_id"] == "T-SALE"
    assert sale["transaction_date"] == datetime.datetime(2026, 10, 28, 15, 31, 2)
    assert earnings["lines"][1]["fee_type"] == "FINAL_VALUE_FEE"
    assert earnings["lines"][-1]["fee_type"] == "AD_FEE"


def test_order_earnings_add_up_fees_of_each_kind_across_items(app, finances):
    fee = {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "1.50"}}
    finances.reply = _transactions(
        dict(
            SALE,
            totalFeeAmount={"value": "3.00"},
            orderLineItems=[{"marketplaceFees": [fee]}, {"marketplaceFees": [fee]}],
        )
    )

    earnings = shipping.order_earnings("12-34567-89012")

    assert _lines(earnings) == [
        ("SALE", "Sale, less the taxes eBay collects", 24.13),
        ("FEE", "Final value fee", -3.00),
    ]


def test_order_earnings_name_an_unknown_fee_readably(app, finances):
    finances.reply = _transactions(
        dict(
            SALE,
            totalFeeAmount={"value": "1.00"},
            orderLineItems=[
                {
                    "marketplaceFees": [
                        {"feeType": "BELOW_STANDARD_FEE", "amount": {"value": "1.00"}}
                    ]
                }
            ],
        )
    )

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["lines"][1]["label"] == "Below standard fee"


def test_shipping_keeps_ebays_earnings_lines_with_the_receipt(client, finances):
    order, unit = _order()
    finances.reply = _transactions(SALE_WITH_FEES, LABEL)

    client.post(
        "/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "16.50"}
    )

    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert [(line.kind, line.amount) for line in receipt.lines] == [
        ("SALE", 25.30),
        ("FEE", -3.27),
        ("FEE", -0.40),
        ("FEE", -0.10),
        ("FEE", -0.40),
        ("SHIPPING_LABEL", -4.63),
    ]
    assert receipt.lines_total() == receipt.net_sold == 16.50
    assert receipt.lines[0].ebay_order_id == order.order_id


def test_shipping_keeps_a_different_net_sold_as_a_manual_adjustment(client, finances):
    order, unit = _order()

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "15.00"},
        follow_redirects=True,
    )

    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert [(line.kind, line.label, line.amount) for line in receipt.lines] == [
        ("SALE", "Sale, less the taxes eBay collects", 21.13),
        ("SHIPPING_LABEL", "Shipping label", -4.63),
        ("MANUAL", "Manual adjustment", -1.50),
    ]
    assert receipt.lines_total() == 15.00
    assert "differs from eBay&#39;s earnings by -$1.50" in page.get_data(as_text=True)


def test_shipping_without_ebays_earnings_keeps_no_lines(client):
    # Without the finances fixture eBay isn't connected, so there are no lines.
    order, unit = _order()

    client.post(
        "/ebayordermodelview/ship/" + order.order_id, data={"net_sold": "16.50"}
    )

    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert receipt.net_sold == 16.50
    assert receipt.lines == []


def _shipped(net_sold=16.50, lines=None):
    """A Sales Receipt for the Carnaval order, with `lines` if given."""
    order, unit = _order()
    receipt = shipping.ship_order(order, unit, net_sold, lines=lines or [])
    return receipt


def test_refresh_earnings_page_shows_both_and_offers_ebays_net(client, finances):
    receipt = _shipped(net_sold=16.50)
    finances.reply = _transactions(
        SALE, LABEL, {**LABEL, "transactionType": "REFUND", "amount": {"value": "5.00"}}
    )

    html = client.get("/salesreceiptmodelview/earnings/" + str(receipt.id)).get_data(
        as_text=True
    )

    assert "No lines yet." in html
    assert "Refund" in html
    assert "Change Net Sold from $16.50 to eBay's $11.50" in html
    assert 'name="total" value="11.50"' in html


def test_refresh_earnings_saves_ebays_lines_and_net_sold(client, finances):
    receipt = _shipped(net_sold=16.50)
    refund = {**LABEL, "transactionType": "REFUND", "amount": {"value": "5.00"}}
    finances.reply = _transactions(SALE, LABEL, refund)

    page = client.post(
        "/salesreceiptmodelview/earnings/" + str(receipt.id),
        data={"total": "11.50", "update_net": "1"},
    )

    assert page.status_code == 302
    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert receipt.net_sold == 11.50
    assert [line.kind for line in receipt.lines] == ["SALE", "SHIPPING_LABEL", "REFUND"]


def test_refresh_earnings_can_keep_net_sold(client, finances):
    receipt = _shipped(net_sold=16.50, lines=[])
    refund = {**LABEL, "transactionType": "REFUND", "amount": {"value": "5.00"}}
    finances.reply = _transactions(SALE, LABEL, refund)

    client.post(
        "/salesreceiptmodelview/earnings/" + str(receipt.id), data={"total": "11.50"}
    )

    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert receipt.net_sold == 16.50
    assert receipt.lines[-1].kind == "MANUAL"
    assert receipt.lines[-1].amount == 5.00
    assert receipt.lines_total() == 16.50


def test_refresh_earnings_replaces_the_lines_it_had(client, finances):
    receipt = _shipped(lines=shipping.order_earnings("12-34567-89012")["lines"])
    finances.reply = _transactions(SALE, LABEL)

    client.post(
        "/salesreceiptmodelview/earnings/" + str(receipt.id),
        data={"total": "16.50", "update_net": "1"},
    )

    db.session.expire_all()
    assert len(db.session.get(SalesReceipt, receipt.id).lines) == 2


def test_refresh_earnings_wont_save_what_changed_since_it_was_shown(client, finances):
    receipt = _shipped()

    html = client.post(
        "/salesreceiptmodelview/earnings/" + str(receipt.id),
        data={"total": "11.50", "update_net": "1"},
    ).get_data(as_text=True)

    assert "changed since this page was shown" in html
    db.session.expire_all()
    assert db.session.get(SalesReceipt, receipt.id).lines == []


def test_refresh_earnings_is_off_in_dev_mode(client, finances, monkeypatch):
    receipt = _shipped()
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)

    show = client.get("/salesreceiptmodelview/show/" + str(receipt.id))
    page = client.post(
        "/salesreceiptmodelview/earnings/" + str(receipt.id), data={"total": "0.00"}
    )

    assert "refreshing earnings from eBay disabled" in show.get_data(as_text=True)
    assert "Refresh earnings from eBay</a>" not in show.get_data(as_text=True)
    assert finances.calls == []
    assert page.status_code == 200
    db.session.expire_all()
    assert db.session.get(SalesReceipt, receipt.id).lines == []


def test_receipt_page_shows_its_lines(client, finances):
    receipt = _shipped(lines=shipping.order_earnings("12-34567-89012")["lines"])

    html = client.get("/salesreceiptmodelview/show/" + str(receipt.id)).get_data(
        as_text=True
    )

    assert "Shipping label" in html
    assert "&minus;$4.63" in html
    assert "Refresh earnings from eBay</a>" in html


def test_receipt_page_says_when_there_are_no_lines(client):
    receipt = _shipped()

    html = client.get("/salesreceiptmodelview/show/" + str(receipt.id)).get_data(
        as_text=True
    )

    assert "no earnings breakdown" in html


def test_editing_net_sold_keeps_the_lines_adding_up(client, finances):
    receipt = _shipped(lines=shipping.order_earnings("12-34567-89012")["lines"])

    client.post(
        "/salesreceiptmodelview/edit/" + str(receipt.id),
        data={"date": "2026-10-28", "sold_price": "25.0", "net_sold": "14.00"},
    )

    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert receipt.net_sold == 14.00
    assert receipt.lines[-1].kind == "MANUAL"
    assert receipt.lines_total() == 14.00


def test_backfill_earnings_only_shows_without_apply(app, finances):
    receipt = _shipped()
    said = []

    counts = shipping.backfill_earnings(echo=said.append)

    assert counts == {
        "saved": 0,
        "differ": 0,
        "skipped": 0,
        "shared": 0,
        "unsettled": 0,
    }
    assert "2 lines, matches Net Sold" in said[0]
    assert "--apply" in said[-1]
    db.session.expire_all()
    assert db.session.get(SalesReceipt, receipt.id).lines == []


def test_backfill_earnings_saves_lines_and_keeps_net_sold(app, finances):
    receipt = _shipped(net_sold=20.00)
    said = []

    counts = shipping.backfill_earnings(apply=True, echo=said.append)

    assert counts == {
        "saved": 1,
        "differ": 1,
        "skipped": 0,
        "shared": 0,
        "unsettled": 0,
    }
    assert "differs from Net Sold $20.00" in said[0]
    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert receipt.net_sold == 20.00
    assert [line.kind for line in receipt.lines] == ["SALE", "SHIPPING_LABEL", "MANUAL"]


def test_backfill_earnings_can_set_net_sold_to_ebays(app, finances):
    receipt = _shipped(net_sold=20.00)

    shipping.backfill_earnings(apply=True, update_net=True, echo=lambda _: None)

    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert receipt.net_sold == 16.50
    assert [line.kind for line in receipt.lines] == ["SALE", "SHIPPING_LABEL"]


def test_backfill_earnings_leaves_receipts_with_lines_or_no_sale(app, finances):
    _shipped(lines=shipping.order_earnings("12-34567-89012")["lines"])
    finances.calls.clear()

    counts = shipping.backfill_earnings(apply=True, echo=lambda _: None)

    assert finances.calls == []
    assert counts["saved"] == 0

    db.session.query(SalesReceipt).one().lines = []
    db.session.commit()
    finances.reply = _transactions(LABEL)
    counts = shipping.backfill_earnings(apply=True, echo=lambda _: None)
    assert counts == {
        "saved": 0,
        "differ": 0,
        "skipped": 1,
        "shared": 0,
        "unsettled": 0,
    }


def test_backfill_earnings_stops_at_an_ebay_error(app, finances):
    _shipped()
    finances.reply = FakeHTTPResponse(403, {"errors": [{"message": "No access."}]})
    said = []

    counts = shipping.backfill_earnings(apply=True, echo=said.append)

    assert counts["saved"] == 0
    assert said[-2:] == [
        "Receipt 1 (2026-10-28, order 12-34567-89012): Getting the order's"
        " earnings: No access.",
        "Stopped; nothing more was asked of eBay.",
    ]


def test_backfill_earnings_command(app, finances):
    receipt = _shipped()

    result = app.test_cli_runner().invoke(
        args=["backfill-earnings", "--apply", "--since", "2026-10-01", "--limit", "5"]
    )

    assert result.exit_code == 0, result.output
    assert (
        "Saved 1, Net Sold differed on 0, eBay had no sale for 0, order on other"
        " receipts too for 0, return or dispute open for 0." in result.output
    )
    db.session.expire_all()
    assert len(db.session.get(SalesReceipt, receipt.id).lines) == 2


def _at(when, transaction, transaction_id):
    return dict(
        transaction,
        transactionDate="2026-09-" + when + ":00.000Z",
        transactionId=transaction_id,
    )


# A real order that was returned. eBay's order page shows earnings of
# -$9.19: the sale's $10.89 (item and shipping), less fees of $0.41, labels
# of $8.78 and a refund of $10.89, with a dispute and credit that cancel out.
# The refund's amount, $9.25, already has the final value fee eBay credited
# back taken off.
RETURN_LABEL = dict(LABEL, amount={"value": "4.39", "currency": "USD"})
RETURNED = [
    _at(
        "25T10:00",
        {
            "transactionType": "SALE",
            "bookingEntry": "CREDIT",
            "amount": {"value": "8.84"},
            "totalFeeAmount": {"value": "2.05"},
            "orderLineItems": [
                {
                    "marketplaceFees": [
                        {
                            "feeType": "FINAL_VALUE_FEE_FIXED_PER_ORDER",
                            "amount": {"value": "0.41"},
                        },
                        {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "1.64"}},
                    ]
                }
            ],
        },
        "03-15224-78875",
    ),
    _at("25T11:00", RETURN_LABEL, "02-15228-21773"),
    _at(
        "29T09:00",
        {
            "transactionType": "DISPUTE",
            "bookingEntry": "DEBIT",
            "amount": {"value": "10.89"},
        },
        "DISPUTE_HOLD-6765273232",
    ),
    _at(
        "30T09:00",
        {
            "transactionType": "REFUND",
            "bookingEntry": "DEBIT",
            "amount": {"value": "9.25"},
            "totalFeeAmount": {"value": "1.64"},
            "orderLineItems": [
                {
                    "marketplaceFees": [
                        {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "1.64"}}
                    ]
                }
            ],
        },
        "RRP-6766918393-CCM_CHARGE",
    ),
    _at("30T10:00", RETURN_LABEL, "FEE-7131055129012_11"),
    _at(
        "30T11:00",
        {
            "transactionType": "CREDIT",
            "bookingEntry": "CREDIT",
            "amount": {"value": "10.89"},
        },
        "DISPUTE_HOLD_REVERSE-6765273232-6650821330",
    ),
]


def test_order_earnings_split_out_the_fees_a_refund_credits_back(app, finances):
    # eBay lists the newest first; the lines are oldest first.
    finances.reply = _transactions(*reversed(RETURNED))

    earnings = shipping.order_earnings("12-34567-89012")

    assert _lines(earnings) == [
        ("SALE", "Sale, less the taxes eBay collects", 10.89),
        ("FEE", "Final value fee per order", -0.41),
        ("FEE", "Final value fee", -1.64),
        ("SHIPPING_LABEL", "Shipping label", -4.39),
        ("DISPUTE", "Dispute", -10.89),
        ("REFUND", "Refund to the buyer", -10.89),
        ("FEE", "Final value fee credited back", 1.64),
        ("SHIPPING_LABEL", "Shipping label", -4.39),
        ("CREDIT", "Credit", 10.89),
    ]
    assert earnings["net"] == -9.19


def test_a_returned_orders_totals_match_ebays_order_page(app, finances):
    finances.reply = _transactions(*reversed(RETURNED))
    _shipped(net_sold=-9.19, lines=shipping.order_earnings("12-34567-89012")["lines"])

    totals = earnings_totals()

    assert (
        totals["sales"],
        totals["ebay_fees"],
        totals["labels"],
        totals["refunds"],
        totals["other"],
        totals["adjustments"],
    ) == (10.89, -0.41, -8.78, -10.89, 0.0, 0.0)


def test_earnings_lines_fit_their_columns(app, finances):
    # SQLite keeps a string longer than its column, but MariaDB refuses it,
    # as it did a dispute hold's 42-character transaction ID.
    finances.reply = _transactions(*RETURNED, SALE_WITH_FEES, AD_FEE)
    columns = SalesReceiptLine.__table__.columns

    for line in shipping.order_earnings("12-34567-89012")["lines"]:
        for name, value in line.items():
            if isinstance(value, str):
                assert len(value) <= columns[name].type.length, (name, value)


def test_order_earnings_leave_out_a_transfer_paying_for_a_label(app, finances):
    # A real order: its label was paid for with a transfer, which eBay's
    # order page leaves out of the order's earnings of $2.69.
    finances.reply = _transactions(
        {
            "transactionType": "SALE",
            "bookingEntry": "CREDIT",
            "amount": {"value": "7.08"},
            "totalFeeAmount": {"value": "1.56"},
            "orderLineItems": [
                {
                    "marketplaceFees": [
                        {
                            "feeType": "FINAL_VALUE_FEE_FIXED_PER_ORDER",
                            "amount": {"value": "0.31"},
                        },
                        {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "1.25"}},
                    ]
                }
            ],
        },
        RETURN_LABEL,
        {
            "transactionType": "TRANSFER",
            "bookingEntry": "CREDIT",
            "amount": {"value": "4.39", "currency": "USD"},
        },
    )

    earnings = shipping.order_earnings("12-34567-89012")

    assert [line["kind"] for line in earnings["lines"]] == [
        "SALE",
        "FEE",
        "FEE",
        "SHIPPING_LABEL",
    ]
    assert earnings["net"] == 2.69


def _split_order():
    """The Carnaval order split by hand into two Sales Receipts."""
    receipt = _shipped(net_sold=10.0)
    other = SalesReceipt(
        date=receipt.date,
        ebay_orders=list(receipt.ebay_orders),
        sold_price=12.0,
        net_sold=6.5,
    )
    db.session.add(other)
    db.session.commit()
    return receipt, other


def test_backfill_earnings_leaves_an_order_split_across_receipts(app, finances):
    receipt, other = _split_order()
    said = []

    counts = shipping.backfill_earnings(apply=True, echo=said.append)

    assert counts == {
        "saved": 0,
        "differ": 0,
        "skipped": 0,
        "shared": 2,
        "unsettled": 0,
    }
    assert finances.calls == []
    assert "its order is on 2 receipts" in said[0]
    db.session.expire_all()
    assert db.session.get(SalesReceipt, receipt.id).lines == []
    assert db.session.get(SalesReceipt, other.id).lines == []


def test_refresh_earnings_refuses_an_order_split_across_receipts(client, finances):
    receipt, other = _split_order()

    show = client.get("/salesreceiptmodelview/show/" + str(receipt.id))
    page = client.post(
        "/salesreceiptmodelview/earnings/" + str(other.id),
        data={"total": "16.50", "update_net": "1"},
        follow_redirects=True,
    )

    html = show.get_data(as_text=True)
    assert "on other Sales Receipts too" in html
    assert "Refresh earnings from eBay</a>" not in html
    assert "on other Sales Receipts too" in page.get_data(as_text=True)
    assert finances.calls == []
    db.session.expire_all()
    assert db.session.get(SalesReceipt, other.id).net_sold == 6.5
    assert db.session.get(SalesReceipt, other.id).lines == []


# The returned order as it stood while its return was open: sold, shipped,
# and eBay holding the sale's money.
RETURN_OPEN = RETURNED[:3]


def test_order_earnings_say_when_ebay_is_holding_money(app, finances):
    finances.reply = _transactions(*reversed(RETURN_OPEN))

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["unsettled"] is True
    assert earnings["net"] == -6.44
    assert "eBay is holding $10.89" in earnings["warnings"][0]


def test_order_earnings_are_settled_once_ebay_releases_the_hold(app, finances):
    finances.reply = _transactions(*reversed(RETURNED))

    earnings = shipping.order_earnings("12-34567-89012")

    assert earnings["unsettled"] is False
    assert earnings["warnings"] == []


def test_order_earnings_name_a_refunded_label(app, finances):
    # A label bought for a return the buyer never sent, then refunded.
    finances.reply = _transactions(
        SALE, LABEL, RETURN_LABEL, dict(RETURN_LABEL, bookingEntry="CREDIT")
    )

    earnings = shipping.order_earnings("12-34567-89012")

    assert _lines(earnings)[-1] == (
        "SHIPPING_LABEL",
        "Shipping label refunded",
        4.39,
    )
    assert earnings["net"] == 16.50


def test_refresh_earnings_leaves_net_sold_unticked_while_ebay_holds_money(
    client, finances
):
    receipt = _shipped(net_sold=6.50)
    finances.reply = _transactions(*reversed(RETURN_OPEN))

    html = client.get("/salesreceiptmodelview/earnings/" + str(receipt.id)).get_data(
        as_text=True
    )

    assert "eBay is holding $10.89" in html
    assert 'name="update_net" value="1" >' in html

    finances.reply = _transactions(*reversed(RETURNED))
    html = client.get("/salesreceiptmodelview/earnings/" + str(receipt.id)).get_data(
        as_text=True
    )
    assert 'name="update_net" value="1" checked>' in html


def test_backfill_earnings_leaves_an_open_return_for_a_later_run(app, finances):
    receipt = _shipped(net_sold=6.50)
    finances.reply = _transactions(*reversed(RETURN_OPEN))
    said = []

    counts = shipping.backfill_earnings(apply=True, update_net=True, echo=said.append)

    assert counts == {
        "saved": 0,
        "differ": 0,
        "skipped": 0,
        "shared": 0,
        "unsettled": 1,
    }
    assert "holding $10.89 while a return or dispute is open" in said[0]
    db.session.expire_all()
    receipt = db.session.get(SalesReceipt, receipt.id)
    assert (receipt.net_sold, receipt.lines) == (6.50, [])

    finances.reply = _transactions(*reversed(RETURNED))
    counts = shipping.backfill_earnings(apply=True, update_net=True, echo=said.append)
    assert counts["saved"] == 1
