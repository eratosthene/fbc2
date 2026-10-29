import datetime

from fbc import db
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import PurchaseLot, Unit
from fbc.util import (
    add_ebay_listing,
    add_ebay_order,
    find_relisted_listing,
    relist_ebay_listing,
)


def _ebay_listing_payload(item_id="123456", price="19.99", title="A Fine Record"):
    return {
        "ItemID": item_id,
        "Title": title,
        "BuyItNowPrice": {"value": price},
        "ListingDetails": {
            "ViewItemURL": "https://www.ebay.com/itm/" + item_id,
            "StartTime": "2024-01-15T10:00:00.000Z",
        },
    }


def _make_unit(sold=False, ebay_listing_id=None, name="Unit"):
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    db.session.add(lot)
    db.session.commit()
    unit = Unit(
        name=name,
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        sold=sold,
        ebay_listing_id=ebay_listing_id,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


def test_add_ebay_listing_creates_new_listing(app):
    add_ebay_listing(_ebay_listing_payload())

    listing = db.session.query(EbayListing).filter_by(item_id="123456").first()
    assert listing is not None
    assert listing.title == "A Fine Record"
    assert listing.price == 19.99
    assert listing.url == "https://www.ebay.com/itm/123456"
    assert listing.start_time.isoformat() == "2024-01-15T10:00:00"


def test_add_ebay_listing_is_idempotent_for_existing_item(app):
    add_ebay_listing(_ebay_listing_payload(price="19.99"))
    # Calling again with a different price should NOT update the existing row.
    add_ebay_listing(_ebay_listing_payload(price="29.99"))

    listings = db.session.query(EbayListing).filter_by(item_id="123456").all()
    assert len(listings) == 1
    assert listings[0].price == 19.99


def _ebay_order_payload(order_id="O-1", item_id="123456"):
    return {
        "OrderID": order_id,
        "Subtotal": {"value": "19.99"},
        "BuyerUserID": "buyer42",
        "CreatedTime": "2024-01-16T10:00:00.000Z",
        "TransactionArray": {
            "Transaction": [
                {"Item": {"Title": "A Fine Record", "ItemID": item_id}},
            ]
        },
    }


def test_add_ebay_order_creates_order_when_listing_exists(app):
    add_ebay_listing(_ebay_listing_payload())
    add_ebay_order(_ebay_order_payload())

    order = db.session.query(EbayOrder).filter_by(order_id="O-1").first()
    assert order is not None
    assert order.price == 19.99
    assert order.buyer == "buyer42"
    assert order.title == "A Fine Record"
    assert order.ebay_listing_id == "123456"


def test_add_ebay_order_skips_when_listing_missing(app):
    add_ebay_order(_ebay_order_payload())

    assert db.session.query(EbayOrder).filter_by(order_id="O-1").first() is None


def test_add_ebay_order_is_idempotent_for_existing_order(app):
    add_ebay_listing(_ebay_listing_payload())
    add_ebay_order(_ebay_order_payload())
    add_ebay_order(_ebay_order_payload())

    orders = db.session.query(EbayOrder).filter_by(order_id="O-1").all()
    assert len(orders) == 1


def test_find_relisted_listing_matches_by_title_when_old_item_gone(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    old_listing = db.session.query(EbayListing).filter_by(item_id="OLD-1").first()
    _make_unit(sold=False, ebay_listing_id="OLD-1")

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    match = find_relisted_listing(new_item, remote_item_ids={"NEW-1"})

    assert match is not None
    assert match.item_id == old_listing.item_id


def test_find_relisted_listing_ignores_old_item_still_active(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    _make_unit(sold=False, ebay_listing_id="OLD-1")

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    # OLD-1 is still reported active by eBay, so it can't be the relisted
    # predecessor of NEW-1.
    match = find_relisted_listing(new_item, remote_item_ids={"NEW-1", "OLD-1"})

    assert match is None


def test_find_relisted_listing_ignores_listing_with_no_unit(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    match = find_relisted_listing(new_item, remote_item_ids={"NEW-1"})

    assert match is None


def test_find_relisted_listing_ignores_listing_with_sold_unit(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    _make_unit(sold=True, ebay_listing_id="OLD-1")

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    match = find_relisted_listing(new_item, remote_item_ids={"NEW-1"})

    assert match is None


def test_find_relisted_listing_is_none_when_ambiguous(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-2", title="Manhattan Transfer LP"))
    _make_unit(sold=False, ebay_listing_id="OLD-1")
    _make_unit(sold=False, ebay_listing_id="OLD-2")

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    match = find_relisted_listing(new_item, remote_item_ids={"NEW-1"})

    assert match is None


def test_relist_ebay_listing_repoints_unit_and_removes_old_listing(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    old_listing = db.session.query(EbayListing).filter_by(item_id="OLD-1").first()
    unit = _make_unit(sold=False, ebay_listing_id="OLD-1")

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    relist_ebay_listing(old_listing, new_item)

    assert db.session.query(EbayListing).filter_by(item_id="OLD-1").first() is None
    new_listing = db.session.query(EbayListing).filter_by(item_id="NEW-1").first()
    assert new_listing is not None

    db.session.refresh(unit)
    assert unit.ebay_listing_id == "NEW-1"


def test_relist_ebay_listing_repoints_existing_orders(app):
    add_ebay_listing(_ebay_listing_payload(item_id="OLD-1", title="Manhattan Transfer LP"))
    old_listing = db.session.query(EbayListing).filter_by(item_id="OLD-1").first()
    add_ebay_order(_ebay_order_payload(order_id="O-1", item_id="OLD-1"))

    new_item = _ebay_listing_payload(item_id="NEW-1", title="Manhattan Transfer LP")
    relist_ebay_listing(old_listing, new_item)

    order = db.session.query(EbayOrder).filter_by(order_id="O-1").first()
    assert order.ebay_listing_id == "NEW-1"
