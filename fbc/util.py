import logging
import json
import datetime
from flask import request
from flask_wtf import FlaskForm
from flask_appbuilder import ModelView
from flask_appbuilder.models.filters import BaseFilter
from flask_appbuilder.models.sqla.filters import get_field_setup_query
from sqlalchemy import or_

from fbc import tracking

logger = logging.getLogger()


def fprice(num):
    return "$" + f"{num:.2f}"


def fpercent(num):
    np = num * 100.0
    return f"{np:.2f}" + "%"


def add_ebay_listing(item):
    from fbc import db
    from fbc.models.ebay import EbayListing

    logger.debug(item)
    item_id = item["ItemID"]
    title = item["Title"]
    price = float(item["BuyItNowPrice"]["value"])
    url = item["ListingDetails"]["ViewItemURL"]
    d = datetime.datetime.strptime(
        item["ListingDetails"]["StartTime"], "%Y-%m-%dT%H:%M:%S.000Z"
    )
    # TODO: make this whole thing atomic instead of if/then
    ebay_listing = db.session.query(EbayListing).filter_by(item_id=item_id).first()
    if not ebay_listing:
        ebay_listing = EbayListing(
            item_id=item_id, title=title, price=price, url=url, start_time=d
        )
        db.session.add(ebay_listing)
        link_unit_by_sku(item)
    else:
        logger.debug("Listing already exists!")
    db.session.commit()
    db.session.refresh(ebay_listing)
    logger.debug(ebay_listing)


def link_unit_by_sku(item):
    """
    Listings published from fbc carry the Unit's id in their Custom label
    (SKU). Link such a listing to its Unit if the Unit has no listing yet.
    """
    from fbc import db
    from fbc.listing import unit_id_from_sku
    from fbc.models.inventory import Unit

    unit_id = unit_id_from_sku(item.get("SKU"))
    if unit_id is None:
        return
    unit = db.session.get(Unit, unit_id)
    if unit and not unit.ebay_listing_id:
        unit.ebay_listing_id = item["ItemID"]
        logger.info("Linked unit " + str(unit_id) + " to " + str(item["ItemID"]))


def find_relisted_listing(item, remote_item_ids):
    """
    Look for a local EbayListing that is actually the same physical item as
    `item`, just relisted under a new eBay ItemID.

    eBay gives a relisted item a brand new ItemID, so there is no ID to join
    on. The best signal we have is the listing title, which eBay carries
    over unchanged on a relist. A candidate old listing must:
      - have the same title as the new item
      - no longer be present in eBay's current active listings (an item that
        is still active can't be the "old" side of a relist)
      - still be linked to a Unit that hasn't sold (so it's genuinely
        pending relist, not a stale/ended listing with no inventory tie)

    Returns the matching EbayListing, or None if there isn't exactly one
    unambiguous match.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit

    candidates = (
        db.session.query(EbayListing)
        .filter_by(title=item["Title"])
        .filter(EbayListing.item_id != item["ItemID"])
        .filter(~EbayListing.item_id.in_(remote_item_ids))
        .all()
    )
    matches = [
        c
        for c in candidates
        if db.session.query(Unit)
        .filter_by(ebay_listing_id=c.item_id)
        .filter(Unit.sold.isnot(True))
        .first()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "Multiple possible old listings for relisted item "
            + str(item["ItemID"])
            + " ("
            + item["Title"]
            + "), skipping auto-relink"
        )
    return None


def relist_ebay_listing(old_listing, item):
    """
    Record a relisted item under its new ItemID, re-point every model that
    referenced the old listing at the new one, and remove the old listing.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing, EbayOrder, EbayOrderItem
    from fbc.models.inventory import Unit

    old_item_id = old_listing.item_id
    add_ebay_listing(item)
    new_item_id = item["ItemID"]

    # A mystery box stays one: it has no Units to find it by.
    new_listing = db.session.get(EbayListing, new_item_id)
    new_listing.pick_from_box_id = old_listing.pick_from_box_id
    new_listing.pick_count = old_listing.pick_count

    db.session.query(Unit).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.query(EbayOrder).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    # Ship This finds an order's Units through its items' listings.
    db.session.query(EbayOrderItem).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.commit()

    db.session.delete(old_listing)
    db.session.commit()
    logger.info("Relisted " + old_item_id + " -> " + new_item_id)


