import logging
import json
import datetime
from flask import request
from flask_wtf import FlaskForm
from flask_appbuilder import ModelView

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
    unit = db.session.query(Unit).get(unit_id)
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
    from fbc.models.ebay import EbayOrder
    from fbc.models.inventory import Unit

    old_item_id = old_listing.item_id
    add_ebay_listing(item)
    new_item_id = item["ItemID"]

    db.session.query(Unit).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.query(EbayOrder).filter_by(ebay_listing_id=old_item_id).update(
        {"ebay_listing_id": new_item_id}
    )
    db.session.commit()

    db.session.delete(old_listing)
    db.session.commit()
    logger.info("Relisted " + old_item_id + " -> " + new_item_id)


def add_ebay_order(item):
    from fbc import db

    logger.debug(item)
    order_id = item["OrderID"]
    price = float(item["Subtotal"]["value"])
    buyer = item["BuyerUserID"]
    created_time = datetime.datetime.strptime(
        item["CreatedTime"], "%Y-%m-%dT%H:%M:%S.000Z"
    )
    title = item["TransactionArray"]["Transaction"][0]["Item"]["Title"]
    ebay_listing_id = item["TransactionArray"]["Transaction"][0]["Item"]["ItemID"]
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
                buyer=buyer,
                created_time=created_time,
                title=title,
                ebay_listing_id=ebay_listing.item_id,
            )
            db.session.add(ebay_order)
        else:
            logger.debug("Order already exists!")
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
    
    list_template = 'crud/list.html'
    edit_template = 'crud/edit.html'
    add_template = 'crud/add.html'
    show_template = 'crud/show.html'
