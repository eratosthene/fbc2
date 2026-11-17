"""
Ending a Unit's listing on one marketplace when it has sold on the other.

A Unit can be listed on eBay and Discogs at once. When an order for it turns
up on one (the orders syncs call end_sold_elsewhere when they finish), its
listing on the other is ended: an eBay listing with EndFixedPriceItem, a
Discogs Draft or For Sale listing by deleting it, and an unpublished eBay
Draft by deleting the draft. Each try is kept as a ListingEnd.

This shrinks the window for selling a Unit twice but can't close it: both
sites can sell it between two syncs. A failed end never stops the sale from
being recorded. It's kept as a failed ListingEnd, which the home page's To
do banner and the Tasks menu show until the listing is gone, and the next
pass tries it again.

Only sales that happened while the other listing was up count: an order from
before the listing was made (a returned Unit listed again, say) ends nothing.
A listing with several Units on it (an eBay listing with a quantity) is left
alone unless every one of its Units has sold, since the order doesn't say
which Unit went.

Dev mode only logs. Nothing here can reach eBay or Discogs then, and the
syncs that call it don't run.
"""

import datetime
import logging

from fbc.core import devmode, localtime
from fbc.discogs import client as discogs_client
from fbc.discogs.client import ApiError, DiscogsAuthError
from fbc.discogs.listing import OPEN_STATUSES as DISCOGS_OPEN
from fbc.ebay import publish

logger = logging.getLogger()

EBAY = "eBay"
DISCOGS = "Discogs"

# How long an end that worked is left alone. The listings syncs that follow
# bring the listing's own row up to date; until then it's ended in fbc's
# records already, so this only covers a row that was edited by hand.
RETRY_HOURS = 6

# An unpublished eBay Draft is only dropped for a sale this recent: a Draft
# has no date of its own, and an old one on a sold Unit is none of this.
DRAFT_DAYS = 30

CANCELLED = "Cancelled"


def _after(when, since):
    """Whether `when` is at or after `since`; a missing date counts as yes."""
    return when is None or since is None or when >= since


def _record(unit, sold_on, ended_on, ref, order_id, ok, message):
    """Save one try as the listing's ListingEnd, and return it."""
    from fbc import db
    from fbc.models.sales import ENDED, FAILED, ListingEnd

    row = (
        db.session.query(ListingEnd)
        .filter_by(ended_on=ended_on, listing_ref=str(ref))
        .first()
    )
    now = localtime.utcnow()
    if row is None:
        row = ListingEnd(
            unit_id=unit.id,
            sold_on=sold_on,
            ended_on=ended_on,
            listing_ref=str(ref),
            created=now,
        )
        db.session.add(row)
    row.order_id = order_id
    row.status = ENDED if ok else FAILED
    row.message = (message or "")[:255]
    row.updated = now
    db.session.commit()
    log = logger.info if ok else logger.error
    log(
        "Unit %s sold on %s: %s listing %s: %s",
        unit.id,
        sold_on,
        ended_on,
        ref,
        message,
    )
    return row


def _recently_ended(ended_on, ref):
    from fbc import db
    from fbc.models.sales import ENDED, ListingEnd

    row = (
        db.session.query(ListingEnd)
        .filter_by(ended_on=ended_on, listing_ref=str(ref), status=ENDED)
        .first()
    )
    return bool(
        row and localtime.utcnow() - row.updated < datetime.timedelta(hours=RETRY_HOURS)
    )


def _end_discogs(listing):
    """Delete a Discogs listing: (ok, message)."""
    from fbc import db

    try:
        discogs_client.account().delete_listing(listing.listing_id)
        message = "Deleted from Discogs."
    except ApiError as e:
        # Discogs answers 404 for a listing that's already gone.
        if e.status_code != 404:
            return False, "Discogs refused: " + str(e).rstrip(".") + "."
        message = "Discogs says it was already gone."
    except (DiscogsAuthError, OSError) as e:
        return False, "Couldn't reach Discogs: " + str(e).rstrip(".") + "."
    listing.ended = True
    db.session.commit()
    return True, message


def _end_ebay(listing):
    """End an eBay listing: (ok, message)."""
    from fbc import db

    ok, message = publish.end_listing(listing.item_id)
    if ok:
        listing.ended = True
        listing.end_time = localtime.utcnow()
        db.session.commit()
    return ok, message


def _ebay_sales(listing_id, since):
    """How many Units eBay orders on `listing_id` took since `since`, and
    the first such order's id."""
    from fbc import db
    from fbc.models.ebay import EbayOrder, EbayOrderItem

    rows = (
        db.session.query(
            EbayOrder.order_id, EbayOrder.created_time, EbayOrderItem.quantity
        )
        .join(EbayOrderItem, EbayOrderItem.ebay_order_id == EbayOrder.order_id)
        .filter(EbayOrderItem.ebay_listing_id == listing_id)
        .order_by(EbayOrder.created_time)
        .all()
    )
    rows = [r for r in rows if _after(r.created_time, since)]
    return sum(r.quantity or 1 for r in rows), (rows[0].order_id if rows else None)


