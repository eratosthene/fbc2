"""
The Discogs and eBay syncs. Each is one function here, run by both the Sync
buttons (in the background, see fbc.sync.start) and the `flask sync` commands
(in the foreground, see fbc.sync.run), so the two can't drift apart.

Each records when it last finished successfully (sync.record_finished), under
the name in its Job, for a scheduler to read with sync.last_finished.
"""

import datetime
import logging
from collections import namedtuple

from ebaysdk.exception import ConnectionError

from fbc import devmode, discogs, sync
from fbc.listing import OAuthError, fetch_active_items, trading
from fbc.util import (
    add_discogs_release,
    add_ebay_listing,
    add_ebay_order,
    find_relisted_listing,
    forget_discogs_instance,
    mark_ended_listings,
    relist_ebay_listing,
    save_renewals,
)

logger = logging.getLogger()

# The sync_record names, which are also the job kinds: a button and a command
# with the same kind never run together.
DISCOGS_SYNC = "discogs"
ORDERS_SYNC = "ebay-orders"

# A Job is what sync.start and sync.run take: the kind, a title, the function.
Job = namedtuple("Job", "kind title fn")


def _job(kind, title, fn):
    # In dev mode every sync only says that it's off, and finishes as done.
    return Job(kind, title, devmode.skip_sync if devmode.on() else fn)


def discogs_job():
    return _job(DISCOGS_SYNC, "Discogs sync", sync_discogs)


def ebay_listings_job():
    return _job(sync.LISTINGS_SYNC, "eBay listings sync", sync_ebay_listings)


def ebay_orders_job(update=False, deep=False):
    """
    The order syncs share a kind, so only one of them runs at a time. `update`
    refreshes orders fbc already has; `deep` looks back 88 days, not 10.
    """
    if deep:
        title, message = "eBay orders deep sync", "Deep updating eBay orders..."
    elif update:
        title, message = "eBay orders sync", "Updating eBay orders..."
    else:
        title, message = "eBay orders sync", "Adding new eBay orders..."

    def run():
        sync_ebay_orders(update and not deep, 88 if deep else 10, message)

    return _job(ORDERS_SYNC, title, run)


def sync_discogs():
    from fbc import db
    from fbc.models.discogs import DiscogsRelease, DiscogsInstance, Folder

    started = sync.utcnow()
    logger.info("Updating discogs...")
    d = discogs.client()
    me = d.identity()
    folders = me.collection_folders
    for folder in folders:
        if folder.id > 0:
            res = db.session.query(Folder).filter_by(folder_id=folder.id).all()
            if len(res) == 0:
                fdoc = Folder(folder_id=folder.id, name=folder.name)
                db.session.add(fdoc)
                db.session.commit()
                db.session.refresh(fdoc)
                logger.info("Added folder " + str(fdoc))
    releases = me.collection_folders[0].releases
    local_releases = db.session.query(DiscogsRelease).all()
    local_instances = db.session.query(DiscogsInstance).all()

    logger.info("Total releases: " + str(len(releases)))
    logger.info("Total local releases: " + str(len(local_releases)))
    for item in releases:
        logger.info("Checking discogs: " + str(item))
        discogs_instance = (
            db.session.query(DiscogsInstance)
            .filter_by(instance_id=item.instance_id)
            .first()
        )
        if not discogs_instance:
            logger.info("Adding " + str(item))
            add_discogs_release(item)

    for item in local_instances:
        if not any(x.instance_id == item.instance_id for x in releases):
            logger.info(
                "Local instance " + str(item.instance_id) + " should be removed"
            )
            forget_discogs_instance(item)
    sync.record_finished(DISCOGS_SYNC, started)
    logger.info("Discogs sync finished.")


def sync_ebay_listings():
    from fbc import db
    from fbc.models.ebay import EbayListing

    # Reads all of eBay's active list or raises, which fails the sync
    # before anything is marked ended.
    previous_sync = sync.last_finished(sync.LISTINGS_SYNC)
    started = sync.utcnow()
    all_items = fetch_active_items()

    # Process every page's items together (rather than page by page) so
    # that a relisted item's old EbayListing -- which by definition has
    # fallen out of eBay's active list entirely -- is matched up with
    # full knowledge of every ItemID eBay is reporting as active right
    # now, not just whatever page happened to contain the new listing.
    local_listings = db.session.query(EbayListing).all()
    logger.info("Total listings: " + str(len(all_items)))
    logger.info("Total local listings: " + str(len(local_listings)))
    remote_item_ids = {item["ItemID"] for item in all_items}
    for item in all_items:
        logger.info("Checking ebay: " + str(item["ItemID"]))
        if not db.session.query(EbayListing).filter_by(item_id=item["ItemID"]).first():
            old_listing = find_relisted_listing(item, remote_item_ids)
            if old_listing:
                logger.info(
                    "Relisted item detected: "
                    + old_listing.item_id
                    + " -> "
                    + item["ItemID"]
                )
                relist_ebay_listing(old_listing, item)
            else:
                logger.info("Adding " + str(item["ItemID"]))
                add_ebay_listing(item)
    save_renewals(all_items, started)
    ended = mark_ended_listings(remote_item_ids, previous_sync)
    logger.info("Marked " + str(ended) + " listings as ended.")
    sync.record_finished(sync.LISTINGS_SYNC, started)
    logger.info("eBay listings sync finished.")


def _save_orders(update, api, response):
    from fbc import db
    from fbc.models.ebay import EbayOrder

    logging.info("eBay response: " + api.response_status())
    resp = response.dict()
    orders = resp["OrderArray"]["Order"]
    local_orders = db.session.query(EbayOrder).all()
    logger.info("Total orders: " + str(len(orders)))
    logger.info("Total local orders: " + str(len(local_orders)))
    for item in orders:
        logger.info("Checking ebay: " + str(item["OrderID"]))
        order = db.session.query(EbayOrder).filter_by(order_id=item["OrderID"]).first()
        if not order:
            logger.info("Adding " + str(item["OrderID"]))
            add_ebay_order(item)
        elif update or not order.items or not order.tracking_number:
            # Orders synced before fbc kept every item get them now, and
            # an order still without tracking checks whether it has some.
            logger.info("Updating " + str(item["OrderID"]))
            add_ebay_order(item)


def sync_ebay_orders(update, days, message):
    logger.info(message)
    started = sync.utcnow()
    try:
        current_time = datetime.datetime.now() + datetime.timedelta(days=1)
        start_time = current_time + datetime.timedelta(days=-days)
        api = trading(compatibility=None)

        response = api.execute(
            "GetOrders",
            {
                "CreateTimeFrom": str(start_time)[0:19],
                "CreateTimeTo": str(current_time)[0:19],
                "OrderStatus": "Completed",
            },
        )
        _save_orders(update, api, response)
    except OAuthError as e:
        logger.error(str(e))
        return
    except ConnectionError as e:
        logging.error(e)
        logging.error(e.response.dict())
        return
    sync.record_finished(ORDERS_SYNC, started)
    logger.info("eBay orders sync finished.")
