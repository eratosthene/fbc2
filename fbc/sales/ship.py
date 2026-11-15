"""
Ship This: finish off an eBay order once its shipping label is bought.

It reads the order's earnings from eBay's Finances API (see fbc.ebay.finances),
records a Sales Receipt for that amount, marks the order's Unit sold, takes the
mailer, sleeves and label it used out of the Supply List and removes that
Unit's own instance of the release from the Discogs collection.
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
        + (", used " + stock.fmt_supplies(supplies) if supplies else "")
    )
    return receipt