def end_discogs_after_ebay():
    """
    End the Discogs listing (or Draft) of every Unit an eBay order has sold.
    Returns the ListingEnds written.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsListing
    from fbc.models.inventory import Unit

    results = []
    open_listings = (
        db.session.query(DiscogsListing)
        .filter(
            DiscogsListing.unit_id.isnot(None),
            DiscogsListing.ended.is_(False),
            DiscogsListing.status.in_(DISCOGS_OPEN),
        )
        .all()
    )
    for listing in open_listings:
        unit = listing.unit
        if unit is None or not unit.ebay_listing_id:
            continue
        sold, order_id = _ebay_sales(unit.ebay_listing_id, listing.posted)
        on_listing = (
            db.session.query(Unit)
            .filter_by(ebay_listing_id=unit.ebay_listing_id)
            .count()
        )
        if not sold or sold < on_listing:
            continue
        if _recently_ended(DISCOGS, listing.listing_id):
            continue
        ok, message = _end_discogs(listing)
        results.append(
            _record(unit, EBAY, DISCOGS, listing.listing_id, order_id, ok, message)
        )
    return results


def _discogs_sale(unit, since, recent_only=False):
    """The id of a Discogs order, not cancelled, that sold `unit` since
    `since` (and, with `recent_only`, in the last DRAFT_DAYS days), or None."""
    from fbc import db
    from fbc.models.discogs import DiscogsOrder, DiscogsOrderItem

    rows = (
        db.session.query(
            DiscogsOrder.order_id, DiscogsOrder.created_time, DiscogsOrder.status
        )
        .join(
            DiscogsOrderItem, DiscogsOrderItem.discogs_order_id == DiscogsOrder.order_id
        )
        .filter(DiscogsOrderItem.unit_id == unit.id)
        .order_by(DiscogsOrder.created_time)
        .all()
    )
    floor = (
        localtime.utcnow() - datetime.timedelta(days=DRAFT_DAYS)
        if recent_only
        else None
    )
    for row in rows:
        if (row.status or "").startswith(CANCELLED):
            continue
        if _after(row.created_time, since) and _after(row.created_time, floor):
            return row.order_id
    return None


def end_ebay_after_discogs():
    """
    End the eBay listing, and delete the unpublished eBay Draft, of every
    Unit a Discogs order has sold. Returns the ListingEnds written.
    """
    from fbc import db
    from fbc.models.ebay import EbayDraft, EbayListing
    from fbc.models.inventory import Unit

    results = []
    active = db.session.query(EbayListing).filter(EbayListing.ended.is_(False)).all()
    for listing in active:
        units = db.session.query(Unit).filter_by(ebay_listing_id=listing.item_id).all()
        if not units:
            continue
        orders = [_discogs_sale(u, listing.start_time) for u in units]
        if not all(orders):
            continue
        if _recently_ended(EBAY, listing.item_id):
            continue
        ok, message = _end_ebay(listing)
        results.append(
            _record(units[0], DISCOGS, EBAY, listing.item_id, orders[0], ok, message)
        )
    drafts = db.session.query(EbayDraft).filter_by(status="draft").all()
    for draft in drafts:
        unit = draft.unit
        order_id = _discogs_sale(unit, None, recent_only=True) if unit else None
        if not order_id:
            continue
        ref = "draft " + str(draft.id)
        title = draft.title
        db.session.delete(draft)
        db.session.commit()
        results.append(
            _record(
                unit,
                DISCOGS,
                EBAY,
                ref,
                order_id,
                True,
                "Deleted the eBay Draft: " + title,
            )
        )
    return results


def _still_open(row):
    """Whether what a failed ListingEnd tried to end is still there."""
    from fbc import db
    from fbc.models.discogs import DiscogsListing
    from fbc.models.ebay import EbayListing

    if row.ended_on == DISCOGS:
        listing = db.session.get(DiscogsListing, int(row.listing_ref))
        return bool(listing and not listing.ended and listing.status in DISCOGS_OPEN)
    listing = db.session.get(EbayListing, row.listing_ref)
    return bool(listing and not listing.ended)


def clear_gone():
    """
    A failed end whose listing has since ended on its own, or been taken
    down by hand, is done: say so, so it stops showing as a task.
    """
    from fbc import db
    from fbc.models.sales import ENDED, FAILED, ListingEnd

    for row in db.session.query(ListingEnd).filter_by(status=FAILED).all():
        if not _still_open(row):
            row.status = ENDED
            row.message = "The listing is no longer up, ended by hand or by a sync."
            row.updated = localtime.utcnow()
    db.session.commit()


def end_sold_elsewhere():
    """
    End every listing whose Unit has sold on the other marketplace. Called
    when an orders sync has finished; it only needs the database to see
    what to do, so it's cheap when there's nothing. Never raises for a
    failed end (those are kept as ListingEnds); returns what was written.
    """
    if devmode.on():
        logger.info(devmode.message("ending listings sold elsewhere"))
        return []
    clear_gone()
    results = end_discogs_after_ebay() + end_ebay_after_discogs()
    clear_gone()
    return results
