"""
Sorting eBay listings into records and Other Items, by eBay's category.

A listing in Music > Vinyl Records is a record's, and gets its Unit as before.
A listing in any other category is something else the owner sells (see
fbc/models/other.py), so it gets an Other Item, which stays out of the
record lists and figures.

The category comes from the listing as the sync reads it, or from GetItem.
When it can't be read the listing stays a record's and has no category, and
`flask sort-other-items` sorts it later.
"""

import logging

from fbc.core import devmode
from fbc.ebay import api

logger = logging.getLogger()

# Music > Vinyl Records.
VINYL_RECORDS = "176985"


def category_in(item):
    """The category id in an item from eBay's lists, or None if it has none."""
    category = item.get("PrimaryCategory") or {}
    return str(category["CategoryID"]) if category.get("CategoryID") else None


def item_category(item):
    """
    The category id of an item from eBay's lists: the one it carries, or else
    what GetItem says, or None. Never reaches eBay in dev mode.
    """
    category = category_in(item)
    if category or devmode.on():
        return category
    category, errors = api.item_category(str(item["ItemID"]))
    if category is None:
        logger.warning(
            "Couldn't read the category of " + str(item["ItemID"]) + ": " + str(errors)
        )
    return category


def is_record_listing(listing):
    """
    Whether `listing` is a record's regardless of its category: a Unit is
    linked to it, it's a mystery box, or an eBay Draft made it. A record listed
    in the wrong category must never disappear from the record lists.
    """
    from fbc import db
    from fbc.models.ebay import EbayDraft
    from fbc.models.inventory import Unit

    return bool(
        listing.is_mystery_box()
        or db.session.query(Unit).filter_by(ebay_listing_id=listing.item_id).first()
        or db.session.query(EbayDraft)
        .filter_by(ebay_listing_id=listing.item_id)
        .first()
    )


def sort_listing(listing, category):
    """
    Save `category` on `listing`, and give it an Other Item if it isn't a
    record's. Returns the new OtherItem, or None. Safe to repeat: a listing
    has at most one. Doesn't commit.
    """
    from fbc import db
    from fbc.models.other import OtherItem

    if category is None:
        return None
    listing.category_id = category
    if category == VINYL_RECORDS:
        return None
    if db.session.query(OtherItem).filter_by(ebay_listing_id=listing.item_id).first():
        return None
    if is_record_listing(listing):
        logger.warning(
            "Listing "
            + listing.item_id
            + " is in category "
            + category
            + " but has a Unit, a mystery box or a Draft, so it stays a record's."
        )
        return None
    item = OtherItem(
        name=(listing.title or listing.item_id)[:100],
        ebay_listing_id=listing.item_id,
        sold=False,
    )
    db.session.add(item)
    logger.info("Listing " + listing.item_id + " is an Other Item.")
    return item


def claim_receipts():
    """
    Mark Other Items sold when their order already has a Sales Receipt, made
    by hand before fbc knew about Other Items. A receipt that has Units on it
    too is left alone: it can't be split. Doesn't commit. Returns the Items.
    """
    from fbc import db
    from fbc.models.ebay import EbayOrder, EbayOrderItem
    from fbc.models.inventory import Unit
    from fbc.models.other import OtherItem

    claimed = []
    items = (
        db.session.query(OtherItem)
        .filter(OtherItem.sold.is_(False), OtherItem.ebay_listing_id.isnot(None))
        .all()
    )
    for item in items:
        orders = (
            db.session.query(EbayOrder)
            .filter(
                (EbayOrder.ebay_listing_id == item.ebay_listing_id)
                | EbayOrder.items.any(
                    EbayOrderItem.ebay_listing_id == item.ebay_listing_id
                )
            )
            .filter(EbayOrder.sales_receipts.any())
            .order_by(EbayOrder.created_time)
            .all()
        )
        if not orders:
            continue
        receipt = orders[0].sales_receipts[0]
        if db.session.query(Unit).filter_by(sales_receipt_id=receipt.id).first():
            continue
        if (
            db.session.query(OtherItem)
            .filter(OtherItem.sales_receipt_id == receipt.id)
            .first()
        ):
            continue
        item.sold = True
        item.sales_receipt_id = receipt.id
        claimed.append(item)
    return claimed


def sort_existing(apply=False, limit=None, echo=print):
    """
    Read the category of every listing that has none (GetItem, one call
    each), and make Other Items of those outside Vinyl Records, then claim the
    receipts of any already sold. Without `apply` it only reads and says what it
    would do. Safe to run again: it skips listings already sorted and never
    makes a second Other Item. Returns counts.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing

    counts = {
        "records": 0,
        "other": 0,
        "kept": 0,
        "unknown": 0,
        "claimed": 0,
    }  # claimed: only with apply
    if devmode.on():
        echo(devmode.message("Sorting listings by eBay category"))
        return counts
    query = (
        db.session.query(EbayListing)
        .filter(EbayListing.category_id.is_(None))
        .order_by(EbayListing.item_id)
    )
    if limit:
        query = query.limit(limit)
    for listing in query.all():
        category, errors = api.item_category(listing.item_id)
        if category is None:
            counts["unknown"] += 1
            echo(listing.item_id + ": category unknown (" + "; ".join(errors) + ")")
            continue
        if category == VINYL_RECORDS:
            counts["records"] += 1
        elif is_record_listing(listing):
            counts["kept"] += 1
            echo(
                listing.item_id
                + " is in category "
                + category
                + " but has a Unit, so it stays a record's: "
                + (listing.title or "")
            )
        else:
            counts["other"] += 1
            echo(
                listing.item_id
                + ": Other Item, category "
                + category
                + ": "
                + listing.title
            )
        if apply:
            sort_listing(listing, category)
            db.session.commit()
    if apply:
        counts["claimed"] = len(claim_receipts())
        db.session.commit()
    return counts