def save_renewals(items, fetched):
    """Save when each of eBay's active `items` renews, as read at `fetched`."""
    from fbc import db, insertions
    from fbc.models.ebay import EbayListing

    for item in items:
        listing = db.session.get(EbayListing, item["ItemID"])
        renews = insertions.renewal_time(item, fetched)
        if listing is not None and renews is not None:
            listing.renews_at = renews
    db.session.commit()


def mark_ended_listings(remote_item_ids, previous_sync=None):
    """
    Mark every listing that isn't in eBay's active list as ended, and clear
    the flag on one that is back in it. Only call this after reading the
    whole active list (listing.fetch_active_items checks that it did), since
    anything missing from `remote_item_ids` is taken as ended.

    A listing's end_time is when the previous successful sync last saw it
    active, since it ended some time after that; it's start_time when there
    was no earlier sync, which counts the start and no renewals.
    Returns how many were marked ended.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing

    ended = 0
    for listing in db.session.query(EbayListing).all():
        if listing.item_id in remote_item_ids:
            if listing.ended:
                listing.ended = False
                listing.end_time = None
        elif not listing.ended:
            seen = previous_sync
            if seen is None or (listing.start_time and seen < listing.start_time):
                seen = listing.start_time
            listing.ended = True
            listing.end_time = seen
            ended += 1
    db.session.commit()
    return ended


def _naive_utc(when):
    """A datetime from Discogs (with a zone, or none) as naive UTC, as fbc stores it."""
    if when is None or when.tzinfo is None:
        return when
    return when.astimezone(datetime.timezone.utc).replace(tzinfo=None)


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
    from discogs_client.utils import parse_timestamp

    from fbc import db
    from fbc.listing import unit_id_from_sku
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
        _naive_utc(parse_timestamp(data["posted"])) if data.get("posted") else None
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


def _money(amount):
    """An amount from the Trading API ({"value": "4.37"}) as a float, or None."""
    try:
        return float(amount["value"])
    except (KeyError, TypeError, ValueError):
        return None


def order_items(item):
    """EbayOrderItems for every transaction (item bought) in a GetOrders order."""
    from fbc.models.ebay import EbayOrderItem

    transactions = (item.get("TransactionArray") or {}).get("Transaction") or []
    # ebaysdk gives a lone transaction as a dict rather than a list of one.
    if isinstance(transactions, dict):
        transactions = [transactions]
    items = []
    for t in transactions:
        try:
            quantity = int(t.get("QuantityPurchased") or 1)
        except ValueError:
            quantity = 1
        items.append(
            EbayOrderItem(
                ebay_listing_id=t["Item"]["ItemID"],
                title=(t["Item"].get("Title") or "")[:100],
                quantity=quantity,
                price=_money(t.get("TransactionPrice")),
            )
        )
    return items


def update_tracking(order, item):
    """Keep an order's tracking as eBay has it; a blank never wipes it."""
    carrier, number = tracking.from_order(item)
    if number:
        order.shipping_carrier = carrier
        order.tracking_number = number


