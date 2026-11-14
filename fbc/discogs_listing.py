"""
Listing a Unit on the Discogs Marketplace: the only place fbc writes to
Discogs's seller inventory. Two writes, each started by its own button:

- create_listing posts the listing as a Draft, which Discogs doesn't show to
  buyers. Pictures can't be set through the API, so the owner adds them on
  Discogs's site.
- make_for_sale changes a Draft to For Sale, which makes it live.

Both go through fbc.discogs, so dev mode refuses them (the views also check
devmode.on() first, so nothing is tried).
"""

import datetime
import logging

from fbc import devmode, discogs
from fbc.listing import (
    build_item_specifics,
    default_weight,
    disc_count,
    listing_settings,
    sku,
    split_grading,
)

logger = logging.getLogger()

# Discogs's condition names, best first. These are the only values Discogs
# accepts for a Media Condition. fbc/listing.py GRADES uses the same words.
MEDIA_CONDITIONS = (
    "Mint (M)",
    "Near Mint (NM or M-)",
    "Very Good Plus (VG+)",
    "Very Good (VG)",
    "Good Plus (G+)",
    "Good (G)",
    "Fair (F)",
    "Poor (P)",
)
# A sleeve can also be one of these.
SLEEVE_CONDITIONS = MEDIA_CONDITIONS + ("Generic", "Not Graded", "No Cover")

# Unit.grading's abbreviations ("VG+/VG+", "NM or M-/VG") to Discogs's names.
# EX isn't a Discogs grade and doesn't map to one for certain, so it's left
# for the owner to choose.
CONDITION_FOR_GRADE = {
    "M": "Mint (M)",
    "NM": "Near Mint (NM or M-)",
    "M-": "Near Mint (NM or M-)",
    "NM or M-": "Near Mint (NM or M-)",
    "VG+": "Very Good Plus (VG+)",
    "VG": "Very Good (VG)",
    "G+": "Good Plus (G+)",
    "G": "Good (G)",
    "F": "Fair (F)",
    "P": "Poor (P)",
}

GRAMS_PER_OUNCE = 28.3495

DRAFT = "Draft"
FOR_SALE = "For Sale"
# A Unit with a listing in either state can't be listed again.
OPEN_STATUSES = (DRAFT, FOR_SALE)

# How many of the newest inventory items to read before creating a listing,
# looking for one that an earlier, timed-out attempt already made.
RECENT_ITEMS = 100


class ListingError(Exception):
    """Something fbc will say to the owner as it is."""


class PostUncertain(ListingError):
    """The post may or may not have reached Discogs."""


def condition_for(abbreviation):
    """Discogs's name for one of Unit.grading's abbreviations, or None."""
    return CONDITION_FOR_GRADE.get((abbreviation or "").strip())


def conditions_from_grading(grading):
    """(media condition, sleeve condition) for Unit.grading; each may be None."""
    record, sleeve = split_grading(grading)
    return condition_for(record), condition_for(sleeve)


def weight_grams(unit):
    """
    The package weight to start with, in grams: eBay Drafts' default weight
    in ounces (EBAY_SETTINGS["LISTING"]["PACKAGE"], by record size and number
    of discs) times 28.3495, rounded to a whole gram. None if there's none.
    """
    settings = listing_settings()
    specifics = build_item_specifics(unit, settings.get("DEFAULT_SPECIFICS"))
    ounces = default_weight(specifics, settings, disc_count(unit))
    if not ounces:
        return None
    return round(ounces * GRAMS_PER_OUNCE)


def default_allow_offers():
    """DISCOGS_SETTINGS["ALLOW_OFFERS"], on if it isn't set (as eBay's BEST_OFFER)."""
    from fbc import app

    return bool(app.config["DISCOGS_SETTINGS"].get("ALLOW_OFFERS", True))


def defaults(unit):
    """What the List on Discogs form starts with for `unit`."""
    media, sleeve = conditions_from_grading(unit.grading)
    return {
        "release_id": unit.discogs_release_id,
        "condition": media,
        "sleeve_condition": sleeve,
        "price": None if unit.retail_price is None else f"{unit.retail_price:.2f}",
        "comments": unit.notes or "",
        "allow_offers": default_allow_offers(),
        "weight": weight_grams(unit),
        "external_id": sku(unit.id),
        "location": unit.storage_box.name if unit.storage_box else "",
    }


