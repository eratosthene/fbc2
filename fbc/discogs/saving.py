"""Saving what Discogs sends us: listings, releases and collection instances."""

import json
import logging

from fbc.core.localtime import naive_utc

logger = logging.getLogger()


# Statuses a listing can't come back from. Discogs only lets For Sale, Draft
# and Expired listings be edited, so those three are still ours to use.
DISCOGS_ENDED_STATUSES = ("Sold", "Deleted", "Violation", "Suspended")


def save_discogs_listing(data, seen):
    """
    Add or update the DiscogsListing for one item of Discogs's seller
    inventory (`data` is the item's JSON), and link it to its Unit by
    external_id ("fbc-<unit id>"). `seen` is the sync's start time.

    A listing with no external_id, or one that names a Unit fbc doesn't have,
    is kept with no Unit. A Unit is only linked from here, never unlinked: it
    stays with the listing even when the listing is no longer for sale.
    Returns the DiscogsListing.
    """
    from fbc import db
    from fbc.discogs_client import parse_timestamp
    from fbc.ebay.skus import unit_id_from_sku
    from fbc.models.discogs import DiscogsListing
    from fbc.models.inventory import Unit

    listing_id = int(data["id"])
    row = db.session.get(DiscogsListing, listing_id)
    if row is None:
        row = DiscogsListing(listing_id=listing_id)
        db.session.add(row)
    release = data.get("release") or {}
    price = data.get("price") or {}
    row.release_id = release.get("id")
    row.title = (release.get("description") or "")[:255] or None
    row.price = price.get("value")
    row.currency = price.get("currency")
    row.condition = data.get("condition")
    row.sleeve_condition = data.get("sleeve_condition")
    row.status = data.get("status")
    row.posted = (
        naive_utc(parse_timestamp(data["posted"])) if data.get("posted") else None
    )
    row.url = data.get("uri")
    row.external_id = (data.get("external_id") or "")[:255] or None
    row.location = (data.get("location") or "")[:255] or None
    row.last_seen = seen
    row.ended = row.status in DISCOGS_ENDED_STATUSES
    unit_id = unit_id_from_sku(row.external_id)
    if unit_id is not None and db.session.get(Unit, unit_id):
        row.unit_id = unit_id
    db.session.commit()
    return row


