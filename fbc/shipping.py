"""
Ship This: finish off an eBay order once its shipping label is bought.

It reads the order's earnings from eBay's Finances API, records a Sales
Receipt for that amount, marks the order's Unit sold and removes that Unit's
own instance of the release from the Discogs collection.
"""

import logging

logger = logging.getLogger()

FINANCES_URL = "https://apiz.ebay.com/sell/finances/v1/transaction"

# How each kind of Finances API transaction is shown in the earnings
# breakdown. Anything else is shown by its own name.
TRANSACTION_LABELS = {
    "SALE": "Sale, less eBay's fees and taxes",
    "SHIPPING_LABEL": "Shipping label",
    "REFUND": "Refund",
    "NON_SALE_CHARGE": "Other fee",
    "CREDIT": "Credit",
    "DISPUTE": "Dispute",
    "ADJUSTMENT": "Adjustment",
}


def order_receipt(order):
    """The Sales Receipt already recorded for `order`, or None."""
    from fbc import db
    from fbc.models.sales import SalesReceipt

    return (
        db.session.query(SalesReceipt).filter_by(ebay_order_id=order.order_id).first()
    )


def order_unit(order):
    """
    The Unit `order` sold: the only Unit on the order's listing, as long as
    it isn't sold yet. None for anything less clear-cut, which is left to be
    done by hand.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    if not order.ebay_listing_id:
        return None
    units = (
        db.session.query(Unit).filter_by(ebay_listing_id=order.ebay_listing_id).all()
    )
    if len(units) != 1:
        return None
    unit = units[0]
    if unit.sold or unit.sales_receipt_id:
        return None
    return unit


def can_ship(order):
    """Whether Ship This is offered for `order`."""
    return order_receipt(order) is None and order_unit(order) is not None


def order_earnings(order_id):
    """
    What the seller keeps from an order, as eBay's order page shows it under
    Order earnings: every money movement eBay has tied to the order (the sale
    less fees and taxes, the shipping label, refunds...) added up.

    Returns a dict with:
      net       the earnings, or None if eBay has no sale for the order yet
      lines     [{"label", "amount"}] making up net, credits positive
      items     how many line items the sale covers (0 if eBay didn't say)
      errors    why eBay couldn't be asked
      warnings  things to check before trusting net
    """
    import requests

    from fbc.listing import (
        FINANCES_SCOPE,
        OAuthError,
        _rest_errors,
        forget_access_token,
        oauth_token,
    )

    result = {"net": None, "lines": [], "items": 0, "errors": [], "warnings": []}
    try:
        token = oauth_token(FINANCES_SCOPE)
    except OAuthError as e:
        result["errors"].append(str(e))
        return result
    try:
        r = requests.get(
            FINANCES_URL,
            headers={
                "Authorization": "Bearer " + token,
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={"filter": "orderId:{" + order_id + "}", "limit": 200},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error(e)
        result["errors"].append(
            "Couldn't reach eBay for the order's earnings: " + str(e)
        )
        return result
    if r.status_code != 200:
        if r.status_code == 401:
            forget_access_token(FINANCES_SCOPE)
        result["errors"].extend(
            "Getting the order's earnings: " + m for m in _rest_errors(r)
        )
        return result

    transactions = r.json().get("transactions") or []
    net = 0.0
    sale = False
    label = False
    for t in transactions:
        kind = t.get("transactionType")
        amount = float((t.get("amount") or {}).get("value") or 0)
        if t.get("bookingEntry") == "DEBIT":
            amount = -amount
        net += amount
        result["lines"].append(
            {"label": TRANSACTION_LABELS.get(kind, kind), "amount": round(amount, 2)}
        )
        if kind == "SALE":
            sale = True
            result["items"] += len(t.get("orderLineItems") or [])
        elif kind == "SHIPPING_LABEL":
            label = True

    if not sale:
        result["warnings"].append(
            "eBay doesn't show a sale for this order yet, so its earnings aren't"
            " known. Enter Net Sold from the order page on eBay."
        )
        return result
    result["net"] = round(net, 2)
    if not label:
        result["warnings"].append(
            "There's no eBay shipping label on this order yet, so these earnings"
            " don't take one off. Buy the label first and reload this page, or"
            " enter Net Sold yourself."
        )
    return result


def discogs_plan(unit):
    """
    The DiscogsInstance shipping `unit` removes from the collection, and
    None; or None and why nothing will be removed. Only the instance the
    Unit is linked to is ever removed, never another copy of the release.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    instance = unit.discogs_instance
    if not instance:
        return None, (
            "This Unit isn't linked to a Discogs instance, so nothing will be"
            " removed from Discogs."
        )
    release = unit.discogs_release
    if not release or instance not in release.discogs_instances:
        return None, (
            "This Unit's Discogs instance "
            + str(instance.instance_id)
            + " isn't one of its release's instances in fbc, so it won't be"
            " removed; remove it from Discogs by hand."
        )
    others = (
        db.session.query(Unit)
        .filter_by(discogs_instance_id=instance.instance_id)
        .filter(Unit.id != unit.id)
        .count()
    )
    if others:
        return None, (
            "Discogs instance "
            + str(instance.instance_id)
            + " is linked to other Units too, so it won't be removed; remove it"
            " from Discogs by hand."
        )
    return instance, None


def _discogs():
    from fbc import discogs

    return discogs.client()


def remove_from_discogs(unit):
    """
    Delete `unit`'s own instance of its release from the Discogs collection,
    then from fbc. Returns (message, category) to flash.

    The instance is looked up on Discogs first, to find the folder it's in
    and to make sure it's still there; the delete names that one instance,
    so other copies of the same release are left alone.
    """
    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    from fbc.util import forget_discogs_instance

    instance, problem = discogs_plan(unit)
    if problem:
        return problem, "warning"
    instance_id = instance.instance_id
    release_id = unit.discogs_release_id
    title = str(unit.discogs_release)
    try:
        client = _discogs()
        me = client.identity()
        try:
            found = [
                i
                for i in me.collection_items(release_id)
                if i.instance_id == instance_id
            ]
        except HTTPError as e:
            # Discogs answers 404 when no copy of the release is left.
            if e.status_code != 404:
                raise
            found = []
        if found:
            client._delete(
                me.fetch("resource_url")
                + "/collection/folders/"
                + str(found[0].folder_id)
                + "/releases/"
                + str(release_id)
                + "/instances/"
                + str(instance_id)
            )
            message = (
                "Removed "
                + title
                + " (instance "
                + str(instance_id)
                + ") from Discogs."
            )
        else:
            message = (
                title
                + " (instance "
                + str(instance_id)
                + ") was already gone from Discogs; removed it from fbc too."
            )
    except (DiscogsAPIError, OSError) as e:
        logger.error(e)
        return (
            "Couldn't remove "
            + title
            + " (instance "
            + str(instance_id)
            + ") from Discogs: "
            + str(e)
            + ". Remove it by hand, then Sync Discogs.",
            "danger",
        )
    forget_discogs_instance(instance)
    logger.info(
        "Removed Discogs instance " + str(instance_id) + " of " + str(release_id)
    )
    return message, "success"


def ship_order(order, unit, net_sold):
    """
    Record the Sales Receipt for `order` and mark `unit` sold with it.
    Returns the receipt.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt

    receipt = SalesReceipt(
        date=order.created_time.date(),
        ebay_order_id=order.order_id,
        sold_price=order.price,
        net_sold=net_sold,
    )
    db.session.add(receipt)
    db.session.flush()
    unit.sold = True
    unit.sales_receipt_id = receipt.id
    db.session.commit()
    logger.info(
        "Shipped order "
        + order.order_id
        + ": unit "
        + str(unit.id)
        + ", receipt "
        + str(receipt.id)
    )
    return receipt
