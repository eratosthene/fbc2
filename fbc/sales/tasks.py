"""
The things to do next, worked out from the data, so each one clears itself
when it's done and there's no to-do table to keep in step:

- an eBay Order with no Sales Receipt is still to ship (Ship This makes it);
- a Discogs Release with more copies in the Discogs collection than unsold
  Units has copies that need a Unit made and priced. (A sold copy leaves the
  collection, so its sold Unit doesn't count.)
- a Discogs Order that isn't cancelled and has no Sales Receipt is still to
  ship (Ship This on the order makes it);
- a failed attempt to end a listing because its Unit sold on the other
  marketplace (fbc/sync/ending.py) is still to sort out, and clears when the
  listing is gone;
- an unsold Unit with no eBay Listing and no eBay Draft, priced over $2 (the
  rest go in the discount boxes), needs an eBay Draft;
- a listing ended to make room (End and park, fbc/sync/ending.py) whose Unit
  is unsold and still linked to it, so it has no active listing, is parked
  and waits to be listed again (the Parked listings Task, #208);
- an eBay Draft that isn't published yet needs pictures and a listing;
- an unsold Unit in the Discogs collection, priced over $2, with no Discogs
  Draft or For Sale listing, needs a Discogs Draft (List on Discogs);
- a Discogs listing that is still a Draft needs pictures (added on Discogs)
  and to be made For Sale.

The ids_* functions return a SELECT of ids, for the lists' filters
(fbc/views/tasks.py); counts() is for the home page's banner.
"""

import sqlalchemy as sa

# The price above which a Unit gets its own listing, as UNSOLD_UNITS_OVER_2_FILTERS.
LISTING_PRICE_OVER = 2

DRAFT_STATUS = "draft"
# Discogs's own word, as fbc.discogs.listing.DRAFT.
DISCOGS_DRAFT_STATUS = "Draft"
DISCOGS_OPEN_STATUSES = ("Draft", "For Sale")


def ids_orders_to_ship():
    from fbc.models.ebay import EbayOrder

    return sa.select(EbayOrder.order_id).where(
        EbayOrder.archived.is_(False), ~EbayOrder.sales_receipts.any()
    )


def ids_discogs_orders_to_ship():
    from fbc.models.discogs import DiscogsOrder

    return sa.select(DiscogsOrder.order_id).where(
        DiscogsOrder.archived.is_(False),
        ~DiscogsOrder.sales_receipts.any(),
        sa.or_(
            DiscogsOrder.status.is_(None), DiscogsOrder.status.notlike("Cancelled%")
        ),
    )


def ids_failed_endings():
    from fbc.models.sales import FAILED, ListingEnd

    return sa.select(ListingEnd.id).where(ListingEnd.status == FAILED)


def ids_releases_needing_units():
    from fbc.models.discogs import DiscogsRelease
    from fbc.models.inventory import Unit

    link = DiscogsRelease.assoc_discogs_instance_discogs_release
    copies = (
        sa.select(link.c.release_id, sa.func.count().label("n"))
        .group_by(link.c.release_id)
        .subquery()
    )
    units = (
        sa.select(Unit.discogs_release_id, sa.func.count().label("n"))
        .where(Unit.discogs_release_id.isnot(None), Unit.sold.isnot(True))
        .group_by(Unit.discogs_release_id)
        .subquery()
    )
    return (
        sa.select(DiscogsRelease.release_id)
        .join(copies, copies.c.release_id == DiscogsRelease.release_id)
        .outerjoin(units, units.c.discogs_release_id == DiscogsRelease.release_id)
        .where(copies.c.n > sa.func.coalesce(units.c.n, 0))
    )


def ids_units_needing_drafts():
    from fbc.models.ebay import EbayDraft
    from fbc.models.inventory import Unit

    return sa.select(Unit.id).where(
        Unit.sold.isnot(True),
        Unit.archived.is_(False),
        Unit.ebay_listing_id.is_(None),
        Unit.retail_price > LISTING_PRICE_OVER,
        Unit.id.notin_(sa.select(EbayDraft.unit_id)),
    )


