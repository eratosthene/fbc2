from fbc import db
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.util import add_ebay_listing, add_ebay_order


def _ebay_listing_payload(item_id="123456", price="19.99"):
    return {
        "ItemID": item_id,
        "Title": "A Fine Record",
        "BuyItNowPrice": {"value": price},
        "ListingDetails": {
            "ViewItemURL": "https://www.ebay.com/itm/" + item_id,
            "StartTime": "2024-01-15T10:00:00.000Z",
        },
    }


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
