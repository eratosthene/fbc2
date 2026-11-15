"""Reading what eBay paid out for an order, from its Finances API: the
transactions on an order, its fees and earnings, and backfilling them."""

import logging

from fbc.core import devmode

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


# eBay holds a sale's money while a return request or a payment dispute is
# open, as a DISPUTE whose transactionId is DISPUTE_HOLD-<id>, and releases it
# as a CREDIT whose transactionId starts DISPUTE_HOLD_REVERSE-<id>.
HOLD_RELEASE = "DISPUTE_HOLD_REVERSE-"


def held(lines):
    """
    What eBay is still holding out of `lines` (order_earnings' lines): its
    disputes less the holds it has released. Below 0 while a return or
    dispute is open, and 0 once it's settled either way, unless the seller
    lost a dispute, when it stays below 0.
    """
    return round(
        sum(
            line["amount"]
            for line in lines
            if line["kind"] == "DISPUTE"
            or (line["transaction_id"] or "").startswith(HOLD_RELEASE)
        ),
        2,
    )


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
    elif kind == "SHIPPING_LABEL" and sign > 0:
        label = "Shipping label refunded"
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


def order_earnings(order_id):
    """shipment_earnings for one order."""
    return shipment_earnings([order_id])


def shipment_earnings(order_ids):
    """
    What the seller keeps from orders shipped together, as eBay's order
    page shows it for each under Order earnings: every money movement eBay
    has tied to the orders (the sale less fees and taxes, the shipping
    label, refunds...) added up.

    Returns a dict with:
      net       the earnings, or None if eBay has no sale for one of the
                orders yet
      lines     the lines making up net, as dicts of SalesReceiptLine's
                columns (see transaction_lines), each with its order
      items     how many line items the sales cover (0 if eBay didn't say)
      labels    how many shipping labels were bought on the orders
      errors    why eBay couldn't be asked
      warnings  things to check before trusting net
      unsettled whether eBay is holding money while a return or dispute is
                open (see held), so net will change once it closes
    """
    import requests

    from fbc.ebay.oauth import (
        FINANCES_SCOPE,
        OAuthError,
        _rest_errors,
        forget_access_token,
        oauth_token,
    )

    several = len(order_ids) > 1
    result = {
        "net": None,
        "lines": [],
        "items": 0,
        "labels": 0,
        "errors": [],
        "warnings": [],
        "unsettled": False,
    }
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
    no_sale = []
    for order_id in order_ids:
        which = " for order " + order_id if several else ""
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
                "Couldn't reach eBay for the order's earnings" + which + ": " + str(e)
            )
            return result
        if r.status_code != 200:
            if r.status_code == 401:
                forget_access_token(FINANCES_SCOPE)
            result["errors"].extend(
                "Getting the order's earnings" + which + ": " + m
                for m in _rest_errors(r)
            )
            return result

        sale = False
        for t in r.json().get("transactions") or []:
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
            elif kind == "SHIPPING_LABEL" and t.get("bookingEntry") == "DEBIT":
                result["labels"] += 1
        if not sale:
            no_sale.append(order_id)

    # eBay lists the newest first; oldest first reads as what happened. A
    # transaction's fees share its date, so they stay under it.
    if all(line["transaction_date"] for line in result["lines"]):
        result["lines"].sort(key=lambda line: line["transaction_date"])

    if no_sale:
        result["warnings"].append(
            (
                "eBay doesn't show a sale for order " + ", ".join(no_sale)
                if several
                else "eBay doesn't show a sale for this order"
            )
            + " yet, so its earnings aren't known. Enter Net Sold from the"
            " order page on eBay."
        )
        return result
    result["net"] = round(sum(line["amount"] for line in result["lines"]), 2)
    holding = held(result["lines"])
    if holding:
        result["unsettled"] = True
        result["warnings"].append(
            "eBay is holding $"
            + f"{-holding:.2f}"
            + (" of these orders' money" if several else " of this order's money")
            + ", as it does while a return request or a"
            " payment dispute is open. These earnings will change when it"
            " closes: if the buyer never sends the item back, eBay releases the"
            " money and doesn't charge for an unused return label. Refresh"
            " again once it's closed. If it already has, and you lost a"
            " dispute, save as usual."
        )
    if not result["labels"]:
        result["warnings"].append(
            (
                "There's no eBay shipping label on any of these orders yet"
                if several
                else "There's no eBay shipping label on this order yet"
            )
            + ", so these earnings don't take one off. Buy the label first and"
            " reload this page, or enter Net Sold yourself."
        )
    elif several and result["labels"] > 1:
        result["warnings"].append(
            "There's a shipping label on more than one of these orders. If"
            " they went in one package, check a label wasn't bought twice."
        )
    return result


def backfill_earnings(
    apply=False, update_net=False, since=None, limit=None, echo=print
):
    """
    Give past Sales Receipts their earnings lines from eBay: every receipt
    linked to eBay orders that has no lines yet, newest first, from
    `since` (a date) on, at most `limit` of them. Only saves when `apply`.

    Where eBay's total differs from Net Sold (often a refund that wasn't
    recorded), Net Sold is kept and the difference becomes a manual
    adjustment line, unless `update_net`, which sets Net Sold to eBay's
    total. Receipts eBay has no sale for are left alone, as are receipts
    whose order is on other receipts too (see SalesReceipt.order_receipts),
    and receipts whose order eBay is holding money on (see held), until a
    later run.
    Stops at the first error from eBay, since the rest would fail the same
    way.

    Returns {"saved", "differ", "skipped", "shared", "unsettled"}: counts,
    with differ the receipts whose Net Sold didn't match.
    """
    from fbc import db
    from fbc.models.sales import MANUAL, SalesReceipt

    counts = {"saved": 0, "differ": 0, "skipped": 0, "shared": 0, "unsettled": 0}
    if devmode.on():
        echo(devmode.message("Backfilling earnings from eBay"))
        return counts
    query = (
        db.session.query(SalesReceipt)
        .filter(SalesReceipt.ebay_orders.any())
        .filter(~SalesReceipt.lines.any())
        .order_by(SalesReceipt.date.desc(), SalesReceipt.id.desc())
    )
    if since is not None:
        query = query.filter(SalesReceipt.date >= since)
    if limit:
        query = query.limit(limit)
    for receipt in query.all():
        order_ids = receipt.order_ids()
        name = (
            "Receipt "
            + str(receipt.id)
            + " ("
            + str(receipt.date)
            + (", orders " if len(order_ids) > 1 else ", order ")
            + ", ".join(order_ids)
            + ")"
        )
        shared = receipt.order_receipts()
        if shared > 1:
            echo(
                name
                + ": its order is on "
                + str(shared)
                + " receipts, and eBay's earnings can't be split between them;"
                " combine them first (combine-receipts). Left as it is."
            )
            counts["shared"] += 1
            continue
        earnings = shipment_earnings(order_ids)
        if earnings["errors"]:
            for e in earnings["errors"]:
                echo(name + ": " + e)
            echo("Stopped; nothing more was asked of eBay.")
            break
        if earnings["net"] is None:
            echo(name + ": eBay has no sale for it; left as it is.")
            counts["skipped"] += 1
            continue
        if earnings["unsettled"]:
            # Left without lines, so the next run picks it up again.
            echo(
                name
                + ": eBay is holding $"
                + f"{-held(earnings['lines']):.2f}"
                + " while a return or dispute is open; left until it closes."
            )
            counts["unsettled"] += 1
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
