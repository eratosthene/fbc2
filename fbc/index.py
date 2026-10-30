import datetime
import logging
from collections import Counter

import discogs_client
from ebaysdk.exception import ConnectionError
from flask import flash, redirect, request
from flask_appbuilder import IndexView
from flask_appbuilder.views import expose

from fbc.listing import OAuthError, trading
from fbc.util import add_ebay_listing
from fbc.util import add_ebay_order
from fbc.util import add_discogs_release
from fbc.util import find_relisted_listing
from fbc.util import forget_discogs_instance
from fbc.util import relist_ebay_listing

logger = logging.getLogger()


class MyIndexView(IndexView):
    index_template = "index.html"

    @expose("/")
    def index(self):
        self.update_redirect()
        from fbc import db
        from fbc.models.inventory import PurchaseLot, StorageBox, Unit
        from fbc.models.sales import SalesReceipt
        from fbc.models.supplies import PurchaseOrder

        # Load just the columns the totals need, once, and do every count
        # from them in Python, rather than a COUNT query per lot, box and
        # sold unit. Units stay in id order and are still visited one at a
        # time below, so the floating-point sums add up in the same order
        # (and so come out exactly the same) as summing unit by unit did.
        units = (
            db.session.query(
                Unit.purchase_lot_id,
                Unit.storage_box_id,
                Unit.sales_receipt_id,
                Unit.sold,
            )
            .order_by(Unit.id)
            .all()
        )
        receipts = {
            r.id: r
            for r in db.session.query(
                SalesReceipt.id, SalesReceipt.sold_price, SalesReceipt.net_sold
            )
        }
        units_per_lot = Counter(u.purchase_lot_id for u in units)
        units_per_receipt = Counter(u.sales_receipt_id for u in units)
        # sold=False, like filter_by(sold=False), leaves out NULL as well as True.
        unsold = [u for u in units if u.sold is False]
        unsold_per_lot = Counter(u.purchase_lot_id for u in unsold)
        unsold_per_box = Counter(u.storage_box_id for u in unsold)

        stock_total = len(unsold)
        unit_total = len(units)
        lots = db.session.query(PurchaseLot).all()
        boxes = db.session.query(StorageBox).all()
        box_ids = {b.id for b in boxes}
        lot_totals = {}
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
        for lot in lots:
            totals["capital"] += lot.price
            lot_totals[lot.id] = {}
            lot_totals[lot.id]["gross"] = 0
            lot_totals[lot.id]["net"] = 0
            plcount = units_per_lot[lot.id]
            if plcount > 0:
                lot_totals[lot.id]["perunit"] = round(lot.price / plcount, 2)
            else:
                lot_totals[lot.id]["perunit"] = 0
        for po in pos:
            supply_total += po.price
        for u in units:
            # A unit whose box no longer exists counts as having no box.
            box = box_totals[u.storage_box_id if u.storage_box_id in box_ids else 0]
            receipt = receipts.get(u.sales_receipt_id)
            if receipt:
                num_units = units_per_receipt[u.sales_receipt_id]
                c_sold_price = receipt.sold_price / num_units
                c_net_sold = receipt.net_sold / num_units
                box["gross"] += c_sold_price
                box["net"] += c_net_sold
                lot_totals[u.purchase_lot_id]["gross"] += c_sold_price
                totals["gross"] += c_sold_price
                lot_totals[u.purchase_lot_id]["net"] += c_net_sold
                totals["net"] += c_net_sold
            box["capital"] += lot_totals[u.purchase_lot_id]["perunit"]
        for lot in lots:
            lot_totals[lot.id]["fees"] = round(
                lot_totals[lot.id]["gross"] - lot_totals[lot.id]["net"], 2
            )
            totals["fees"] += lot_totals[lot.id]["fees"]
            if lot_totals[lot.id]["gross"] > 0:
                lot_totals[lot.id]["feepc"] = round(
                    lot_totals[lot.id]["fees"] / lot_totals[lot.id]["gross"] * 100, 2
                )
            else:
                lot_totals[lot.id]["feepc"] = 0.0
            lot_totals[lot.id]["profit"] = round(
                lot_totals[lot.id]["net"] - lot.price, 2
            )
            totals["profit"] += lot_totals[lot.id]["profit"]
            if lot.price > 0:
                lot_totals[lot.id]["roi"] = round(
                    lot_totals[lot.id]["profit"] / lot.price * 100, 2
                )
            else:
                lot_totals[lot.id]["roi"] = 0.0
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
        box_totals[0]["profit"] = round(
            box_totals[0]["net"] - box_totals[0]["capital"], 2
        )
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
        total_net_profit = totals["profit"] - supply_total
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
        for lot in lots:
            lot_totals[lot.id]["instock"] = unsold_per_lot[lot.id]
        return self.render_template(
            self.index_template,
            appbuilder=self.appbuilder,
            stock_total=stock_total,
            lots=lots,
            lot_totals=lot_totals,
            totals=totals,
            supply_total=supply_total,
            total_net_profit=total_net_profit,
            profit_margin=profit_margin,
            supply_per_unit=supply_per_unit,
            breakeven_per_unit=breakeven_per_unit,
            unit_total=unit_total,
            box_totals=box_totals,
        )

    @expose("/syncdiscogs")
    def syncdiscogs(self):
        self.update_redirect()
        from fbc import app, db
        from fbc.models.discogs import DiscogsRelease, DiscogsInstance, Folder

        logger.info("Updating discogs...")
        d = discogs_client.Client(
            app.config["DISCOGS_SETTINGS"]["USER_AGENT"],
            user_token=app.config["DISCOGS_SETTINGS"]["USER_TOKEN"],
        )
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
            logger.debug("Checking discogs: " + str(item))
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
                logger.debug(
                    "Local instance " + str(item.instance_id) + " should be removed"
                )
                forget_discogs_instance(item)

        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/discogsreleasemodelview/list/")

    @expose("/syncebaylistings")
    def syncebaylistings(self):
        self.update_redirect()
        from fbc import db
        from fbc.models.ebay import EbayListing

        totalpages = 1
        currentpage = 1
        all_items = []
        while currentpage <= totalpages:
            logger.info("Getting page " + str(currentpage) + "...")
            try:
                api = trading(compatibility=None)

                response = api.execute(
                    "GetMyeBaySelling",
                    {
                        "ActiveList": {
                            "Include": True,
                            "Sort": "StartTime",
                            "Pagination": {
                                "EntriesPerPage": 200,
                                "PageNumber": currentpage,
                            },
                        }
                    },
                )
                logging.info("eBay response: " + api.response_status())
                resp = response.dict()
                totalpages = int(
                    resp["ActiveList"]["PaginationResult"]["TotalNumberOfPages"]
                )
                logging.info("Total pages: " + str(totalpages))
                all_items.extend(resp["ActiveList"]["ItemArray"]["Item"])
            except OAuthError as e:
                flash(str(e), "danger")
                break
            except ConnectionError as e:
                logging.error(e)
                logging.error(e.response.dict())
            currentpage += 1

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
            logger.debug("Checking ebay: " + str(item["ItemID"]))
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
        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/ebaylistingmodelview/list/")

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
            logger.debug("Checking ebay: " + str(item["OrderID"]))
            if (
                not db.session.query(EbayOrder)
                .filter_by(order_id=item["OrderID"])
                .first()
            ):
                logger.info("Adding " + str(item["OrderID"]))
                add_ebay_order(item)
            elif doupdate == "True":
                logger.info("Updating " + str(item["OrderID"]))
                add_ebay_order(item)

    @expose("/syncebayorders")
    def syncebayorders(self):
        doupdate = request.args.get("doupdate")
        self.update_redirect()
        if doupdate == "True":
            logger.info("Updating eBay orders...")
        else:
            logger.info("Adding new eBay orders...")
        try:
            currentTime = datetime.datetime.now() + datetime.timedelta(days=1)
            startTime = currentTime + datetime.timedelta(days=-10)
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
            flash(str(e), "danger")
        except ConnectionError as e:
            logging.error(e)
            logging.error(e.response.dict())
        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/ebayordermodelview/list/")

    @expose("/syncebayordersdeep")
    def syncebayordersdeep(self):
        self.update_redirect()
        logger.info("Deep updating eBay orders...")
        try:
            currentTime = datetime.datetime.now() + datetime.timedelta(days=1)
            startTime = currentTime + datetime.timedelta(days=-88)
            api = trading(compatibility=None)

            response = api.execute(
                "GetOrders",
                {
                    "CreateTimeFrom": str(startTime)[0:19],
                    "CreateTimeTo": str(currentTime)[0:19],
                    "OrderStatus": "Completed",
                },
            )
            self.doebaysync("False", api, response)
        except OAuthError as e:
            flash(str(e), "danger")
        except ConnectionError as e:
            logging.error(e)
            logging.error(e.response.dict())
        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/ebayordermodelview/list/")
