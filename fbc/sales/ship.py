"""
Ship This: finish off an eBay order once its shipping label is bought.

It reads the order's earnings from eBay's Finances API (see fbc.ebay.finances),
records a Sales Receipt for that amount, marks the order's Unit sold, takes the
mailer, sleeves and label it used out of the Supply List and removes that
Unit's own instance of the release from the Discogs collection.

An order for an Other Item (see fbc/models/other.py) is shipped the same way,
with no Units and no Discogs step: the receipt marks the Item sold, and the
supplies are counted by hand, since a record's size doesn't apply.
"""

import logging

from fbc.sales import stock

logger = logging.getLogger()


def order_receipt(order):
    """The Sales Receipt already recorded for `order`, or None."""
    return order.sales_receipts[0] if order.sales_receipts else None


def shipment_units(orders):
    """
    The Units shipping `orders` together sells, and []; or None and what
    stops Ship This finding them, which is left to be done by hand. Each
    listing on the orders needs exactly as many unsold Units on it as were
    bought. A mystery box's Units are picked at Ship This (see fbc/ebay/mystery.py),
    so it adds none here and is no problem.
    """
    from fbc import db
    from fbc.ebay import mystery
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


def other_items(order):
    """
    (the Other Items `order` sold, problems). ([], []) when none of the order's
    listings is an Other Item's, so it's an order of records. Otherwise
    problems say what stops Ship This, which is left to be done by hand: the
    order mixes records and Other Items, or an Item is already sold.
    """
    from fbc import db
    from fbc.models.other import OtherItem

    listings = order.listing_ids()
    if not listings:
        return [], []
    found = (
        db.session.query(OtherItem)
        .filter(OtherItem.ebay_listing_id.in_(list(listings)))
        .order_by(OtherItem.id)
        .all()
    )
    if not found:
        return [], []
    problems = []
    if len(found) < len(listings):
        problems.append(
            "Order "
            + order.order_id
            + " has records and Other Items together, which Ship This can't"
            " record in one Sales Receipt."
        )
    for item in found:
        if item.sold or item.sales_receipt_id:
            problems.append(item.name + " is already sold.")
    return found, problems


def stub_listing_id(order):
    """The id of the listing made for an order that has none, "o" and its id."""
    return ("o" + order.order_id)[:20]


def other_item_candidates(order):
    """
    [(listing id, name)] for the listings of `order` that Ship This could make
    Other Items of, when the order is clearly not for records: none of its
    listings has a Unit, an eBay Draft or is a mystery box. A listing fbc no
    longer has (an old one) counts: make_other_items brings it back. So does an
    order that doesn't know its listing at all. [] for any other order.
    """
    from fbc import db
    from fbc.ebay import sorting
    from fbc.models.ebay import EbayListing

    listings = order.listing_ids()
    if not listings:
        # Orders from before fbc kept items, whose listing is long gone, know
        # nothing of it. Nothing links them to a Unit, so they can't be a
        # record's; the listing made for them is keyed by the order.
        return [(stub_listing_id(order), order.title or order.order_id)]
    titles = {i.ebay_listing_id: i.title for i in order.items}
    found = []
    for listing_id in listings:
        listing = db.session.get(EbayListing, listing_id)
        if listing is not None and sorting.is_record_listing(listing):
            return []
        title = (
            titles.get(listing_id)
            or (order.title if len(listings) == 1 else None)
            or (listing.title if listing else None)
        )
        found.append((listing_id, title or listing_id))
    return found


def make_other_items(order, candidates):
    """
    Make an Other Item for each of `candidates` (see other_item_candidates). A
    listing fbc doesn't have is added, ended and archived, as it was for an old
    order: only what the order says (title, price, date) is known.
    """

    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.other import OtherItem

    prices = {i.ebay_listing_id: i.price for i in order.items}
    unlinked = not order.listing_ids()
    items = []
    for listing_id, name in candidates:
        if db.session.get(EbayListing, listing_id) is None:
            when = order.created_time
            db.session.add(
                EbayListing(
                    item_id=listing_id,
                    title=name[:100],
                    price=prices.get(listing_id) or order.price,
                    url="https://www.ebay.com/itm/" + listing_id,
                    start_time=when,
                    # Ended when it was ordered, so it counts in no month's
                    # free insertions now.
                    ended=True,
                    end_time=when,
                    archived=True,
                )
            )
            db.session.flush()
        if unlinked:
            # Ship This finds an order's Items through its listings.
            order.ebay_listing_id = listing_id
        items.append(OtherItem(name=name[:100], ebay_listing_id=listing_id, sold=False))
    db.session.add_all(items)
    db.session.commit()
    return items


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


