"""
Putting listings that were ended by hand into the Parked Listings queue.

End and park (fbc/sync/ending.py) sets `parked_at`, so a listing the owner
ended in eBay before that existed isn't in the queue. `flask
park-ended-listings` sets `parked_at` from `end_time` for the ones that fit the
queue's own conditions (fbc/sales/tasks.py ids_parked_listings). It only edits
fbc's own rows and never calls eBay.

`end_time` is when fbc's sync noticed the listing was gone, so it can be a
little later than the true end.
"""

import sqlalchemy as sa

from fbc.core import localtime, relist


def candidates():
    """
    The ended listings that should be parked, oldest end first: not archived,
    not parked, with an end time, not a mystery box or Other Item, linked to
    exactly one Unit (unsold, not archived), with no eBay order and no other
    active listing for that Unit. Returns a list of EbayListing.
    """
    from fbc import db
    from fbc.models import other
    from fbc.models.ebay import EbayDraft, EbayListing, EbayOrderItem
    from fbc.models.inventory import Unit

    units_on = (
        sa.select(Unit.ebay_listing_id)
        .where(Unit.ebay_listing_id.isnot(None))
        .group_by(Unit.ebay_listing_id)
        .having(sa.func.count() == 1)
    )
    # A re-list that the Draft knows about but the Unit doesn't point at yet.
    relisted_sql = (
        sa.select(EbayDraft.unit_id)
        .join(EbayListing, EbayListing.item_id == EbayDraft.ebay_listing_id)
        .where(EbayDraft.unit_id.isnot(None), EbayListing.ended.is_(False))
    )
    relisted_units = {r for (r,) in db.session.execute(relisted_sql)}
    rows = db.session.scalars(
        sa.select(EbayListing)
        .where(
            EbayListing.ended.is_(True),
            EbayListing.archived.is_(False),
            EbayListing.parked_at.is_(None),
            EbayListing.end_time.isnot(None),
            EbayListing.pick_from_box_id.is_(None),
            EbayListing.item_id.in_(units_on),
            EbayListing.item_id.notin_(other.listing_ids()),
            EbayListing.item_id.notin_(
                sa.select(EbayOrderItem.ebay_listing_id).where(
                    EbayOrderItem.ebay_listing_id.isnot(None)
                )
            ),
        )
        .order_by(EbayListing.end_time, EbayListing.item_id)
    ).all()
    result = []
    for listing in rows:
        if listing.is_mystery_box():
            continue
        unit = (
            db.session.query(Unit)
            .filter_by(ebay_listing_id=listing.item_id)
            .filter(Unit.sold.isnot(True), Unit.archived.is_(False))
            .first()
        )
        if unit is None or unit.id in relisted_units:
            continue
        result.append(listing)
    return result


def park_ended(apply=False, limit=None, echo=print, now=None):
    """
    Park the candidates (at most `limit`, oldest first); without `apply`
    only say what would change. Returns {"parked": n, "past_window": n}.
    """
    from fbc import db

    now = now or localtime.utcnow()
    found = candidates()
    if limit is not None:
        found = found[: max(limit, 0)]
    past = 0
    for listing in found:
        days = relist.days_out(listing.end_time, now)
        late = now > relist.relist_by(listing.end_time)
        past += late
        echo(
            "%s  %s  ended %s, %d day%s ago, %s"
            % (
                listing.item_id,
                listing.title,
                localtime.fmt_datetime(listing.end_time),
                days,
                "" if days == 1 else "s",
                "past the re-list window (it will re-list through a new Draft)"
                if late
                else "inside the re-list window",
            )
        )
        if apply:
            listing.parked_at = listing.end_time
    if apply:
        db.session.commit()
    echo(
        "%s %d listing%s, %d past the re-list window of %d days."
        % (
            "Parked" if apply else "Would park",
            len(found),
            "" if len(found) == 1 else "s",
            past,
            relist.window_days(),
        )
    )
    echo(
        "An end time is when fbc's sync noticed the listing was gone, so it can"
        " be a little later than when it really ended."
    )
    return {"parked": len(found), "past_window": past}
