import logging
import json
import datetime
from flask import request
from flask_wtf import FlaskForm

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
    else:
        logger.debug("Listing already exists!")
    db.session.commit()
    db.session.refresh(ebay_listing)
    logger.debug(ebay_listing)


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
