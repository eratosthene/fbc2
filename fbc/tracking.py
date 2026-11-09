"""
Shipment tracking for eBay Orders.

eBay's GetOrders gives each order's tracking number and carrier (once the
label is bought on eBay, or the seller uploads them), and that's all: it has
no tracking events or delivered state. Those come only from the carrier. fbc
asks USPS (the Tracking API v3, with the client ID and secret in
USPS_SETTINGS) and, for any other carrier, or without those keys, only links
to the carrier's own tracking page. See "Shipment tracking" in
docs/development.md.
"""

import datetime
import logging
from urllib.parse import quote

import requests

from fbc import devmode

logger = logging.getLogger()

USPS_TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"
USPS_TRACKING_URL = "https://apis.usps.com/tracking/v3/tracking/"
# The most orders one sync asks USPS about, as USPS's default limit is low.
SYNC_LOOKUPS = 20

# eBay's carrier codes (ShippingCarrierCodeType) to a name and a tracking page.
CARRIERS = {
    "USPS": ("USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels={}"),
    "UPS": ("UPS", "https://www.ups.com/track?tracknum={}"),
    "FEDEX": ("FedEx", "https://www.fedex.com/fedextrack/?trknbr={}"),
    "DHL": ("DHL", "https://www.dhl.com/us-en/home/tracking.html?tracking-id={}"),
}


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def from_order(item):
    """
    (carrier, tracking number) from a GetOrders order, or (None, None) while
    nothing has shipped. eBay puts them on each transaction's ShippingDetails
    (older responses had them on the order's); an order of several items
    usually ships in one package, so the first one found is used.
    """
    details = list(_as_list(item.get("ShippingDetails")))
    for t in _as_list((item.get("TransactionArray") or {}).get("Transaction")):
        details.extend(_as_list(t.get("ShippingDetails")))
    for d in details:
        for tracking in _as_list(d.get("ShipmentTrackingDetails")):
            number = (tracking.get("ShipmentTrackingNumber") or "").strip()
            if number:
                carrier = (tracking.get("ShippingCarrierUsed") or "").strip()
                return carrier[:40] or None, number[:40]
    return None, None


def carrier_name(carrier):
    return CARRIERS.get((carrier or "").upper(), (carrier or "", None))[0]


def tracking_url(carrier, number):
    """The carrier's tracking page for number, or None if fbc doesn't know it."""
    page = CARRIERS.get((carrier or "").upper())
    if not page or not number:
        return None
    return page[1].format(quote(number, safe=""))


class TrackingError(Exception):
    """A status lookup that couldn't be done; the message says why."""


def settings():
    from fbc import app

    return app.config.get("USPS_SETTINGS") or {}


def configured():
    s = settings()
    return bool(s.get("CLIENT_ID") and s.get("CLIENT_SECRET"))


def is_usps(carrier):
    return (carrier or "").upper() == "USPS"


def can_check(order):
    """Whether fbc could ask a carrier about this order's package."""
    return bool(order.tracking_number) and is_usps(order.shipping_carrier)