def add_ebay_order(item):
    """
    Add a GetOrders order, with every item on it, if its first item's
    listing is in fbc. An order fbc already has only gets the items and
    shipping it was synced without, from before fbc kept them.
    """
    from fbc import db

    logger.debug(item)
    order_id = item["OrderID"]
    price = float(item["Subtotal"]["value"])
    buyer = item["BuyerUserID"]
    created_time = datetime.datetime.strptime(
        item["CreatedTime"], "%Y-%m-%dT%H:%M:%S.000Z"
    )
    items = order_items(item)
    carrier, tracking_number = tracking.from_order(item)
    title = items[0].title
    ebay_listing_id = items[0].ebay_listing_id
    shipping = _money(
        (item.get("ShippingServiceSelected") or {}).get("ShippingServiceCost")
    )
    from fbc.models.ebay import EbayListing
    from fbc.models.ebay import EbayOrder

    ebay_listing = (
        db.session.query(EbayListing).filter_by(item_id=ebay_listing_id).first()
    )
    if not ebay_listing:
        logger.warn("No eBay listing in DB for " + str(ebay_listing_id))
        # TODO: should we make orders for things without listings?
    else:
        logger.info("Found eBay listing " + str(ebay_listing))
        # TODO: make this whole thing atomic instead of if/then
        ebay_order = db.session.query(EbayOrder).filter_by(order_id=order_id).first()
        if not ebay_order:
            ebay_order = EbayOrder(
                order_id=order_id,
                price=price,
                shipping=shipping,
                shipping_carrier=carrier,
                tracking_number=tracking_number,
                buyer=buyer,
                created_time=created_time,
                title=title,
                ebay_listing_id=ebay_listing.item_id,
                items=items,
            )
            db.session.add(ebay_order)
        else:
            logger.debug("Order already exists!")
            if not ebay_order.items:
                ebay_order.items = items
            if ebay_order.shipping is None:
                ebay_order.shipping = shipping
            update_tracking(ebay_order, item)
        db.session.commit()
        db.session.refresh(ebay_order)
        logger.debug(ebay_order)


