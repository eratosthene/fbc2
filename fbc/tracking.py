"""
Shipment tracking for eBay Orders.

eBay's GetOrders gives each order's tracking number and carrier (once the
label is bought on eBay, or the seller uploads them), and that's all: it has
no tracking events or delivered state. Those come only from the carrier, whose
API needs its own account and keys (see "Shipment tracking" in
docs/development.md). So fbc keeps the number and carrier and links to the
carrier's own tracking page, and never calls a carrier itself.
"""

from urllib.parse import quote

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
