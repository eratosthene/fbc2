"""
Ship This: finish off an eBay order once its shipping label is bought.

It reads the order's earnings from eBay's Finances API, records a Sales
Receipt for that amount, marks the order's Unit sold, takes the mailer,
sleeves and label it used out of the Supply List and removes that Unit's own
instance of the release from the Discogs collection.
"""

import logging

from fbc import devmode

logger = logging.getLogger()

FINANCES_URL = "https://apiz.ebay.com/sell/finances/v1/transaction"

# How each kind of Finances API transaction is shown in the earnings
# breakdown. Anything else is shown by its own name. A sale is shown before
# the fees eBay took out of it, which follow it as lines of their own.
TRANSACTION_LABELS = {
    "SALE": "Sale, less the taxes eBay collects",
    "SHIPPING_LABEL": "Shipping label",
    "REFUND": "Refund to the buyer",
    "NON_SALE_CHARGE": "Other fee",
    "CREDIT": "Credit",
    "DISPUTE": "Dispute",
    "ADJUSTMENT": "Adjustment",
}

# Transactions on an order that aren't earnings, which eBay's order page
# leaves out too. A TRANSFER is the seller paying eBay for a charge, like a
# label bought with a card on file: the charge is a line of its own.
NOT_EARNINGS = {"TRANSFER"}

# eBay's feeTypes, for fees taken out of a sale and charges of their own.
# Anything else is shown by its own name, made readable.
FEE_LABELS = {
    "FINAL_VALUE_FEE": "Final value fee",
    "FINAL_VALUE_FEE_FIXED_PER_ORDER": "Final value fee per order",
    "REGULATORY_OPERATING_FEE": "Regulatory operating fee",
    "INTERNATIONAL_FEE": "International fee",
    "AD_FEE": "Ad fee",
}


def fee_label(fee_type):
    if not fee_type:
        return "eBay fees"
    return FEE_LABELS.get(fee_type) or fee_type.replace("_", " ").capitalize()


def _cents(money):
    """An amount from eBay ({"value": "4.63"}) in whole cents."""
    return round(float((money or {}).get("value") or 0) * 100)


def _date(text):
    """eBay's 2026-10-28T15:30:00.000Z as a datetime, in UTC; None if not."""
    import datetime

    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError):
        return None


