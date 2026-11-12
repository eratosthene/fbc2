import datetime
import functools
import logging
from collections import Counter

from ebaysdk.exception import ConnectionError
from flask import flash, jsonify, redirect, request, url_for
from flask_appbuilder import IndexView
from flask_appbuilder.views import expose

from fbc import devmode, discogs, insertions, mystery, sync
from fbc.listing import OAuthError, fetch_active_items, trading
from fbc.util import add_ebay_listing
from fbc.util import add_ebay_order
from fbc.util import add_discogs_release
from fbc.util import find_relisted_listing
from fbc.util import forget_discogs_instance
from fbc.util import mark_ended_listings
from fbc.util import save_renewals
from fbc.util import relist_ebay_listing

logger = logging.getLogger()


def home_totals():
    """Everything the home page shows, worked out from the database."""
    from fbc import db, finance
    from fbc.models.inventory import PurchaseLot, StorageBox
    from fbc.models.expenses import (
        category_label,
        expense_totals,
        purchase_order_expense_total,
    )
    from fbc.models.supplies import PurchaseOrder, Supply

    # The Units and receipts are loaded once, in fbc.finance, and the
    # per-lot figures come from there too, so the Purchase Lots list
    # shows the same numbers.
    inv = finance.Inventory()
    units = inv.units
    # sold=False, like filter_by(sold=False), leaves out NULL as well as True.
    unsold = [u for u in units if u.sold is False]
    unsold_per_box = Counter(u.storage_box_id for u in unsold)

    stock_total = len(unsold)
    unit_total = len(units)
    lots = db.session.query(PurchaseLot).all()
    boxes = db.session.query(StorageBox).all()
    box_ids = {b.id for b in boxes}
    box_totals = {}
    pos = db.session.query(PurchaseOrder).all()
    supply_total = 0
    totals = {
        "capital": 0,
        "gross": 0,
        "net": 0,
        "fees": 0,
        "feepc": 0,
        "profit": 0,
        "roi": 0,
    }
    for b in boxes:
        box_totals[b.id] = {}
        box_totals[b.id]["name"] = b.name
        box_totals[b.id]["instock"] = unsold_per_box[b.id]
        box_totals[b.id]["capital"] = 0.0
        box_totals[b.id]["gross"] = 0.0
        box_totals[b.id]["net"] = 0.0
    box_totals[0] = {}
    box_totals[0]["name"] = "No Box Assigned"
    box_totals[0]["instock"] = unsold_per_box[None]
    box_totals[0]["capital"] = 0.0
    box_totals[0]["gross"] = 0.0
    box_totals[0]["net"] = 0.0
    lot_totals = finance.lot_figures(lots, inv)
    for lot in lots:
        totals["capital"] += lot.price
    for po in pos:
        supply_total += po.price
    # Expenses on a Purchase Order are part of its price, and count as
    # expenses, not supplies.
    supply_total -= purchase_order_expense_total()
    for u in units:
        # A unit whose box no longer exists counts as having no box.
        box = box_totals[u.storage_box_id if u.storage_box_id in box_ids else 0]
        # A unit with no lot (or whose lot no longer exists) adds no
        # capital, but its sales still count toward its box and the totals.
        lot_total = lot_totals.get(u.purchase_lot_id)
        sale = inv.sale(u)
        if sale:
            c_sold_price, c_net_sold = sale
            box["gross"] += c_sold_price
            box["net"] += c_net_sold
            totals["gross"] += c_sold_price
            totals["net"] += c_net_sold
        if lot_total:
            box["capital"] += lot_total["perunit"]
    for lot in lots:
        totals["fees"] += lot_totals[lot.id]["fees"]
        totals["profit"] += lot_totals[lot.id]["profit"]
    for b in boxes:
        box_totals[b.id]["fees"] = round(
            box_totals[b.id]["gross"] - box_totals[b.id]["net"], 2
        )
        if box_totals[b.id]["gross"] > 0:
            box_totals[b.id]["feepc"] = round(
                box_totals[b.id]["fees"] / box_totals[b.id]["gross"] * 100, 2
            )
        else:
            box_totals[b.id]["feepc"] = 0.0
        box_totals[b.id]["profit"] = round(
            box_totals[b.id]["net"] - box_totals[b.id]["capital"], 2
        )
        if box_totals[b.id]["capital"] > 0:
            box_totals[b.id]["roi"] = round(
                box_totals[b.id]["profit"] / box_totals[b.id]["capital"] * 100, 2
            )
        else:
            box_totals[b.id]["roi"] = 0.0
    box_totals[0]["fees"] = round(box_totals[0]["gross"] - box_totals[0]["net"], 2)
    if box_totals[0]["gross"] > 0:
        box_totals[0]["feepc"] = round(
            box_totals[0]["fees"] / box_totals[0]["gross"] * 100, 2
        )
    else:
        box_totals[0]["feepc"] = 0.0
    box_totals[0]["profit"] = round(box_totals[0]["net"] - box_totals[0]["capital"], 2)
    if box_totals[0]["capital"] > 0:
        box_totals[0]["roi"] = round(
            box_totals[0]["profit"] / box_totals[0]["capital"] * 100, 2
        )
    else:
        box_totals[0]["roi"] = 0.0
    if totals["gross"] > 0:
        totals["feepc"] = round(totals["fees"] / totals["gross"] * 100, 2)
    else:
        totals["feepc"] = 0.0
    if totals["capital"] > 0:
        totals["roi"] = round(totals["profit"] / totals["capital"] * 100, 2)
    expenses = [
        (category_label(category), amount)
        for category, amount in expense_totals().items()
    ]
    expense_total = sum(amount for _, amount in expenses)
    total_net_profit = totals["profit"] - supply_total - expense_total
    if totals["net"] != 0:
        profit_margin = total_net_profit / totals["net"] * 100
    else:
        profit_margin = 0.0
    fee_factor = 1 - totals["feepc"] / 100
    if unit_total > 0:
        supply_per_unit = supply_total / unit_total
    else:
        supply_per_unit = 0.0
    if unit_total > 0 and fee_factor != 0:
        breakeven_per_unit = (
            totals["capital"] / unit_total / fee_factor + supply_per_unit
        )
    else:
        breakeven_per_unit = 0.0
    return {
        "stock_total": stock_total,
        "lots": lots,
        "lot_totals": lot_totals,
        "totals": totals,
        "supply_total": supply_total,
        "expenses": expenses,
        "expense_total": expense_total,
        "total_net_profit": total_net_profit,
        "profit_margin": profit_margin,
        "supply_per_unit": supply_per_unit,
        "breakeven_per_unit": breakeven_per_unit,
        "unit_total": unit_total,
        "box_totals": box_totals,
        "low_supplies": Supply.low_list(),
        "listings": insertions.listing_summary(),
        "mystery_boxes": [
            (listing, mystery.boxes_available(listing))
            for listing in mystery.active_listings()
        ],
    }