def add_discogs_release(item):
    from fbc.models.discogs import (
        Artist,
        DiscogsInstance,
        DiscogsRelease,
        Genre,
        Style,
    )
    from fbc import db

    logger.debug("add_discogs_release: " + str(item))
    artists = []
    genres = []
    styles = []
    for artist in item.release.artists:
        adoc = db.session.query(Artist).filter_by(artist_id=artist.id).first()
        if not adoc:
            sn = ""
            n = artist.name
            if n.startswith("The "):
                sn = n.removeprefix("The ") + ", The"
                logger.debug("Replacing /" + n + "/ with /" + sn + "/")
            adoc = Artist(artist_id=artist.id, name=artist.name, sort_name=sn)
            db.session.add(adoc)
            db.session.commit()
            db.session.refresh(adoc)
            logger.info("Added artist " + str(adoc))
        artists.append(adoc)
    for genre in item.release.genres:
        gdoc = db.session.query(Genre).filter_by(name=genre).first()
        if not gdoc:
            gdoc = Genre(name=genre)
            db.session.add(gdoc)
            db.session.commit()
            db.session.refresh(gdoc)
            logger.info("Added genre " + str(gdoc))
        genres.append(gdoc)
    for style in item.release.styles:
        sdoc = db.session.query(Style).filter_by(name=style).first()
        if not sdoc:
            sdoc = Style(name=style)
            db.session.add(sdoc)
            db.session.commit()
            db.session.refresh(sdoc)
            logger.info("Added style " + str(sdoc))
        styles.append(sdoc)
    folder_id = item.folder_id
    master_id = 0
    master_year = item.release.year
    if item.release.master:
        master_id = item.release.master.id
        master_year = item.release.master.year
    date_added = item.date_added
    labels = json.dumps(
        [{"name": label.name, "catno": label.catno} for label in item.release.labels]
    )
    country = item.release.country
    # TODO: make this whole thing atomic instead of if/then
    discogs_release = (
        db.session.query(DiscogsRelease).filter_by(release_id=item.id).first()
    )
    if discogs_release:
        if discogs_release.discogs_instances:
            logger.debug(
                "found existing instance(s): " + str(discogs_release.discogs_instances)
            )
            discogs_instance = (
                db.session.query(DiscogsInstance)
                .filter_by(instance_id=item.instance_id)
                .first()
            )
            if not discogs_instance:
                discogs_instance = DiscogsInstance(instance_id=item.instance_id)
            if discogs_instance not in discogs_release.discogs_instances:
                logger.debug("appending instances: " + str(discogs_instance))
                discogs_release.discogs_instances.append(discogs_instance)
            else:
                logger.debug("this instance already exists")
        else:
            logger.debug("no existing instance(s)")
            discogs_release.discogs_instances = [
                DiscogsInstance(instance_id=item.instance_id)
            ]
        discogs_release.title = item.release.title
        discogs_release.year = item.release.year
        discogs_release.artists = artists
        discogs_release.genres = genres
        discogs_release.styles = styles
        discogs_release.master_id = master_id
        discogs_release.master_year = master_year
        discogs_release.formats = json.dumps(item.release.formats)
        discogs_release.labels = labels
        discogs_release.country = country
        discogs_release.released = item.release.fetch("released")
        discogs_release.folder_id = folder_id
        discogs_release.notes = json.dumps(item.notes)
        discogs_release.date_added = date_added
    else:
        discogs_release = DiscogsRelease(
            release_id=item.id,
            discogs_instances=[DiscogsInstance(instance_id=item.instance_id)],
            title=item.release.title,
            year=item.release.year,
            artists=artists,
            genres=genres,
            styles=styles,
            master_id=master_id,
            master_year=master_year,
            formats=json.dumps(item.release.formats),
            labels=labels,
            country=country,
            released=item.release.fetch("released"),
            folder_id=folder_id,
            notes=json.dumps(item.notes),
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


class CustomForm(FlaskForm):
    """
    A custom FlaskForm which reads data from request params
    """

    @classmethod
    def refresh(cls, obj=None):
        kw = dict(obj=obj)
        if request.method == "GET":
            kw["formdata"] = request.args
            logger.info(request.args)
        form = cls(**kw)
        return form


class CustomModelView(ModelView):
    """
    A custom ModelView which overrides the various CRUD templates
    """

    list_template = "crud/list.html"
    edit_template = "crud/edit.html"
    add_template = "crud/add.html"
    show_template = "crud/show.html"

    def _init_properties(self):
        """
        Makes every view searchable by its primary key. Flask-AppBuilder
        leaves primary keys out of the default search, and views that list
        their own search_columns mostly forgot the id. An integer id also
        gets FilterIdMatches, so a range like 100-200 works.
        """
        super()._init_properties()
        pk = self.datamodel.get_pk_name()
        if not isinstance(pk, str) or pk not in self.datamodel.list_columns:
            return
        if pk not in self.search_columns:
            self.search_columns = [pk] + list(self.search_columns)
            self._filters = self.datamodel.get_filters(self.search_columns)
        if self.datamodel.is_integer(pk):
            # The tables hold filter instances, not classes.
            mine = FilterIdMatches(pk, self.datamodel)
            for table in (self._filters._search_filters, self._filters._all_filters):
                table[pk] = [mine] + [
                    f for f in table[pk] if not isinstance(f, FilterIdMatches)
                ]


class FilterIdMatches(BaseFilter):
    """
    Matches an integer id typed as "42", a range "100-200", or a
    comma-separated mix of both ("5, 100-200"). Anything that doesn't parse
    matches nothing, the same as Flask-AppBuilder's own integer filters.
    """

    name = "Matches id"
    arg_name = "idm"

    @staticmethod
    def parse(text):
        """Returns [(low, high), ...], or None if the text isn't valid."""
        spans = []
        for part in str(text).split(","):
            bounds = [b.strip() for b in part.split("-")]
            if len(bounds) > 2 or not all(b.isdigit() for b in bounds):
                return None
            low, high = int(bounds[0]), int(bounds[-1])
            spans.append((min(low, high), max(low, high)))
        return spans

    def apply(self, query, value):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        spans = self.parse(value)
        if not spans:
            return query.filter(False)
        return query.filter(
            or_(
                *[
                    field == low if low == high else field.between(low, high)
                    for low, high in spans
                ]
            )
        )