_token = {"value": None, "expires": None}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _get_token():
    if _token["value"] and _token["expires"] > _now():
        return _token["value"]
    s = settings()
    try:
        r = requests.post(
            USPS_TOKEN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": s["CLIENT_ID"],
                "client_secret": s["CLIENT_SECRET"],
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise TrackingError("Couldn't reach USPS: " + str(e))
    if r.status_code != 200:
        raise TrackingError(
            "USPS wouldn't sign fbc in (HTTP " + str(r.status_code) + "). Check"
            " USPS_SETTINGS."
        )
    body = r.json()
    # Renewed a few minutes early.
    seconds = int(body.get("expires_in") or 3600) - 300
    _token["value"] = body["access_token"]
    _token["expires"] = _now() + datetime.timedelta(seconds=max(seconds, 60))
    return _token["value"]


def _parse_time(text):
    """An ISO timestamp from USPS as a naive UTC datetime, or None."""
    try:
        t = datetime.datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo:
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return t


def parse_usps(body):
    """
    {"status", "delivered", "last_event"} from a USPS tracking response. The
    field names are from USPS's docs as others describe them; anything
    missing leaves the matching value blank.
    """
    status = (body.get("statusSummary") or body.get("status") or "").strip()
    category = (body.get("statusCategory") or "").strip().lower()
    events = body.get("trackingEvents") or []
    times = [_parse_time(e.get("eventTimestamp")) for e in events]
    times = [t for t in times if t]
    return {
        "status": status[:100] or None,
        "delivered": category == "delivered" or status.lower().startswith("delivered"),
        "last_event": max(times) if times else None,
    }


def fetch_status(number):
    """Ask USPS about a tracking number. Raises TrackingError."""
    if devmode.on():
        raise TrackingError(devmode.message("USPS tracking lookup"))
    if not configured():
        raise TrackingError("USPS_SETTINGS has no client ID and secret.")
    token = _get_token()
    try:
        r = requests.get(
            USPS_TRACKING_URL + quote(number, safe=""),
            headers={"Authorization": "Bearer " + token},
            params={"expand": "DETAIL"},
            timeout=30,
        )
    except requests.RequestException as e:
        raise TrackingError("Couldn't reach USPS: " + str(e))
    if r.status_code == 401:
        _token["value"] = None
        raise TrackingError("USPS refused fbc's sign-in; try again.")
    if r.status_code == 429:
        raise TrackingError("USPS says fbc has asked too often; try again later.")
    if r.status_code == 404:
        return {
            "status": "USPS has no scan yet",
            "delivered": False,
            "last_event": None,
        }
    if r.status_code != 200:
        raise TrackingError("USPS answered HTTP " + str(r.status_code) + ".")
    return parse_usps(r.json())


def is_fresh(order, now=None):
    """Whether the order was checked recently enough to leave alone."""
    minutes = settings().get("CACHE_MINUTES", 60) or 0
    if not order.tracking_checked:
        return False
    return (now or _now()) - order.tracking_checked < datetime.timedelta(
        minutes=minutes
    )


def refresh(order, force=False):
    """
    Ask USPS about an order and keep the answer. Returns None when it was
    done or left alone as fresh (unless force), else why it couldn't be.
    A delivered order is never asked again. Doesn't commit.
    """
    if not can_check(order):
        return "Only USPS shipments can be checked."
    if order.tracking_delivered:
        return None
    if not force and is_fresh(order):
        return None
    try:
        result = fetch_status(order.tracking_number)
    except TrackingError as e:
        return str(e)
    order.tracking_status = result["status"]
    order.tracking_delivered = result["delivered"]
    order.tracking_last_event = result["last_event"] or order.tracking_last_event
    order.tracking_checked = _now()
    return None


def refresh_pending(limit=SYNC_LOOKUPS):
    """
    Check the shipped, undelivered, unarchived USPS orders last checked
    longest ago, up to limit. Does nothing without USPS keys, in dev mode
    or if USPS pushes back. Returns how many it checked.
    """
    from fbc import db
    from fbc.models.ebay import EbayOrder

    if devmode.on() or not configured():
        return 0
    orders = (
        db.session.query(EbayOrder)
        .filter(
            EbayOrder.tracking_number.isnot(None),
            EbayOrder.tracking_delivered.is_(False),
            EbayOrder.archived.is_(False),
        )
        .order_by(
            EbayOrder.tracking_checked.is_(None).desc(), EbayOrder.tracking_checked
        )
        .all()
    )
    done = 0
    for order in orders:
        if done >= limit:
            break
        if not can_check(order) or is_fresh(order):
            continue
        problem = refresh(order)
        if problem:
            # One failure (rate limit, USPS down) is likely to repeat.
            logger.warning("Tracking lookup stopped: " + problem)
            break
        db.session.commit()
        done += 1
    return done


def tracking_state(order, now=None):
    """
    (text, bootstrap label colour) for what USPS last said about an order,
    or None if it hasn't been asked. A shipped order that hasn't moved for
    NO_MOVEMENT_DAYS says so.
    """
    if not order.tracking_status:
        return None
    if order.tracking_delivered:
        return order.tracking_status, "success"
    days = settings().get("NO_MOVEMENT_DAYS", 5) or 0
    last = order.tracking_last_event
    if days and last and (now or _now()) - last >= datetime.timedelta(days=days):
        idle = ((now or _now()) - last).days
        return order.tracking_status + " - no movement for " + str(idle) + " days", (
            "warning"
        )
    return order.tracking_status, "info"