def transaction_lines(t):
    """
    The earnings lines for one Finances API transaction, as dicts of
    SalesReceiptLine's columns, credits above 0. A sale becomes the sale
    before fees and one line per kind of fee eBay took out of it. A refund
    becomes the whole refund and one line per fee eBay credited back, since
    eBay takes those credits off the refund's amount. Either way they add up
    to the transaction's amount.
    """
    from fbc.models.sales import FEE

    kind = t.get("transactionType")
    sign = -1 if t.get("bookingEntry") == "DEBIT" else 1
    cents = sign * _cents(t.get("amount"))
    base = {
        "kind": kind,
        "fee_type": t.get("feeType"),
        "ebay_order_id": t.get("orderId"),
        "transaction_id": t.get("transactionId"),
        "transaction_date": _date(t.get("transactionDate")),
    }
    if kind == "NON_SALE_CHARGE" and t.get("feeType"):
        label = fee_label(t["feeType"])
    else:
        label = TRANSACTION_LABELS.get(kind, kind)

    fees = {}
    if (kind, sign) in (("SALE", 1), ("REFUND", -1)):
        for item in t.get("orderLineItems") or []:
            for fee in item.get("marketplaceFees") or []:
                fee_type = fee.get("feeType")
                fees[fee_type] = fees.get(fee_type, 0) + abs(_cents(fee.get("amount")))
        # Whatever totalFeeAmount has that the per-fee list doesn't.
        rest = abs(_cents(t.get("totalFeeAmount"))) - sum(fees.values())
        if rest > 0:
            fees[None] = fees.get(None, 0) + rest
    fees = {fee_type: c for fee_type, c in fees.items() if c}

    # A sale's fees are taken off it, and a refund's are credited back.
    lines = [dict(base, label=label, amount=(cents + sign * sum(fees.values())) / 100)]
    for fee_type, c in fees.items():
        lines.append(
            dict(
                base,
                kind=FEE,
                fee_type=fee_type,
                label=fee_label(fee_type) + (" credited back" if sign < 0 else ""),
                amount=-sign * c / 100,
            )
        )
    return lines


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
      lines     the lines making up net, as dicts of SalesReceiptLine's
                columns (see transaction_lines)
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
    if devmode.on():
        result["warnings"].append(
            devmode.message("eBay earnings lookup") + " Enter Net Sold yourself."
        )
        return result
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
    sale = False
    label = False
    for t in transactions:
        kind = t.get("transactionType")
        if kind in NOT_EARNINGS:
            continue
        lines = transaction_lines(t)
        for line in lines:
            line["ebay_order_id"] = line["ebay_order_id"] or order_id
        result["lines"].extend(lines)
        if kind == "SALE":
            sale = True
            result["items"] += len(t.get("orderLineItems") or [])
        elif kind == "SHIPPING_LABEL":
            label = True

    # eBay lists the newest first; oldest first reads as what happened. A
    # transaction's fees share its date, so they stay under it.
    if all(line["transaction_date"] for line in result["lines"]):
        result["lines"].sort(key=lambda line: line["transaction_date"])

    if not sale:
        result["warnings"].append(
            "eBay doesn't show a sale for this order yet, so its earnings aren't"
            " known. Enter Net Sold from the order page on eBay."
        )
        return result
    result["net"] = round(sum(line["amount"] for line in result["lines"]), 2)
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
    so other copies of the same release are left alone. In dev mode it's
    only removed from fbc.
    """
    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    from fbc.discogs import DiscogsAuthError
    from fbc.util import forget_discogs_instance

    instance, problem = discogs_plan(unit)
    if problem:
        return problem, "warning"
    instance_id = instance.instance_id
    release_id = unit.discogs_release_id
    title = str(unit.discogs_release)
    if devmode.on():
        forget_discogs_instance(instance)
        return (
            devmode.message("Discogs removal")
            + " Removed "
            + title
            + " (instance "
            + str(instance_id)
            + ") from fbc only.",
            "warning",
        )
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
    # DiscogsAuthError: fbc isn't connected to Discogs, which is only found
    # out once the receipt is saved, so it's reported like any other failure.
    except (DiscogsAPIError, DiscogsAuthError, OSError) as e:
        logger.error(e)
        return (
            "Couldn't remove "
            + title
            + " (instance "
            + str(instance_id)
            + ") from Discogs: "
            + str(e).rstrip(".")
            + ". Remove it by hand, then Sync Discogs.",
            "danger",
        )
    forget_discogs_instance(instance)
    logger.info(
        "Removed Discogs instance " + str(instance_id) + " of " + str(release_id)
    )
    return message, "success"


def supplies_plan(units):
    """
    [(supply, count)] for every supply used for shipping: how many uses
    shipping `units` together normally takes (see Supply.shipping_count).
    """
    from fbc.listing import record_size
    from fbc.models.supplies import SHIPPING, Supply

    sizes = [record_size(u) for u in units]
    return [(s, s.shipping_count(sizes)) for s in Supply.used_for_list(SHIPPING)]


def supply_counts(form, supplies, prefix="supply-"):
    """
    {supply: count} from the `prefix`<id> fields of `form` for each of
    `supplies`, leaving out blanks and 0. Raises ValueError, with what to
    fix, for anything but a whole number of 0 or more.
    """
    counts = {}
    for supply in supplies:
        value = (form.get(prefix + str(supply.id)) or "").strip()
        if not value:
            continue
        try:
            count = int(value)
        except ValueError:
            count = -1
        if count < 0:
            raise ValueError(
                "Enter how many " + supply.name + " were used as a whole number."
            )
        if count:
            counts[supply] = count
    return counts


def stock_problems(counts):
    """
    What to fix for each of `counts` ({supply: count}) that uses more than
    the supply has left (see Supply.available), as messages linking to
    Reconcile Supplies; [] if none does. Stock can't really run below 0, so
    a shortfall means the Supply List is out of step with what's on hand.
    """
    from flask import url_for
    from markupsafe import Markup

    problems = []
    for supply, count in counts.items():
        left = supply.available()
        if count > left:
            link = url_for(
                "ReconcileSuppliesView.reconcile",
                supply=supply.id,
                _anchor="supply-" + str(supply.id),
            )
            problems.append(
                Markup(
                    "There aren't enough {0} in stock for this: it uses {1}, and"
                    " the Supply List has {2} left. Count what's really on hand"
                    ' and correct it on <a href="{3}">Reconcile Supplies</a>, or'
                    " change how many were used, then try again."
                ).format(supply.name, count, left, link)
            )
    return problems


def fmt_supplies(counts):
    """{supply: count} as "1 LP Mailers, 1 Shipping Label Roll"."""
    return ", ".join(str(n) + " " + s.name for s, n in counts.items())


def ship_order(order, unit, net_sold, supplies=None, lines=None):
    """
    Record the Sales Receipt for `order`, with its earnings `lines` (as
    order_earnings gives them) and a manual adjustment if they don't add up
    to `net_sold`. Mark `unit` sold with it and take `supplies` ({supply:
    count}) out of stock. Returns the receipt.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt
    from fbc.models.supplies import SHIPPING

    receipt = SalesReceipt(
        date=order.created_time.date(),
        ebay_order_id=order.order_id,
        sold_price=order.price,
        net_sold=net_sold,
    )
    receipt.set_lines(lines or [])
    db.session.add(receipt)
    db.session.flush()
    unit.sold = True
    unit.sales_receipt_id = receipt.id
    for supply, count in (supplies or {}).items():
        supply.use(count, SHIPPING, receipt=receipt)
    db.session.commit()
    logger.info(
        "Shipped order "
        + order.order_id
        + ": unit "
        + str(unit.id)
        + ", receipt "
        + str(receipt.id)
        + (", used " + fmt_supplies(supplies) if supplies else "")
    )
    return receipt


