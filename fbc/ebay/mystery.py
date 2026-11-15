"""
Mystery box listings: a listing that sells `pick_count` random Units from one
Storage Box with each sale (see EbayListing.pick_from_box_id).

None of those Units is linked to the listing. Ship This picks them when the
order is shipped (`fill`), shows the picks as a pull list, and saves nothing
until Ship. The picks live in the page's hidden fields, so a reload of a
posted page doesn't re-roll them.
"""

import random

from sqlalchemy import or_


def box_listings(listing_ids):
    """{item id: EbayListing} for those of `listing_ids` that are mystery boxes."""
    from fbc import db
    from fbc.models.ebay import EbayListing

    if not listing_ids:
        return {}
    found = db.session.query(EbayListing).filter(
        EbayListing.item_id.in_(list(listing_ids)),
        EbayListing.pick_from_box_id.isnot(None),
        EbayListing.pick_count > 0,
    )
    return {listing.item_id: listing for listing in found}


def needs(orders):
    """
    [(listing, how many Units)] for every mystery box on `orders`: the Units
    per sale times the boxes bought.
    """
    wanted = {}
    for order in orders:
        for listing_id, quantity in order.listing_ids().items():
            wanted[listing_id] = wanted.get(listing_id, 0) + quantity
    return [
        (listing, wanted[item_id] * listing.pick_count)
        for item_id, listing in box_listings(wanted).items()
    ]


def available_units(box_id=None, exclude=()):
    """
    The Units in the Storage Box a record can be pulled from: unsold, with no
    Sales Receipt, not archived, and not on an active listing of their own
    (so a record can't sell twice). `box_id` None means any box.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit

    query = (
        db.session.query(Unit)
        .outerjoin(EbayListing, Unit.ebay_listing_id == EbayListing.item_id)
        .filter(Unit.sold.is_(False), Unit.sales_receipt_id.is_(None))
        .filter(Unit.archived.is_(False))
        .filter(or_(Unit.ebay_listing_id.is_(None), EbayListing.ended.is_(True)))
    )
    if box_id is not None:
        query = query.filter(Unit.storage_box_id == box_id)
    if exclude:
        query = query.filter(~Unit.id.in_(list(exclude)))
    return query.order_by(Unit.id).all()


def boxes_available(listing):
    """How many mystery boxes the listing's Storage Box can still fill."""
    if not listing.is_mystery_box():
        return 0
    return len(available_units(listing.pick_from_box_id)) // listing.pick_count


def active_listings():
    """The mystery box listings still for sale, for the home page."""
    from fbc import db
    from fbc.models.ebay import EbayListing

    return (
        db.session.query(EbayListing)
        .filter(
            EbayListing.pick_from_box_id.isnot(None),
            EbayListing.pick_count > 0,
            EbayListing.ended.is_(False),
            EbayListing.archived.is_(False),
        )
        .order_by(EbayListing.title)
        .all()
    )


def history(buyer):
    """
    (release ids, masters) of the Units `buyer` already got on earlier Sales
    Receipts, to keep from sending the same record twice.
    """
    from fbc import db
    from fbc.models.ebay import EbayOrder
    from fbc.models.inventory import Unit
    from fbc.models.sales import SalesReceipt

    if not buyer:
        return set(), set()
    units = (
        db.session.query(Unit)
        .filter(
            Unit.sales_receipt.has(
                SalesReceipt.ebay_orders.any(EbayOrder.buyer == buyer)
            )
        )
        .all()
    )
    releases = {u.discogs_release_id for u in units if u.discogs_release_id}
    masters = {u.discogs_master for u in units if u.discogs_master}
    return releases, masters


def choose(candidates, wanted, seen=(set(), set()), taken=(), rng=random):
    """
    Up to `wanted` of `candidates` (Units), at random. Units whose release or
    master the buyer `seen` before, or that repeats one already `taken` (Units
    picked so far), come last, so they're only used when nothing else is left.
    """
    seen_releases, seen_masters = seen
    releases = set(seen_releases) | {
        u.discogs_release_id for u in taken if u.discogs_release_id
    }
    masters = set(seen_masters) | {u.discogs_master for u in taken if u.discogs_master}
    pool = list(candidates)
    rng.shuffle(pool)
    chosen = []
    repeats = []
    for unit in pool:
        if len(chosen) == wanted:
            break
        repeat = bool(
            (unit.discogs_release_id and unit.discogs_release_id in releases)
            or (unit.discogs_master and unit.discogs_master in masters)
        )
        if repeat:
            repeats.append(unit)
            continue
        chosen.append(unit)
        if unit.discogs_release_id:
            releases.add(unit.discogs_release_id)
        if unit.discogs_master:
            masters.add(unit.discogs_master)
    return chosen + repeats[: wanted - len(chosen)]