def open_listing(unit):
    """The Unit's DiscogsListing that is a Draft or For Sale, or None."""
    from fbc import db
    from fbc.models.discogs import DiscogsListing

    return (
        db.session.query(DiscogsListing)
        .filter(
            DiscogsListing.unit_id == unit.id,
            DiscogsListing.ended.is_(False),
            DiscogsListing.status.in_(OPEN_STATUSES),
        )
        .first()
    )


def problem_with(unit):
    """Why `unit` can't be listed on Discogs at all, or None."""
    if unit.sold:
        return "This Unit is sold."
    if not unit.discogs_release_id:
        return "This Unit has no Discogs release to list."
    if not unit.discogs_instance_id:
        return (
            "This Unit isn't in your Discogs collection. Only Units in the"
            " collection are listed on Discogs, so the collection stays a"
            " record of what you have."
        )
    existing = open_listing(unit)
    if existing:
        return (
            "This Unit already has a Discogs listing ("
            + str(existing.listing_id)
            + ", "
            + str(existing.status)
            + ")."
        )
    return None


def ebay_conflicts(unit):
    """
    Notes about the Unit being on eBay too: (text, link or None) for an
    active eBay listing and for an unpublished eBay Draft. Listing it on both
    can sell it twice; the owner can still go on.
    """
    from flask import url_for

    from fbc import db
    from fbc.models.ebay import EbayDraft

    notes = []
    listing = unit.ebay_listing
    if listing and not listing.ended:
        notes.append(
            (
                "This Unit has an active eBay listing ("
                + str(listing.item_id)
                + "). If it sells on one site it has to be ended on the other by hand.",
                listing.url,
            )
        )
    draft = (
        db.session.query(EbayDraft).filter_by(unit_id=unit.id, status="draft").first()
    )
    if draft:
        notes.append(
            (
                "This Unit has an eBay Draft that hasn't been published.",
                url_for("EbayDraftModelView.show", pk=draft.id),
            )
        )
    return notes


def clean_form(form):
    """
    Check the form's values and return (payload for Discogs, errors). The
    payload holds what Discogs's create-listing call takes, except for
    external_id, which the caller sets from the Unit.
    """
    errors = []
    try:
        release_id = int(form.get("release_id", ""))
    except ValueError:
        release_id = None
        errors.append("Release ID has to be a number.")
    condition = form.get("condition", "")
    if condition not in MEDIA_CONDITIONS:
        errors.append("Choose a Media Condition.")
    sleeve = form.get("sleeve_condition", "")
    if sleeve not in SLEEVE_CONDITIONS:
        errors.append("Choose a Sleeve Condition.")
    try:
        price = round(float(form.get("price", "")), 2)
        if price <= 0:
            raise ValueError
    except ValueError:
        price = None
        errors.append("Price has to be more than 0.")
    weight = None
    if (form.get("weight") or "").strip():
        try:
            weight = int(float(form["weight"]))
            if weight <= 0:
                raise ValueError
        except ValueError:
            errors.append("Weight has to be a whole number of grams.")
    payload = {
        "release_id": release_id,
        "condition": condition,
        "sleeve_condition": sleeve,
        "price": price,
        "comments": (form.get("comments") or "").strip(),
        "allow_offers": bool(form.get("allow_offers")),
        "status": DRAFT,
        "location": (form.get("location") or "").strip()[:255],
    }
    if weight:
        payload["weight"] = weight
    return payload, errors


def find_existing(account, unit):
    """
    The newest Draft or For Sale item on Discogs whose external_id is the
    Unit's, from the first RECENT_ITEMS items of the inventory, newest first.
    A post that timed out may have worked, and the sync may not have seen it
    yet; this finds it so a retry doesn't make a second one. Returns the
    item's JSON or None.
    """
    wanted = sku(unit.id)
    for data in account.newest_inventory(RECENT_ITEMS):
        if data.get("external_id") == wanted and data.get("status") in OPEN_STATUSES:
            return data
    return None


def save_posted(data):
    """Save one item of Discogs's JSON as a DiscogsListing, as the sync would."""
    from fbc import sync
    from fbc.util import save_discogs_listing

    return save_discogs_listing(data, sync.utcnow())