class MyIndexView(IndexView):
    index_template = "index.html"

    @expose("/")
    def index(self):
        self.update_redirect()
        return self.render_template(
            self.index_template, appbuilder=self.appbuilder, **home_totals()
        )

    @expose("/calibrateinsertions", methods=["POST"])
    def calibrateinsertions(self):
        """Save the free listings left that the eBay app shows."""
        limit = insertions.free_insertions_limit()
        try:
            remaining = int(request.form.get("remaining", ""))
        except ValueError:
            remaining = -1
        if not 0 <= remaining <= limit:
            flash(
                "Enter the number of free listings left that the eBay app shows,"
                " from 0 to " + str(limit) + ".",
                "danger",
            )
        else:
            change = insertions.calibrate(remaining)
            flash(
                "Saved. fbc's count was "
                + (
                    "the same."
                    if change == 0
                    else str(abs(change)) + (" low." if change > 0 else " high.")
                ),
                "success",
            )
        return redirect(url_for("MyIndexView.index"))

    def _start_sync(self, kind, title, fn, fallback_url):
        """
        Start fn as a background sync job (see fbc.sync). A request from the
        Sync buttons' script gets the job as JSON, to show in a modal; any
        other request gets a page that shows the job's log. In dev mode
        the job only logs that syncing is off.
        """
        if devmode.on():
            fn = devmode.skip_sync
        job_id = sync.start(kind, title, fn, request.referrer or fallback_url)
        job_url = url_for("MyIndexView.syncjob", job_id=job_id)
        best = request.accept_mimetypes.best_match(["text/html", "application/json"])
        if best == "application/json":
            return jsonify(id=job_id, url=job_url)
        self.update_redirect()
        return self.render_template(
            "sync_job.html", appbuilder=self.appbuilder, job_url=job_url
        )

    @expose("/syncjob/<job_id>")
    def syncjob(self, job_id):
        job = sync.read(job_id, request.args.get("offset", 0, type=int))
        if job is None:
            return jsonify(error="No such sync."), 404
        return jsonify(job)

    @expose("/syncdiscogs")
    def syncdiscogs(self):
        return self._start_sync(
            "discogs",
            "Discogs sync",
            self.dosyncdiscogs,
            "/discogsreleasemodelview/list/",
        )

    def dosyncdiscogs(self):
        from fbc import db
        from fbc.models.discogs import DiscogsRelease, DiscogsInstance, Folder

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
        logger.info("Discogs sync finished.")

    @expose("/syncebaylistings")
    def syncebaylistings(self):
        return self._start_sync(
            "ebay-listings",
            "eBay listings sync",
            self.dosyncebaylistings,
            "/ebaylistingmodelview/list/",
        )

    def dosyncebaylistings(self):
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
            if (
                not db.session.query(EbayListing)
                .filter_by(item_id=item["ItemID"])
                .first()
            ):
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

    def doebaysync(self, doupdate, api, response):
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
            order = (
                db.session.query(EbayOrder).filter_by(order_id=item["OrderID"]).first()
            )
            if not order:
                logger.info("Adding " + str(item["OrderID"]))
                add_ebay_order(item)
            elif doupdate == "True" or not order.items or not order.tracking_number:
                # Orders synced before fbc kept every item get them now, and
                # an order still without tracking checks whether it has some.
                logger.info("Updating " + str(item["OrderID"]))
                add_ebay_order(item)

    def dosyncebayorders(self, doupdate, days, message):
        logger.info(message)
        try:
            currentTime = datetime.datetime.now() + datetime.timedelta(days=1)
            startTime = currentTime + datetime.timedelta(days=-days)
            api = trading(compatibility=None)

            response = api.execute(
                "GetOrders",
                {
                    "CreateTimeFrom": str(startTime)[0:19],
                    "CreateTimeTo": str(currentTime)[0:19],
                    "OrderStatus": "Completed",
                },
            )
            self.doebaysync(doupdate, api, response)
        except OAuthError as e:
            logger.error(str(e))
            return
        except ConnectionError as e:
            logging.error(e)
            logging.error(e.response.dict())
            return
        logger.info("eBay orders sync finished.")

    # The order syncs share a kind, so only one of them runs at a time.
    @expose("/syncebayorders")
    def syncebayorders(self):
        doupdate = request.args.get("doupdate")
        if doupdate == "True":
            message = "Updating eBay orders..."
        else:
            message = "Adding new eBay orders..."
        return self._start_sync(
            "ebay-orders",
            "eBay orders sync",
            functools.partial(self.dosyncebayorders, doupdate, 10, message),
            "/ebayordermodelview/list/",
        )

    @expose("/syncebayordersdeep")
    def syncebayordersdeep(self):
        return self._start_sync(
            "ebay-orders",
            "eBay orders deep sync",
            functools.partial(
                self.dosyncebayorders, "False", 88, "Deep updating eBay orders..."
            ),
            "/ebayordermodelview/list/",
        )
