import datetime
from types import SimpleNamespace

import pytest
from discogs_client.exceptions import HTTPError

import fbc
from fbc import db
from fbc import listing
from fbc import shipping
from fbc.models.discogs import DiscogsInstance, DiscogsRelease
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import Supply
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
    assert earnings["lines"] == [
        {"label": "Sale, less eBay's fees and taxes", "amount": 21.13},
        {"label": "Shipping label", "amount": -4.63},
    ]
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


def test_ship_this_is_offered_for_an_order_with_one_unsold_unit(app):
    order, unit = _order()

    assert "Ship&nbsp;This" in str(order.links())


def test_ship_this_is_not_offered_once_there_is_a_sales_receipt(app):
    order, unit = _order()
    db.session.add(
        SalesReceipt(
            date=datetime.date(2026, 10, 28),
            ebay_order=order,
            sold_price=25.0,
            net_sold=16.5,
        )
    )
    db.session.commit()

    assert "Ship&nbsp;This" not in str(order.links())


def test_ship_this_is_not_offered_when_the_unit_is_unclear(app):
    order, unit = _order()
    db.session.add(Unit(name="Another copy", ebay_listing_id="L1"))
    db.session.commit()

    assert shipping.order_unit(order) is None
    assert "Ship&nbsp;This" not in str(order.links())


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

    assert "This order has 2 items" in html
    assert "disabled>Ship</button>" in html


def test_shipping_records_the_receipt_marks_sold_and_removes_from_discogs(
    client, discogs
):
    order, unit = _order()

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "remove_discogs": "1"},
    )

    assert page.status_code == 302
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert (
        receipt.date,
        receipt.ebay_order_id,
        receipt.sold_price,
        receipt.net_sold,
    ) == (
        datetime.date(2026, 10, 28),
        "12-34567-89012",
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
            name="Shipping Label Rolls",
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
        ("Shipping Label Rolls", 1),
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


@pytest.mark.parametrize("count", ["-1", "two", "1.5"])
def test_shipping_needs_whole_supply_counts(client, discogs, count):
    order, unit = _order()
    supplies = _shipping_supplies()

    page = client.post(
        "/ebayordermodelview/ship/" + order.order_id,
        data={"net_sold": "16.50", "supply-" + str(supplies["mailers"].id): count},
    )

    assert page.headers["Location"].endswith("/ship/" + order.order_id)
    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 0
    assert supplies["mailers"].quantity == 10
    assert discogs.deleted == []