def backfill_earnings(
    apply=False, update_net=False, since=None, limit=None, echo=print
):
    """
    Give past Sales Receipts their earnings lines from eBay: every receipt
    linked to an eBay order that has no lines yet, newest first, from
    `since` (a date) on, at most `limit` of them. Only saves when `apply`.

    Where eBay's total differs from Net Sold (often a refund that wasn't
    recorded), Net Sold is kept and the difference becomes a manual
    adjustment line, unless `update_net`, which sets Net Sold to eBay's
    total. Receipts eBay has no sale for are left alone, as are receipts
    whose order is on other receipts too (see SalesReceipt.order_receipts).
    Stops at the first error from eBay, since the rest would fail the same
    way.

    Returns {"saved", "differ", "skipped", "shared"}: counts, with differ the
    receipts whose Net Sold didn't match.
    """
    from fbc import db
    from fbc.models.sales import MANUAL, SalesReceipt

    counts = {"saved": 0, "differ": 0, "skipped": 0, "shared": 0}
    if devmode.on():
        echo(devmode.message("Backfilling earnings from eBay"))
        return counts
    query = (
        db.session.query(SalesReceipt)
        .filter(SalesReceipt.ebay_order_id.isnot(None))
        .filter(~SalesReceipt.lines.any())
        .order_by(SalesReceipt.date.desc(), SalesReceipt.id.desc())
    )
    if since is not None:
        query = query.filter(SalesReceipt.date >= since)
    if limit:
        query = query.limit(limit)
    for receipt in query.all():
        name = (
            "Receipt "
            + str(receipt.id)
            + " ("
            + str(receipt.date)
            + ", order "
            + receipt.ebay_order_id
            + ")"
        )
        shared = receipt.order_receipts()
        if shared > 1:
            echo(
                name
                + ": its order is on "
                + str(shared)
                + " receipts, and eBay's earnings can't be split between them;"
                " left as it is."
            )
            counts["shared"] += 1
            continue
        earnings = order_earnings(receipt.ebay_order_id)
        if earnings["errors"]:
            for e in earnings["errors"]:
                echo(name + ": " + e)
            echo("Stopped; nothing more was asked of eBay.")
            break
        if earnings["net"] is None:
            echo(name + ": eBay has no sale for it; left as it is.")
            counts["skipped"] += 1
            continue
        difference = round(earnings["net"] - receipt.net_sold, 2)
        if difference:
            counts["differ"] += 1
            if update_net:
                what = (
                    "Net Sold "
                    + receipt.fmt_net_sold()
                    + " set to eBay's $"
                    + f"{earnings['net']:.2f}"
                )
                receipt.net_sold = earnings["net"]
            else:
                what = (
                    "eBay's total $"
                    + f"{earnings['net']:.2f}"
                    + " differs from Net Sold "
                    + receipt.fmt_net_sold()
                    + "; kept as a manual adjustment"
                )
        else:
            what = "matches Net Sold"
        receipt.set_lines(earnings["lines"])
        echo(
            name
            + ": "
            + str(len([line for line in receipt.lines if line.kind != MANUAL]))
            + " lines, "
            + what
            + "."
        )
        if apply:
            db.session.commit()
            counts["saved"] += 1
        else:
            db.session.rollback()
    if not apply:
        echo("Nothing was saved. Run it again with --apply to save these.")
    return counts