def _unit(unit_id):
    from fbc import db
    from fbc.models.inventory import Unit

    try:
        return db.session.get(Unit, int(unit_id))
    except (TypeError, ValueError):
        return None


def fill(listing, wanted, buyer, values=None, rng=random):
    """
    The picks for one mystery box on an order, as a dict:

      units    the Units picked, in pull order
      notes    what changed since the page was shown (a Unit that's gone, a
               swap that wasn't allowed)
      changed  whether the picks differ from the ones posted, so a Ship
               shouldn't go ahead on them
      short    how many Units are still missing
      box      how many Units the Storage Box has to pick from
      fill_box the other Storage Box short picks come from, or None

    `values` is the posted form (None the first time): its `pick-<item id>`
    fields are the earlier picks, `swap-<unit id>` a Unit typed in to replace
    one, `fill-<item id>` the other Storage Box to fill from, and an `action`
    of `reroll:<unit id>` drops that pick for another.
    """
    from fbc import db
    from fbc.models.inventory import StorageBox

    values = values if values is not None else {}
    getlist = getattr(values, "getlist", lambda _: [])
    notes = []
    changed = False
    allowed = {u.id for u in available_units()}

    posted = []
    for unit_id in getlist("pick-" + listing.item_id):
        unit = _unit(unit_id)
        if unit is None or unit.id not in allowed:
            notes.append("Unit " + str(unit_id) + " isn't available any more.")
            changed = True
        elif unit not in posted:
            posted.append(unit)
    picks = posted[:wanted]

    skipped = set()
    action = values.get("action", "") if values else ""
    for unit in list(picks):
        typed = (values.get("swap-" + str(unit.id)) or "").strip() if values else ""
        if typed:
            swap = _unit(typed.lstrip("#"))
            if swap is None or swap.id not in allowed:
                notes.append(
                    "Unit " + typed + " can't be swapped in: it isn't an unsold"
                    " Unit without a listing of its own."
                )
            elif swap in picks:
                notes.append("Unit " + typed + " is already picked.")
            else:
                picks[picks.index(unit)] = swap
                skipped.add(unit.id)
        if action == "reroll:" + str(unit.id):
            picks.remove(unit)
            skipped.add(unit.id)

    fill_box = None
    fill_id = (values.get("fill-" + listing.item_id) or "") if values else ""
    if fill_id.isdigit():
        fill_box = db.session.get(StorageBox, int(fill_id))
    if fill_box is not None and fill_box.id == listing.pick_from_box_id:
        fill_box = None

    seen = history(buyer)
    box = available_units(listing.pick_from_box_id)
    for source in (box, available_units(fill_box.id) if fill_box else []):
        left = wanted - len(picks)
        if left <= 0:
            break
        chosen_ids = {u.id for u in picks}
        pool = [u for u in source if u.id not in chosen_ids and u.id not in skipped]
        picks.extend(choose(pool, left, seen, picks, rng))
    if values and [u.id for u in picks] != [u.id for u in posted]:
        changed = True
    return {
        "units": picks,
        "notes": notes,
        "changed": changed,
        "short": wanted - len(picks),
        "box": len(box),
        "fill_box": fill_box,
    }


def unit_price(order_items, listing):
    """
    What one picked Unit sold for: its box's item price split over the Units
    per sale. None if the order has no price for the listing.
    """
    for item in order_items:
        if item.ebay_listing_id == listing.item_id and item.price is not None:
            return round(item.price / listing.pick_count, 2)
    return None


def other_boxes(listing):
    """[(Storage Box, unsold Units in it)] to fill a short box from."""
    from fbc import db
    from fbc.models.inventory import StorageBox

    boxes = []
    for box in db.session.query(StorageBox).order_by(StorageBox.name):
        if box.id == listing.pick_from_box_id:
            continue
        count = len(available_units(box.id))
        if count:
            boxes.append((box, count))
    return boxes