def ids_parked_listings():
    """
    The listings End and park ended, whose Unit hasn't sold and still points
    at the ended listing (a re-list that sync linked up moves the Unit to the
    new listing, which takes it out). Mystery boxes and Other Items have no
    Unit linked, so never show. The list view orders them, longest out first.
    """
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit

    return sa.select(EbayListing.item_id).where(
        EbayListing.parked_at.isnot(None),
        EbayListing.ended.is_(True),
        EbayListing.item_id.in_(
            sa.select(Unit.ebay_listing_id).where(
                Unit.ebay_listing_id.isnot(None),
                Unit.sold.isnot(True),
                Unit.archived.is_(False),
            )
        ),
    )


def first_parked():
    """The listing the Parked Listings queue puts first (the longest out), or None."""
    from fbc import db
    from fbc.models.ebay import EbayListing

    return db.session.scalars(
        sa.select(EbayListing)
        .where(EbayListing.item_id.in_(ids_parked_listings()))
        .order_by(EbayListing.parked_at, EbayListing.item_id)
        .limit(1)
    ).first()


def nothing_better_to_list(room, waiting_total):
    """
    The parked listing to suggest re-listing, only when there are more free
    insertions left than Units waiting: new stock always comes first (#208).
    """
    if room is None or room <= waiting_total:
        return None
    return first_parked()


def ids_drafts_to_list():
    from fbc.models.ebay import EbayDraft

    return sa.select(EbayDraft.id).where(
        EbayDraft.status == DRAFT_STATUS, EbayDraft.ebay_listing_id.is_(None)
    )


def ids_units_needing_discogs_drafts():
    """
    What fbc.discogs.listing.problem_with lets be listed: in the Discogs
    collection, with no open Discogs listing; and over the price a Unit gets
    its own listing at. A Unit that is on eBay is still here: it can be sold
    in both places, and List on Discogs warns about it.
    """
    from fbc.models.discogs import DiscogsListing
    from fbc.models.inventory import Unit

    # NOT IN is never true if the subquery has a NULL, so leave those out.
    open_listings = sa.select(DiscogsListing.unit_id).where(
        DiscogsListing.unit_id.isnot(None),
        DiscogsListing.ended.is_(False),
        DiscogsListing.status.in_(DISCOGS_OPEN_STATUSES),
    )
    return sa.select(Unit.id).where(
        Unit.sold.isnot(True),
        Unit.archived.is_(False),
        Unit.discogs_release_id.isnot(None),
        Unit.discogs_instance_id.isnot(None),
        Unit.retail_price > LISTING_PRICE_OVER,
        Unit.id.notin_(open_listings),
    )


def ids_discogs_drafts_to_list():
    from fbc.models.discogs import DiscogsListing

    return sa.select(DiscogsListing.listing_id).where(
        DiscogsListing.status == DISCOGS_DRAFT_STATUS,
        DiscogsListing.ended.is_(False),
    )


def _count(select):
    from fbc import db

    return db.session.scalar(sa.select(sa.func.count()).select_from(select.subquery()))


def stock_waiting():
    """
    {"units", "drafts", "total"}: the Units with no eBay listing or Draft, and
    the Units whose eBay Draft isn't published yet, each Unit once. Other
    Items and mystery boxes aren't Units, so they never count.
    """
    from fbc import db
    from fbc.models.ebay import EbayDraft

    needing = set(db.session.scalars(ids_units_needing_drafts()))

    drafted = set(
        db.session.scalars(
            sa.select(EbayDraft.unit_id).where(EbayDraft.id.in_(ids_drafts_to_list()))
        )
    )
    return {
        "units": len(needing),
        "drafts": len(drafted - needing),
        "total": len(needing | drafted),
    }


def counts():
    """How many of each task there are, by key."""
    return {
        "orders": _count(ids_orders_to_ship()),
        "discogs_orders": _count(ids_discogs_orders_to_ship()),
        "failed_endings": _count(ids_failed_endings()),
        "releases": _count(ids_releases_needing_units()),
        "units": _count(ids_units_needing_drafts()),
        "drafts": _count(ids_drafts_to_list()),
        "parked": _count(ids_parked_listings()),
        "discogs_units": _count(ids_units_needing_discogs_drafts()),
        "discogs_drafts": _count(ids_discogs_drafts_to_list()),
    }
