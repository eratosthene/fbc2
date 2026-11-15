"""Saving eBay orders that a sync reads from eBay, and their tracking."""

import datetime
import logging

from fbc.ebay import tracking

logger = logging.getLogger()


def _money(amount):
    """An amount from the Trading API ({"value": "4.37"}) as a float, or None."""
    try:
        return float(amount["value"])
    except (KeyError, TypeError, ValueError):
        return None


def order_items(item):
    """EbayOrderItems for every transaction (item bought) in a GetOrders order."""
    from fbc.models.ebay import EbayOrderItem

    transactions = (item.get("TransactionArray") or {}).get("Transaction") or []
    # ebaysdk gives a lone transaction as a dict rather than a list of one.
    if isinstance(transactions, dict):
        transactions = [transactions]
    items = []
    for t in transactions:
        try:
            quantity = int(t.get("QuantityPurchased") or 1)
        except ValueError:
            quantity = 1
        items.append(
            EbayOrderItem(
                ebay_listing_id=t["Item"]["ItemID"],
                title=(t["Item"].get("Title") or "")[:100],
                quantity=quantity,
                price=_money(t.get("TransactionPrice")),
            )
        )
    return items


def update_tracking(order, item):
    """Keep an order's tracking as eBay has it; a blank never wipes it."""
    carrier, number = tracking.from_order(item)
    if number:
        order.shipping_carrier = carrier
        order.tracking_number = number


def add_ebay_order(item):
    """
    Add a GetOrders order, with every item on it, if its first item's
    listing is in fbc. An order fbc already has only gets the items and
    shipping it was synced without, from before fbc kept them.
    """
    from fbc import db

    logger.debug(item)
    order_id = item["OrderID"]
    price = float(item["Subtotal"]["value"])
    buyer = item["BuyerUserID"]
    created_time = datetime.datetime.strptime(
        item["CreatedTime"], "%Y-%m-%dT%H:%M:%S.000Z"
    )
    items = order_items(item)
    carrier, tracking_number = tracking.from_order(item)
    title = items[0].title
    ebay_listing_id = items[0].ebay_listing_id
    shipping = _money(
        (item.get("ShippingServiceSelected") or {}).get("ShippingServiceCost")
    )
    from fbc.models.ebay import EbayListing
    from fbc.models.ebay import EbayOrder

    ebay_listing = (
        db.session.query(EbayListing).filter_by(item_id=ebay_listing_id).first()
    )
    if not ebay_listing:
        logger.warn("No eBay listing in DB for " + str(ebay_listing_id))
        # TODO: should we make orders for things without listings?
    else:
        logger.info("Found eBay listing " + str(ebay_listing))
        # TODO: make this whole thing atomic instead of if/then
        ebay_order = db.session.query(EbayOrder).filter_by(order_id=order_id).first()
        if not ebay_order:
            ebay_order = EbayOrder(
                order_id=order_id,
                price=price,
                shipping=shipping,
                shipping_carrier=carrier,
                tracking_number=tracking_number,
                buyer=buyer,
                created_time=created_time,
                title=title,
                ebay_listing_id=ebay_listing.item_id,
                items=items,
            )
            db.session.add(ebay_order)
        else:
            logger.debug("Order already exists!")
            if not ebay_order.items:
                ebay_order.items = items
            if ebay_order.shipping is None:
                ebay_order.shipping = shipping
            update_tracking(ebay_order, item)
        db.session.commit()
        db.session.refresh(ebay_order)
        logger.debug(ebay_order)
