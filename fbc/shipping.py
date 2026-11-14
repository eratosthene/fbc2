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


def order_receipt(order):
    """The Sales Receipt already recorded for `order`, or None."""
    return order.sales_receipts[0] if order.sales_receipts else None


def shipment_units(orders):
    """
    The Units shipping `orders` together sells, and []; or None and what
    stops Ship This finding them, which is left to be done by hand. Each
    listing on the orders needs exactly as many unsold Units on it as were
    bought. A mystery box's Units are picked at Ship This (see fbc/mystery.py),
    so it adds none here and is no problem.
    """
    from fbc import db, mystery
    from fbc.models.inventory import Unit

    wanted = {}
    for order in orders:
        listings = order.listing_ids()
        if not listings:
            return None, ["Order " + order.order_id + " isn't linked to a listing."]
        for listing_id, quantity in listings.items():
            wanted[listing_id] = wanted.get(listing_id, 0) + quantity
    units = []
    problems = []
    boxes = mystery.box_listings(wanted)
    for listing_id, quantity in wanted.items():
        if listing_id in boxes:
            continue
        unsold = (
            db.session.query(Unit)
            .filter_by(ebay_listing_id=listing_id, sold=False, sales_receipt_id=None)
            .order_by(Unit.id)
            .all()
        )
        if len(unsold) != quantity:
            problems.append(
                "Listing "
                + listing_id
                + " has "
                + str(len(unsold))
                + " unsold Unit"
                + ("" if len(unsold) == 1 else "s")
                + " on it, and "
                + str(quantity)
                + " "
                + ("was" if quantity == 1 else "were")
                + " bought."
            )
        units.extend(unsold)
    if problems:
        return None, problems
    return units, []


def order_unit(order):
    """
    The Unit `order` sold, when it's an order of one Unit and Ship This can
    find it; None for anything else.
    """
    units, _ = shipment_units([order])
    if units and len(units) == 1:
        return units[0]
    return None


# How far apart a buyer's orders can be and still be offered for shipping
# together. Ship This lets you choose which go in the package.
COMBINE_DAYS = 7


def combinable_orders(order):
    """
    The buyer's other orders that could go in the same package as `order`:
    placed within COMBINE_DAYS of it, with no Sales Receipt yet, and that
    Ship This can find the Units for. Oldest first.
    """
    import datetime

    from fbc import db
    from fbc.models.ebay import EbayOrder

    if not order.buyer or not order.created_time:
        return []
    window = datetime.timedelta(days=COMBINE_DAYS)
    others = (
        db.session.query(EbayOrder)
        .filter(EbayOrder.buyer == order.buyer)
        .filter(EbayOrder.order_id != order.order_id)
        .filter(EbayOrder.created_time >= order.created_time - window)
        .filter(EbayOrder.created_time <= order.created_time + window)
        .filter(~EbayOrder.sales_receipts.any())
        .order_by(EbayOrder.created_time, EbayOrder.order_id)
        .all()
    )
    return [o for o in others if shipment_units([o])[0] is not None]


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

    from fbc.listing import (
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


def combined_discount(orders, earnings):
    """
    The combined-shipping saving to pass on to the buyer of `orders`, when
    they paid shipping on each order and went in one package: what they paid
    for shipping less the label's cost and any refund already given, as
    {"paid", "label", "refunded", "discount"}. None when there's nothing to
    work out: one order, shipping fbc doesn't know, or no label yet.
    """
    if len(orders) < 2 or any(o.shipping is None for o in orders):
        return None
    if not earnings["labels"]:
        return None
    paid = round(sum(o.shipping for o in orders), 2)
    label = round(
        -sum(
            line["amount"]
            for line in earnings["lines"]
            if line["kind"] == "SHIPPING_LABEL"
        ),
        2,
    )
    refunded = round(
        -sum(line["amount"] for line in earnings["lines"] if line["kind"] == "REFUND"),
        2,
    )
    return {
        "paid": paid,
        "label": label,
        "refunded": refunded,
        "discount": round(max(paid - label - refunded, 0), 2),
    }


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


def remove_from_discogs(unit):
    """
    Delete `unit`'s own instance of its release from the Discogs collection,
    then from fbc. Returns (message, category) to flash.

    The instance is looked up on Discogs first, to find the folder it's in
    and to make sure it's still there; the delete names that one instance,
    so other copies of the same release are left alone. In dev mode it's
    only removed from fbc.
    """
    from fbc import discogs_client
    from fbc.discogs_client import ApiError, DiscogsAuthError
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
        account = discogs_client.account()
        found = [
            i
            for i in account.collection_items(release_id)
            if i["instance_id"] == instance_id
        ]
        if found:
            account.delete_collection_instance(
                found[0]["folder_id"], release_id, instance_id
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
    except (ApiError, DiscogsAuthError, OSError) as e:
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


def ship_order(orders, units, net_sold, supplies=None, lines=None, unit_prices=None):
    """
    Record one Sales Receipt for `orders` (one EbayOrder or several shipped
    together), dated the first of them, with its earnings `lines` (as
    shipment_earnings gives them) and a manual adjustment if they don't add
    up to `net_sold`. Mark `units` (one Unit or several) sold with it and
    take `supplies` ({supply: count}) out of stock. `unit_prices` ({unit id:
    price}) is what Units picked for a mystery box sold for, as their order
    items can't say. Returns the receipt.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt, item_prices
    from fbc.models.supplies import SHIPPING

    if not isinstance(orders, (list, tuple)):
        orders = [orders]
    if not isinstance(units, (list, tuple)):
        units = [units]
    receipt = SalesReceipt(
        date=min(o.created_time for o in orders).date(),
        ebay_orders=list(orders),
        sold_price=round(sum(o.price for o in orders), 2),
        net_sold=net_sold,
    )
    receipt.set_lines(lines or [])
    db.session.add(receipt)
    db.session.flush()
    prices = item_prices(orders)
    for unit in units:
        unit.sold = True
        unit.sales_receipt_id = receipt.id
        if unit_prices and unit.id in unit_prices:
            unit.sold_price = unit_prices[unit.id]
        elif len(units) > 1:
            unit.sold_price = prices.get(unit.ebay_listing_id)
    for supply, count in (supplies or {}).items():
        supply.use(count, SHIPPING, receipt=receipt)
    db.session.commit()
    logger.info(
        "Shipped order"
        + ("s " if len(orders) > 1 else " ")
        + ", ".join(o.order_id for o in orders)
        + ": unit"
        + ("s " if len(units) > 1 else " ")
        + ", ".join(str(u.id) for u in units)
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
