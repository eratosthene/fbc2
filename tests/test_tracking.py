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


# --- USPS status ---------------------------------------------------------

import datetime  # noqa: E402

import pytest  # noqa: E402

from fbc import tracking  # noqa: E402


class _Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def usps(app, monkeypatch):
    """USPS keys set and a fake USPS: lists the requests it got."""
    monkeypatch.setitem(
        app.config,
        "USPS_SETTINGS",
        {"CLIENT_ID": "id", "CLIENT_SECRET": "secret", "CACHE_MINUTES": 60},
    )
    monkeypatch.setitem(app.config, "DEV_MODE", False)
    tracking._token.update(value=None, expires=None)
    seen = {"posts": [], "gets": [], "reply": _Response(200, {})}

    def post(url, **kw):
        seen["posts"].append(url)
        return _Response(200, {"access_token": "tok", "expires_in": 3600})

    def get(url, **kw):
        seen["gets"].append((url, kw))
        return seen["reply"]

    monkeypatch.setattr(tracking.requests, "post", post)
    monkeypatch.setattr(tracking.requests, "get", get)
    return seen


def _usps_order():
    add_ebay_listing(_ebay_listing_payload())
    add_ebay_order(_shipped(_ebay_order_payload()))
    return db.session.query(EbayOrder).one()


IN_TRANSIT = {
    "statusCategory": "In Transit",
    "statusSummary": "Your item arrived at the post office",
    "trackingEvents": [
        {"eventTimestamp": "2024-01-17T10:00:00Z"},
        {"eventTimestamp": "2024-01-18T10:00:00Z"},
    ],
}


def test_refresh_keeps_status_and_latest_event(usps):
    order = _usps_order()
    usps["reply"] = _Response(200, IN_TRANSIT)

    assert tracking.refresh(order) is None

    assert order.tracking_status == "Your item arrived at the post office"
    assert not order.tracking_delivered
    assert order.tracking_last_event == datetime.datetime(2024, 1, 18, 10)
    assert usps["gets"][0][0].endswith("/9400111899223344556677")
    assert usps["gets"][0][1]["headers"]["Authorization"] == "Bearer tok"


def test_refresh_marks_delivered_and_never_asks_again(usps):
    order = _usps_order()
    usps["reply"] = _Response(
        200, {"statusCategory": "Delivered", "statusSummary": "Delivered, Mailbox"}
    )
    tracking.refresh(order)
    assert order.tracking_delivered
    assert tracking.tracking_state(order) == ("Delivered, Mailbox", "success")

    usps["gets"].clear()
    assert tracking.refresh(order, force=True) is None
    assert usps["gets"] == []


def test_refresh_is_cached_unless_forced(usps):
    order = _usps_order()
    usps["reply"] = _Response(200, IN_TRANSIT)
    tracking.refresh(order)
    tracking.refresh(order)
    assert len(usps["gets"]) == 1
    tracking.refresh(order, force=True)
    assert len(usps["gets"]) == 2
    # One sign-in serves them all.
    assert len(usps["posts"]) == 1


def test_rate_limit_is_reported_and_keeps_old_status(usps):
    order = _usps_order()
    order.tracking_status = "Old"
    usps["reply"] = _Response(429)

    problem = tracking.refresh(order)

    assert "too often" in problem
    assert order.tracking_status == "Old"
    assert order.tracking_checked is None


def test_no_movement_flag(usps):
    order = _usps_order()
    order.tracking_status = "In Transit"
    order.tracking_last_event = datetime.datetime(2024, 1, 1)
    now = datetime.datetime(2024, 1, 10)
    label, css = tracking.tracking_state(order, now=now)
    assert css == "warning" and "9 days" in label
    assert tracking.tracking_state(order, now=datetime.datetime(2024, 1, 3))[1] == (
        "info"
    )


def test_refresh_pending_skips_delivered_other_carriers_and_stops_on_error(usps):
    order = _usps_order()
    usps["reply"] = _Response(200, IN_TRANSIT)
    assert tracking.refresh_pending() == 1
    # Fresh now, so a second sync leaves it alone.
    assert tracking.refresh_pending() == 0
    order.tracking_checked = None
    usps["reply"] = _Response(429)
    assert tracking.refresh_pending() == 0


def test_dev_mode_never_reaches_usps(usps, app, monkeypatch):
    order = _usps_order()
    monkeypatch.setitem(app.config, "DEV_MODE", True)
    problem = tracking.refresh(order, force=True)
    assert "Dev Mode is turned on" in problem
    assert usps["gets"] == [] and usps["posts"] == []
    assert tracking.refresh_pending() == 0


def test_without_keys_only_links(app):
    order = _usps_order()
    assert "client ID" in tracking.refresh(order)
    assert order.tracking_status is None
