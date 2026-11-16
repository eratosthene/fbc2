"""
`flask pictures-import`: copy the pictures of eBay listings down to the Units
linked to them (#140), so they can be used again, for Discogs by hand or a
relisting. It reads each listing's picture addresses with GetItem and fetches
the files from eBay's picture host.

Safe to run again: a Unit that has pictures is left alone, so a second run
only does what the first couldn't. A listing is all or nothing, so a Unit
never ends up with some of its pictures.
"""

import logging

from fbc.core import devmode
from fbc.ebay import api, pictures
from fbc.pictures import images, store

logger = logging.getLogger()


def _candidates(include_sold, item):
    """
    [(listing, [Units with no stored picture])] for the listings worth
    reading, in item id order, and counts of what was passed over.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit
    from fbc.models.pictures import UnitPicture

    skipped = {"mystery": 0, "have": 0, "sold": 0}
    has_pictures = {
        unit_id for (unit_id,) in db.session.query(UnitPicture.unit_id).distinct()
    }
    query = (
        db.session.query(Unit)
        .filter(Unit.ebay_listing_id.isnot(None))
        .order_by(Unit.ebay_listing_id, Unit.id)
    )
    if item:
        query = query.filter(Unit.ebay_listing_id == item)
    by_listing = {}
    for unit in query.all():
        if unit.sold and not include_sold:
            skipped["sold"] += 1
        elif unit.id in has_pictures:
            skipped["have"] += 1
        else:
            by_listing.setdefault(unit.ebay_listing_id, []).append(unit)
    found = []
    for item_id, units in by_listing.items():
        listing = db.session.get(EbayListing, item_id)
        if listing is None:
            continue
        if listing.is_mystery_box():
            # Its Units aren't linked to it, but say so if one is.
            skipped["mystery"] += 1
            continue
        found.append((listing, units))
    return found, skipped


def _name(url, n):
    name = url.rsplit("/", 1)[-1].split("?")[0]
    return name if "." in name else "picture-" + str(n) + ".jpg"


def import_pictures(apply=False, limit=None, item=None, include_sold=False, echo=print):
    """
    Give each Unit linked to an eBay listing, and with no pictures of its own,
    the listing's pictures. Without `apply` it only reads the listings' picture
    addresses and says what it would do. Units that are sold are left out
    unless `include_sold`. `limit` stops after that many listings, `item`
    does one. Returns counts.
    """
    from fbc import db

    counts = {
        "listings": 0,
        "units": 0,
        "pictures": 0,
        "no_pictures": 0,
        "unreadable": 0,
        "have": 0,
        "sold": 0,
        "mystery": 0,
    }
    if devmode.on():
        echo(devmode.message("Importing pictures from eBay"))
        return counts
    if store.folder() is None:
        echo("PICTURES_DIR isn't set in config.py, so there's nowhere to keep them.")
        return counts
    found, skipped = _candidates(include_sold, item)
    counts.update(skipped)
    for listing, units in found[:limit] if limit else found:
        label = listing.item_id + " (" + (listing.title or "") + ")"
        urls, errors = api.item_pictures(listing.item_id)
        if errors:
            counts["unreadable"] += 1
            echo(label + ": couldn't read it from eBay: " + "; ".join(errors))
            continue
        if not urls:
            counts["no_pictures"] += 1
            echo(label + ": eBay has no pictures for it.")
            continue
        if not apply:
            counts["listings"] += 1
            counts["units"] += len(units)
            counts["pictures"] += len(urls) * len(units)
            echo(
                label
                + ": would copy "
                + str(len(urls))
                + " pictures to "
                + str(len(units))
                + " Unit"
                + ("" if len(units) == 1 else "s")
            )
            continue
        files = []
        failed = None
        for n, url in enumerate(urls, start=1):
            data, error = pictures.fetch_picture(url)
            if error is None:
                try:
                    images.working_copy(data)
                except ValueError as e:
                    error = str(e)
            if error:
                failed = url + ": " + error
                break
            files.append((data, _name(url, n)))
        if failed:
            counts["unreadable"] += 1
            echo(label + ": left alone, " + failed)
            continue
        for unit in units:
            for data, name in files:
                store.add(unit, data, name)
        db.session.commit()
        counts["listings"] += 1
        counts["units"] += len(units)
        counts["pictures"] += len(files) * len(units)
        echo(
            label
            + ": copied "
            + str(len(files))
            + " pictures to "
            + ", ".join("Unit " + str(u.id) for u in units)
        )
    return counts