def mark_ended_discogs_listings(seen_ids):
    """
    Mark every DiscogsListing that isn't in the seller inventory as ended, and
    clear the flag on one that is back in it (unless its status is one of
    DISCOGS_ENDED_STATUSES, such as Sold), as mark_ended_listings does for
    eBay. Only call this after reading the whole inventory (the sync checks
    that it did), since anything missing from `seen_ids` is taken as gone.
    Returns how many were marked ended.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsListing

    ended = 0
    for row in db.session.query(DiscogsListing).all():
        if row.listing_id in seen_ids:
            row.ended = row.status in DISCOGS_ENDED_STATUSES
        elif not row.ended:
            row.ended = True
            ended += 1
    db.session.commit()
    return ended


def add_discogs_release(item):
    """
    Save one item of the Discogs collection (the JSON Discogs sent: its
    `basic_information`, `instance_id`, `folder_id`, `date_added` and `notes`)
    as a DiscogsInstance of its DiscogsRelease, adding or updating the
    release. The country and release date, and the master's year, aren't in
    the item, so they are fetched from Discogs.
    """
    from fbc.models.discogs import (
        Artist,
        DiscogsInstance,
        DiscogsRelease,
        Genre,
        Style,
    )
    from fbc import db, discogs_client

    logger.debug("add_discogs_release: " + str(item))
    info = item["basic_information"]
    instance_id = item["instance_id"]
    release_id = info["id"]
    full = discogs_client.release(release_id)
    artists = []
    genres = []
    styles = []
    for artist in info.get("artists") or []:
        adoc = db.session.query(Artist).filter_by(artist_id=artist["id"]).first()
        if not adoc:
            sn = ""
            n = artist["name"]
            if n.startswith("The "):
                sn = n.removeprefix("The ") + ", The"
                logger.debug("Replacing /" + n + "/ with /" + sn + "/")
            adoc = Artist(artist_id=artist["id"], name=n, sort_name=sn)
            db.session.add(adoc)
            db.session.commit()
            db.session.refresh(adoc)
            logger.info("Added artist " + str(adoc))
        artists.append(adoc)
    for genre in info.get("genres") or []:
        gdoc = db.session.query(Genre).filter_by(name=genre).first()
        if not gdoc:
            gdoc = Genre(name=genre)
            db.session.add(gdoc)
            db.session.commit()
            db.session.refresh(gdoc)
            logger.info("Added genre " + str(gdoc))
        genres.append(gdoc)
    for style in info.get("styles") or []:
        sdoc = db.session.query(Style).filter_by(name=style).first()
        if not sdoc:
            sdoc = Style(name=style)
            db.session.add(sdoc)
            db.session.commit()
            db.session.refresh(sdoc)
            logger.info("Added style " + str(sdoc))
        styles.append(sdoc)
    folder_id = item["folder_id"]
    master_id = 0
    master_year = info.get("year")
    if info.get("master_id"):
        master_id = info["master_id"]
        master_year = discogs_client.master(master_id).get("year")
    date_added = discogs_client.parse_timestamp(item["date_added"])
    labels = json.dumps(
        [
            {"name": label["name"], "catno": label.get("catno")}
            for label in info.get("labels") or []
        ]
    )
    country = full.get("country")
    released = full.get("released")
    formats = json.dumps(info.get("formats") or [])
    # TODO: make this whole thing atomic instead of if/then
    discogs_release = (
        db.session.query(DiscogsRelease).filter_by(release_id=release_id).first()
    )
    if discogs_release:
        if discogs_release.discogs_instances:
            logger.debug(
                "found existing instance(s): " + str(discogs_release.discogs_instances)
            )
            discogs_instance = (
                db.session.query(DiscogsInstance)
                .filter_by(instance_id=instance_id)
                .first()
            )
            if not discogs_instance:
                discogs_instance = DiscogsInstance(instance_id=instance_id)
            if discogs_instance not in discogs_release.discogs_instances:
                logger.debug("appending instances: " + str(discogs_instance))
                discogs_release.discogs_instances.append(discogs_instance)
            else:
                logger.debug("this instance already exists")
        else:
            logger.debug("no existing instance(s)")
            discogs_release.discogs_instances = [
                DiscogsInstance(instance_id=instance_id)
            ]
        discogs_release.title = info.get("title")
        discogs_release.year = info.get("year")
        discogs_release.artists = artists
        discogs_release.genres = genres
        discogs_release.styles = styles
        discogs_release.master_id = master_id
        discogs_release.master_year = master_year
        discogs_release.formats = formats
        discogs_release.labels = labels
        discogs_release.country = country
        discogs_release.released = released
        discogs_release.folder_id = folder_id
        discogs_release.notes = json.dumps(item.get("notes"))
        discogs_release.date_added = date_added
    else:
        discogs_release = DiscogsRelease(
            release_id=release_id,
            discogs_instances=[DiscogsInstance(instance_id=instance_id)],
            title=info.get("title"),
            year=info.get("year"),
            artists=artists,
            genres=genres,
            styles=styles,
            master_id=master_id,
            master_year=master_year,
            formats=formats,
            labels=labels,
            country=country,
            released=released,
            folder_id=folder_id,
            notes=json.dumps(item.get("notes")),
            date_added=date_added,
        )
        db.session.add(discogs_release)
    db.session.commit()
    db.session.refresh(discogs_release)
    logger.debug(discogs_release)


def forget_discogs_instance(instance):
    """
    Remove a DiscogsInstance that's no longer in the Discogs collection:
    unlink it from its Unit, drop it from its DiscogsRelease, and remove the
    release too once it has no instances left.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsRelease
    from fbc.models.inventory import Unit

    for u in db.session.query(Unit).filter_by(discogs_instance_id=instance.instance_id):
        logger.info("Updating Unit " + str(u))
        u.discogs_instance_id = None
    dr = (
        db.session.query(DiscogsRelease)
        .filter(DiscogsRelease.discogs_instances.contains(instance))
        .first()
    )
    if dr:
        logger.info("Updating DiscogsRelease " + str(dr))
        logger.debug("Removing instance from release")
        dr.discogs_instances.remove(instance)
        if len(dr.discogs_instances) == 0:
            logger.info("All instances are gone, removing DiscogsRelease " + str(dr))
            for u in db.session.query(Unit).filter_by(discogs_release_id=dr.release_id):
                logger.info("Updating Unit " + str(u))
                u.discogs_release_id = None
            db.session.delete(dr)
    logger.debug("Removing instance")
    db.session.delete(instance)
    db.session.commit()
