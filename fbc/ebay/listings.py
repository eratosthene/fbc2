"""Saving eBay listings that a sync reads from eBay: new, relisted, renewed
and ended ones."""

import datetime
import logging


logger = logging.getLogger()


def add_ebay_listing(item):
    from fbc import db
    from fbc.models.ebay import EbayListing

    logger.debug(item)
    item_id = item["ItemID"]
    title = item["Title"]
    price = float(item["BuyItNowPrice"]["value"])
    url = item["ListingDetails"]["ViewItemURL"]
    d = datetime.datetime.strptime(
        item["ListingDetails"]["StartTime"], "%Y-%m-%dT%H:%M:%S.000Z"
    )
    # TODO: make this whole thing atomic instead of if/then
    ebay_listing = db.session.query(EbayListing).filter_by(item_id=item_id).first()
    if not ebay_listing:
        ebay_listing = EbayListing(
            item_id=item_id, title=title, price=price, url=url, start_time=d
        )
        db.session.add(ebay_listing)
        link_unit_by_sku(item)
    else:
        logger.debug("Listing already exists!")
    db.session.commit()
    db.session.refresh(ebay_listing)
    logger.debug(ebay_listing)


def link_unit_by_sku(item):
    """
    Listings published from fbc carry the Unit's id in their Custom label
    (SKU). Link such a listing to its Unit if the Unit has no listing yet.
    """
    from fbc import db
    from fbc.ebay.skus import unit_id_from_sku
    from fbc.models.inventory import Unit

    unit_id = unit_id_from_sku(item.get("SKU"))
    if unit_id is None:
        return
    unit = db.session.get(Unit, unit_id)
    if unit and not unit.ebay_listing_id:
        unit.ebay_listing_id = item["ItemID"]
        logger.info("Linked unit " + str(unit_id) + " to " + str(item["ItemID"]))


def find_relisted_listing(item, remote_item_ids):
    """
    Look for a local EbayListing that is actually the same physical item as
    `item`, just relisted under a new eBay ItemID.

    eBay gives a relisted item a brand new ItemID, so there is no ID to join
    on. The best signal we have is the listing title, which eBay carries
    over unchanged on a relist. A candidate old listing must:
      - have the same title as the new item
      - no longer be present in eBay's current active listings (an item that
        is still active can't be the "old" side of a relist)
      - still be linked to a Unit that hasn't sold (so it's genuinely
        pending relist, not a stale/ended listing with no inventory tie)

    Returns the matching EbayListing, or None if there isn't exactly one
    unambiguous match.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit

    candidates = (
        db.session.query(EbayListing)
        .filter_by(title=item["Title"])
        .filter(EbayListing.item_id != item["ItemID"])
        .filter(~EbayListing.item_id.in_(remote_item_ids))
        .all()
    )
    matches = [
        c
        for c in candidates
        if db.session.query(Unit)
        .filter_by(ebay_listing_id=c.item_id)
        .filter(Unit.sold.isnot(True))
        .first()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "Multiple possible old listings for relisted item "
            + str(item["ItemID"])
            + " ("
            + item["Title"]
            + "), skipping auto-relink"
        )
    return None


def relist_ebay_listing(old_listing, item):
    """
    Record a relisted item under its new ItemID, re-point every model that
    referenced the old listing at the new one, and remove the old listing.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing, EbayOrder, EbayOrderItem
    from fbc.models.inventory import Unit

    old_item_id = old_listing.item_id
    add_ebay_listing(item)
    new_item_id = item["ItemID"]

    # A mystery box stays one: it has no Units to find it by.
    new_listing = db.session.get(EbayListing, new_item_id)
    new_listing.pick_from_box_id = old_listing.pick_from_box_id
    new_listing.pick_count = old_listing.pick_count

    db.session.query(Unit).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.query(EbayOrder).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    # Ship This finds an order's Units through its items' listings.
    db.session.query(EbayOrderItem).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.commit()

    db.session.delete(old_listing)
    db.session.commit()
    logger.info("Relisted " + old_item_id + " -> " + new_item_id)


def save_renewals(items, fetched):
    """Save when each of eBay's active `items` renews, as read at `fetched`."""
    from fbc import db
    from fbc.ebay import insertions
    from fbc.models.ebay import EbayListing

    for item in items:
        listing = db.session.get(EbayListing, item["ItemID"])
        renews = insertions.renewal_time(item, fetched)
        if listing is not None and renews is not None:
            listing.renews_at = renews
    db.session.commit()


def mark_ended_listings(remote_item_ids, previous_sync=None):
    """
    Mark every listing that isn't in eBay's active list as ended, and clear
    the flag on one that is back in it. Only call this after reading the
    whole active list (api.fetch_active_items checks that it did), since
    anything missing from `remote_item_ids` is taken as ended.

    A listing's end_time is when the previous successful sync last saw it
    active, since it ended some time after that; it's start_time when there
    was no earlier sync, which counts the start and no renewals.
    Returns how many were marked ended.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing

    ended = 0
    for listing in db.session.query(EbayListing).all():
        if listing.item_id in remote_item_ids:
            if listing.ended:
                listing.ended = False
                listing.end_time = None
        elif not listing.ended:
            seen = previous_sync
            if seen is None or (listing.start_time and seen < listing.start_time):
                seen = listing.start_time
            listing.ended = True
            listing.end_time = seen
            ended += 1
    db.session.commit()
    return ended
