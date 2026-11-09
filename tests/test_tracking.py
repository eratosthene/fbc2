from fbc import db
from fbc.models.ebay import EbayOrder
from fbc.tracking import carrier_name, from_order, tracking_url
from fbc.util import add_ebay_listing, add_ebay_order
from test_util_ebay_sync import _ebay_listing_payload, _ebay_order_payload


def _shipped(order, carrier="USPS", number="9400111899223344556677"):
    t = order["TransactionArray"]["Transaction"][0]
    t["ShippingDetails"] = {
        "ShipmentTrackingDetails": {
            "ShippingCarrierUsed": carrier,
            "ShipmentTrackingNumber": number,
        }
    }
    return order


def test_from_order_reads_a_lone_tracking_detail():
    assert from_order(_shipped(_ebay_order_payload())) == (
        "USPS",
        "9400111899223344556677",
    )


def test_from_order_takes_the_first_of_a_list_and_skips_blanks():
    order = _ebay_order_payload()
    order["TransactionArray"]["Transaction"][0]["ShippingDetails"] = {
        "ShipmentTrackingDetails": [
            {"ShippingCarrierUsed": "UPS", "ShipmentTrackingNumber": " "},
            {"ShippingCarrierUsed": "UPS", "ShipmentTrackingNumber": "1Z999"},
        ]
    }
    assert from_order(order) == ("UPS", "1Z999")


def test_from_order_when_not_shipped():
    assert from_order(_ebay_order_payload()) == (None, None)


def test_tracking_url_for_known_and_unknown_carriers():
    assert "1Z 9" not in tracking_url("UPS", "1Z 9")
    assert tracking_url("usps", "940").endswith("tLabels=940")
    assert tracking_url("Mystery", "940") is None
    assert carrier_name("FEDEX") == "FedEx"


def test_order_gets_tracking_and_later_orders_syncs_fill_it_in(app):
    add_ebay_listing(_ebay_listing_payload())
    add_ebay_order(_ebay_order_payload())
    order = db.session.query(EbayOrder).one()
    assert order.fmt_tracking() == ""

    add_ebay_order(_shipped(_ebay_order_payload()))
    db.session.refresh(order)
    assert order.tracking_number == "9400111899223344556677"
    assert "tLabels=9400111899223344556677" in str(order.fmt_tracking())

    # An order eBay no longer shows tracking for keeps what it had.
    add_ebay_order(_ebay_order_payload())
    db.session.refresh(order)
    assert order.shipping_carrier == "USPS"


def test_unknown_carrier_shows_text_without_a_link(app):
    add_ebay_listing(_ebay_listing_payload())
    add_ebay_order(_shipped(_ebay_order_payload(), carrier="Mystery", number="AB1"))
    order = db.session.query(EbayOrder).one()
    assert str(order.fmt_tracking()) == "Mystery AB1"
