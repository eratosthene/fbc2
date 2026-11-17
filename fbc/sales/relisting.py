"""
Re-listing a parked listing (the Parked Listings Task, #208).

Inside eBay's re-list window (core.relist) the old listing is re-listed with
RelistFixedPriceItem and the new ItemID linked at once. Outside the window,
or if eBay refuses, the Unit gets a fresh eBay Draft instead, starting from
its last Draft's title, description, item specifics and price, to finish and
publish like any other. fbc only does this when the owner presses Re-list.
"""

from fbc.core import devmode, localtime, relist
from fbc.ebay import drafts, publish

RELISTED = "relisted"
DRAFTED = "drafted"
REFUSED = "refused"


def unit_of(listing):
    """The unsold Unit still linked to an ended listing, or None."""
    from fbc import db
    from fbc.models.inventory import Unit

    return (
        db.session.query(Unit)
        .filter_by(ebay_listing_id=listing.item_id)
        .filter(Unit.sold.isnot(True))
        .order_by(Unit.id)
        .first()
    )


def window_open(listing, now=None):
    """Whether eBay should still take a re-list of `listing`."""
    by = relist.relist_by(listing.parked_at)
    return by is not None and by > (now or localtime.utcnow())


def fresh_draft(unit):
    """
    A Draft for `unit` to publish as a new listing: its open Draft if it has
    one, else a copy of the text and price of its last Draft, else one built
    from the Unit like any new Draft. Saved.
    """
    from fbc import db
    from fbc.models.ebay import EbayDraft

    open_draft = (
        db.session.query(EbayDraft)
        .filter_by(unit_id=unit.id, status="draft")
        .order_by(EbayDraft.id.desc())
        .first()
    )
    if open_draft is not None:
        return open_draft
    old = (
        db.session.query(EbayDraft)
        .filter_by(unit_id=unit.id)
        .order_by(EbayDraft.id.desc())
        .first()
    )
    if old is None:
        draft = drafts.new_draft(unit)
    else:
        draft = EbayDraft(
            unit=unit,
            title=old.title,
            price=old.price,
            best_offer=old.best_offer,
            description=old.description,
            item_specifics=old.item_specifics,
            weight_oz=old.weight_oz,
            status="draft",
        )
    db.session.add(draft)
    db.session.commit()
    return draft


def relist_or_draft(listing, now=None):
    """
    Do what Re-list means for a parked listing. Returns a dict with
    `outcome` (RELISTED, DRAFTED or REFUSED), `message`, and `item_id` or
    `draft`. REFUSED means nothing was changed.
    """
    unit = unit_of(listing)
    if unit is None:
        return {"outcome": REFUSED, "message": "It has no unsold Unit to list."}
    if window_open(listing, now):
        if devmode.on():
            return {
                "outcome": REFUSED,
                "message": devmode.message("re-listing on eBay"),
            }
        item_id, summary = publish.relist_listing(listing)
        if item_id:
            return {
                "outcome": RELISTED,
                "item_id": item_id,
                "message": "Re-listed as " + item_id + ".",
                "warnings": summary["warnings"],
            }
        why = "eBay refused the re-list: " + "; ".join(summary["errors"])
    else:
        why = "It is past its re-list by date."
    draft = fresh_draft(unit)
    return {
        "outcome": DRAFTED,
        "draft": draft,
        "message": why + " Here is a new eBay Draft for the Unit.",
    }