def ship_order(
    orders,
    units,
    net_sold,
    supplies=None,
    lines=None,
    unit_prices=None,
    other_items=None,
):
    """
    Record one Sales Receipt for `orders` (one EbayOrder or several shipped
    together), dated the first of them, with its earnings `lines` (as
    shipment_earnings gives them) and a manual adjustment if they don't add
    up to `net_sold`. Mark `units` (one Unit or several) sold with it and
    take `supplies` ({supply: count}) out of stock. `unit_prices` ({unit id:
    price}) is what Units picked for a mystery box sold for, as their order
    items can't say. `other_items` are Other Items the order sold (with no
    Units): they're marked sold with the receipt. Returns the receipt.
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
    for item in other_items or []:
        item.sold = True
        item.sales_receipt_id = receipt.id
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
        + (", used " + stock.fmt_supplies(supplies) if supplies else "")
    )
    return receipt


# Ship This for a Discogs order. A Discogs order is shipped whole: it can
# hold several items, but orders aren't combined, since Discogs already puts a
# buyer's items in one order.


def discogs_shipment_units(order):
    """
    The Units a Discogs `order` sold, in item order, and []; or None and what
    stops Ship This finding them, which is left to be done by hand. Every
    item must be linked to a Unit that isn't sold yet.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    if not order.items:
        return None, ["Order " + order.order_id + " has no items in fbc."]
    units = []
    problems = []
    for item in order.items:
        unit = db.session.get(Unit, item.unit_id) if item.unit_id else None
        if unit is None:
            problems.append(
                (item.title or "An item")
                + " isn't linked to a Unit: its Discogs listing's Private"
                " Comments aren't an fbc ID, or the listings sync hasn't seen it."
            )
        elif unit.sold or unit.sales_receipt_id:
            problems.append(unit.name + " is already sold.")
        elif unit in units:
            problems.append(unit.name + " is on this order twice.")
        else:
            units.append(unit)
    if problems:
        return None, problems
    return units, []


def discogs_earnings(order):
    """
    The earnings lines for a Discogs `order`, as SalesReceiptLine's columns,
    and what they add up to (None with no lines). Discogs's API gives the
    order's total and its selling fee, and no payment processing fee or
    shipping label cost, so these are the sale and the fee only. The
    owner corrects Net Sold to what was really kept, and the difference
    becomes a manual adjustment line.
    """
    from fbc.models.sales import DISCOGS_FEE, FEE

    if order.total is None:
        return [], None
    lines = [
        {
            "kind": "SALE",
            "label": "Sale, with shipping",
            "amount": round(order.total, 2),
            "transaction_date": order.created_time,
        }
    ]
    if order.fee:
        lines.append(
            {
                "kind": FEE,
                "fee_type": DISCOGS_FEE,
                "label": "Discogs selling fee",
                "amount": -round(order.fee, 2),
                "transaction_date": order.created_time,
            }
        )
    return lines, round(sum(line["amount"] for line in lines), 2)


def ship_discogs_order(order, units, net_sold, supplies=None, lines=None):
    """
    Record a Sales Receipt for a Discogs `order`, dated when it was placed,
    with its earnings `lines` (see discogs_earnings) and a manual adjustment
    if they don't add up to `net_sold`. Mark `units` sold with it, each at
    the price of its item when there are several, and take `supplies`
    ({supply: count}) out of stock. Returns the receipt.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt
    from fbc.models.supplies import SHIPPING

    prices = {i.unit_id: i.price for i in order.items if i.unit_id}
    sold_price = round(sum(i.price or 0 for i in order.items), 2)
    receipt = SalesReceipt(
        date=order.created_time.date(),
        discogs_orders=[order],
        sold_price=sold_price,
        net_sold=net_sold,
    )
    receipt.set_lines(lines or [])
    db.session.add(receipt)
    db.session.flush()
    for unit in units:
        unit.sold = True
        unit.sales_receipt_id = receipt.id
        if len(units) > 1:
            unit.sold_price = prices.get(unit.id)
    for supply, count in (supplies or {}).items():
        supply.use(count, SHIPPING, receipt=receipt)
    db.session.commit()
    logger.info(
        "Shipped Discogs order "
        + order.order_id
        + ": unit"
        + ("s " if len(units) > 1 else " ")
        + ", ".join(str(u.id) for u in units)
        + ", receipt "
        + str(receipt.id)
        + (", used " + stock.fmt_supplies(supplies) if supplies else "")
    )
    return receipt


def other_marketplace_notes(units, here):
    """
    Notes (text, link or None) for Units of a shipment that are still listed
    on the other marketplace than `here` ("eBay" or "Discogs"), or whose
    ending there failed. fbc ends those listings when an orders sync sees the
    sale, so this is for a sale not synced yet, or a refusal.
    """
    from flask import url_for

    from fbc import db
    from fbc.discogs import listing as discogs_listing
    from fbc.models.ebay import EbayDraft
    from fbc.models.sales import FAILED, ListingEnd

    notes = []
    for unit in units:
        if here == "eBay":
            listing = discogs_listing.open_listing(unit)
            if listing:
                notes.append(
                    (
                        unit.name
                        + " still has a Discogs listing ("
                        + str(listing.listing_id)
                        + ", "
                        + str(listing.status)
                        + "). End it on Discogs before it sells twice.",
                        listing.url,
                    )
                )
        else:
            ebay = unit.ebay_listing
            if ebay and not ebay.ended:
                notes.append(
                    (
                        unit.name
                        + " still has an active eBay listing ("
                        + ebay.item_id
                        + "). End it on eBay before it sells twice.",
                        ebay.url,
                    )
                )
            draft = (
                db.session.query(EbayDraft)
                .filter_by(unit_id=unit.id, status="draft")
                .first()
            )
            if draft:
                notes.append(
                    (
                        unit.name + " still has an unpublished eBay Draft.",
                        url_for("EbayDraftModelView.show", pk=draft.id),
                    )
                )
        failed = (
            db.session.query(ListingEnd).filter_by(unit_id=unit.id, status=FAILED).all()
        )
        for row in failed:
            notes.append(
                (
                    "fbc couldn't end "
                    + row.ended_on
                    + " "
                    + row.listing_ref
                    + ": "
                    + (row.message or ""),
                    None,
                )
            )
    return notes
