"""
Give the active eBay listings the description the current Description
Template makes, so they look like the new ones.

`flask update-listing-descriptions` goes through the listings fbc thinks are
active, builds each one's description from its Unit (as a new eBay Draft would),
and, with --apply, sets it with ReviseFixedPriceItem, sending nothing else.

A description you wrote by hand on a Draft, or on eBay, is replaced; the dry run
says which listings it would change. A Unit with no notes or Extras yet is left
alone, since its description would only be its old one.

Everything goes through api._call(), and so through api.trading(): in dev mode
it refuses, and nothing reaches eBay.
"""

import time

from fbc.core import devmode
from fbc.ebay import api, describe

# Between revises, to stay well inside the Trading API's daily call limit.
PAUSE_SECONDS = 0.5

# What happens to a listing.
SET = "set"
RIGHT = "already right"
NO_UNIT = "no Unit linked"
SEVERAL = "several Units linked"
NO_NOTES = "Unit has no notes or Extras yet"
MYSTERY = "mystery box"
FAILED = "eBay refused the change"

SKIP_REASONS = (NO_UNIT, SEVERAL, NO_NOTES, MYSTERY, FAILED)


def decide(listing, units, draft):
    """
    (outcome, new description or None) for one listing. `units` are the Units
    whose ebay_listing_id is the listing; `draft` is its last eBay Draft, or None.
    """
    if listing.pick_from_box_id:
        return MYSTERY, None
    unsold = [u for u in units if not u.sold]
    if not unsold:
        return NO_UNIT, None
    if len(unsold) > 1:
        return SEVERAL, None
    unit = unsold[0]
    if not any(
        [unit.record_note, unit.sleeve_note, unit.dust_jacket_note, unit.extras]
    ):
        return NO_NOTES, None
    new = describe.build_description(unit)
    if draft is not None and draft.description == new:
        return RIGHT, None
    return SET, new


def revise_description(item_id, description):
    """Set the description, sending nothing else. Returns the summary's errors."""
    resp, summary = api._call(
        "ReviseFixedPriceItem",
        {"Item": {"ItemID": item_id, "Description": description}},
    )
    if resp is None and not summary["errors"]:
        summary["errors"].append("no response")
    return summary["errors"]


def update_all(apply=False, item=None, limit=None, echo=None, pause=PAUSE_SECONDS):
    """
    Work through the active listings. Returns {"counts": {outcome: n}}. Without
    `apply` nothing is changed on eBay: "set" counts what would be.
    """
    from fbc import db
    from fbc.models.ebay import EbayDraft, EbayListing
    from fbc.models.inventory import Unit

    echo = echo or (lambda text: None)
    if apply and devmode.on():
        raise ValueError(devmode.message("changing listings on eBay"))
    query = db.session.query(EbayListing).filter(
        EbayListing.ended.is_(False), EbayListing.archived.is_(False)
    )
    if item:
        query = query.filter(EbayListing.item_id == str(item))
    counts = dict.fromkeys((SET, RIGHT) + SKIP_REASONS, 0)
    calls = 0
    for listing in query.order_by(EbayListing.item_id).all():
        units = (
            db.session.query(Unit).filter(Unit.ebay_listing_id == listing.item_id).all()
        )
        draft = (
            db.session.query(EbayDraft)
            .filter(EbayDraft.ebay_listing_id == listing.item_id)
            .order_by(EbayDraft.id.desc())
            .first()
        )
        outcome, new = decide(listing, units, draft)
        if outcome == SET:
            if limit is not None and calls >= limit:
                continue
            calls += 1
            if apply:
                if calls > 1 and pause:
                    time.sleep(pause)
                errors = revise_description(listing.item_id, new)
                if errors:
                    outcome = FAILED
                    echo("Failed " + listing.item_id + ": " + " ".join(errors))
                elif draft is not None:
                    draft.description = new
                    db.session.commit()
            if outcome == SET:
                echo(
                    ("Set " if apply else "Would set ")
                    + listing.item_id
                    + ": "
                    + (listing.title or "")
                )
        elif outcome != RIGHT:
            echo("Skipped " + listing.item_id + " (" + outcome + ")")
        counts[outcome] += 1
    if item and not sum(counts.values()):
        raise ValueError("Item " + str(item) + " isn't an active listing in fbc.")
    return {"counts": counts}