def _as_item(listing_id, payload, url=None):
    """The inventory-item JSON for a listing just posted, from what was posted."""
    return {
        "id": listing_id,
        "uri": url or "https://www.discogs.com/sell/item/" + str(listing_id),
        "status": payload["status"],
        "price": {"value": payload["price"], "currency": "USD"},
        "condition": payload["condition"],
        "sleeve_condition": payload["sleeve_condition"],
        "posted": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "external_id": payload["external_id"],
        "location": payload.get("location") or None,
        "comments": payload.get("comments") or None,
        "release": {"id": payload["release_id"], "description": payload.get("title")},
    }


def create_listing(unit, payload):
    """
    Post a Draft listing for `unit` and save it as a DiscogsListing linked to
    the Unit. Returns (DiscogsListing, adopted): adopted is True when
    Discogs already had a Draft or For Sale item for the Unit, which was
    saved instead of posting another one. Raises ListingError with a message
    for the owner if it couldn't be done (nothing is saved then), or
    PostUncertain if Discogs may have made the listing (running the button
    again looks for it first).

    The caller has checked problem_with(unit) and dev mode.
    """
    from fbc import db

    devmode.check(discogs.DiscogsAuthError, "Discogs")
    if unit.sold or not unit.discogs_release_id:
        raise ListingError(problem_with(unit))
    payload = dict(payload, external_id=sku(unit.id), status=DRAFT)
    try:
        account = discogs.account()
        found = find_existing(account, unit)
        if found:
            logger.info(
                "Unit %s is already on Discogs as listing %s", unit.id, found["id"]
            )
            return save_posted(found), True
        created = account.create_listing(payload)
    except discogs.DiscogsAuthError as e:
        raise ListingError(str(e))
    except discogs.ApiError as e:
        if e.refused:
            raise ListingError("Discogs said no: " + str(e))
        if e.rate_limited:
            raise ListingError("Discogs says too many requests; try in a minute.")
        raise PostUncertain("Discogs answered with an error: " + str(e))
    except Exception as e:
        # A timeout or dropped connection: the listing may have been made.
        logger.exception("Posting a Discogs listing for unit %s didn't finish", unit.id)
        raise PostUncertain("Couldn't reach Discogs: " + str(e))
    listing_id = int((created or {}).get("listing_id") or 0)
    if not listing_id:
        raise PostUncertain("Discogs didn't say what the new listing's ID is.")
    logger.info("Posted Discogs listing %s as a Draft for unit %s", listing_id, unit.id)
    try:
        return save_posted(_as_item(listing_id, payload, created.get("uri"))), False
    except Exception as e:
        db.session.rollback()
        logger.exception("Posted Discogs listing %s but couldn't save it", listing_id)
        raise ListingError(
            "The Draft was made on Discogs (listing "
            + str(listing_id)
            + ") but fbc couldn't save it ("
            + str(e)
            + "). Run Sync Discogs Listings to bring it in."
        )


def make_for_sale(row):
    """
    Change a Draft DiscogsListing to For Sale on Discogs, which makes it live.
    The listing is read from Discogs first, and only goes ahead if it is still
    a Draft there. Returns the updated DiscogsListing; raises ListingError
    with a message for the owner otherwise.
    """
    devmode.check(discogs.DiscogsAuthError, "Discogs")
    if row.ended or row.status != DRAFT:
        raise ListingError("Only a Draft can be made For Sale.")
    try:
        account = discogs.account()
        current = account.listing(row.listing_id)
        if current.get("status") != DRAFT:
            save_posted(current)
            raise ListingError(
                "On Discogs this listing is "
                + str(current.get("status"))
                + ", not a Draft, so nothing was changed. fbc's copy is updated."
            )
        # Discogs's edit call wants the release, condition and price again.
        account.update_listing(
            row.listing_id,
            {
                "release_id": current["release"]["id"],
                "condition": current["condition"],
                "price": current["price"]["value"],
                "status": FOR_SALE,
            },
        )
        current["status"] = FOR_SALE
        return save_posted(current)
    except ListingError:
        raise
    except discogs.DiscogsAuthError as e:
        raise ListingError(str(e))
    except discogs.ApiError as e:
        raise ListingError("Discogs said no: " + str(e))
    except Exception as e:
        logger.exception(
            "Making Discogs listing %s For Sale didn't finish", row.listing_id
        )
        raise ListingError(
            "Couldn't reach Discogs: "
            + str(e)
            + ". It may or may not have gone through; run Sync Discogs Listings"
            " to see which."
        )
