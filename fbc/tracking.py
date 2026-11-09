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
# Tracking 3.2: one POST of up to 35 numbers, so a sync is one request.
USPS_TRACKING_URL = "https://apis.usps.com/tracking/v3r2/tracking"
MAX_NUMBERS = 35
# The most orders one sync asks USPS about (one request).
SYNC_LOOKUPS = MAX_NUMBERS

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
    status = (body.get("status") or body.get("statusSummary") or "").strip()
    category = (body.get("statusCategory") or "").strip().lower()
    events = body.get("trackingEvents") or []
    times = [_parse_time(e.get("eventTimestamp")) for e in events]
    times = [t for t in times if t]
    return {
        "status": status[:100] or None,
        "delivered": category == "delivered" or status.lower().startswith("delivered"),
        "last_event": max(times) if times else None,
    }


def _why(r):
    """ ": <message>" from a USPS error body (see its ErrorMessage), or ""."""
    try:
        error = r.json().get("error") or {}
    except (ValueError, AttributeError):
        return ""
    messages = [error.get("message")] + [
        e.get("detail") or e.get("code") for e in error.get("errors") or []
    ]
    text = "; ".join(m for m in messages if m)
    if text:
        logger.warning("USPS error body: " + text)
    return ": " + text[:300] if text else ""


def fetch_statuses(numbers):
    """
    Ask USPS about up to MAX_NUMBERS tracking numbers in one request. Returns
    {number: result}, where result is parse_usps's dict, or None if USPS
    doesn't know that number yet. Raises TrackingError if the request fails.
    """
    if devmode.on():
        raise TrackingError(devmode.message("USPS tracking lookup"))
    if not configured():
        raise TrackingError("USPS_SETTINGS has no client ID and secret.")
    numbers = list(numbers)[:MAX_NUMBERS]
    token = _get_token()
    try:
        r = requests.post(
            USPS_TRACKING_URL,
            headers={"Authorization": "Bearer " + token},
            json=[{"trackingNumber": n} for n in numbers],
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
        return dict.fromkeys(numbers)
    # 207 is a mix: each entry has its own statusCode, and the ones that
    # failed are an error body with the tracking number.
    if r.status_code not in (200, 207):
        raise TrackingError("USPS answered HTTP " + str(r.status_code) + _why(r) + ".")
    found = dict.fromkeys(numbers)
    for entry in r.json():
        if str(entry.get("statusCode", "200")) != "200" or entry.get("error"):
            continue
        if entry.get("trackingNumber") in found:
            found[entry["trackingNumber"]] = parse_usps(entry)
    return found


def fetch_status(number):
    """What USPS says about one tracking number (see fetch_statuses)."""
    result = fetch_statuses([number])[number]
    return result or {
        "status": "USPS has no scan yet",
        "delivered": False,
        "last_event": None,
    }


def is_fresh(order, now=None):
    """Whether the order was checked recently enough to leave alone."""
    minutes = settings().get("CACHE_MINUTES", 60) or 0
    if not order.tracking_checked:
        return False
    return (now or _now()) - order.tracking_checked < datetime.timedelta(
        minutes=minutes
    )


def _keep(order, result):
    order.tracking_status = result["status"]
    order.tracking_delivered = result["delivered"]
    order.tracking_last_event = result["last_event"] or order.tracking_last_event
    order.tracking_checked = _now()


def refresh_orders(orders, force=False):
    """
    Ask USPS about orders, MAX_NUMBERS to a request, and keep the answers.
    Orders that aren't USPS, are delivered, or (unless force) were checked
    within CACHE_MINUTES are left alone. Returns (how many it checked, why it
    stopped early or None). Doesn't commit.
    """
    todo = [
        o
        for o in orders
        if can_check(o) and not o.tracking_delivered and (force or not is_fresh(o))
    ]
    checked = 0
    for i in range(0, len(todo), MAX_NUMBERS):
        batch = todo[i : i + MAX_NUMBERS]
        try:
            found = fetch_statuses([o.tracking_number for o in batch])
        except TrackingError as e:
            return checked, str(e)
        for o in batch:
            _keep(
                o,
                found.get(o.tracking_number)
                or {
                    "status": "USPS has no scan yet",
                    "delivered": False,
                    "last_event": None,
                },
            )
            checked += 1
    return checked, None


def refresh(order, force=False):
    """refresh_orders for one order: None if it's fine, else why not."""
    if not can_check(order):
        return "Only USPS shipments can be checked."
    return refresh_orders([order], force)[1]


def refresh_pending(limit=SYNC_LOOKUPS):
    """
    Check the shipped, undelivered, unarchived USPS orders last checked
    longest ago, up to limit. Does nothing without USPS keys or in dev mode,
    and stops at the first problem (USPS pushing back, say). Returns how many
    it checked.
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
    orders = [o for o in orders if can_check(o) and not is_fresh(o)][:limit]
    checked, problem = refresh_orders(orders)
    if problem:
        logger.warning("Tracking lookup stopped: " + problem)
    db.session.commit()
    return checked


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
