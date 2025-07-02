import datetime
import logging

import discogs_client
from ebaysdk.exception import ConnectionError
from ebaysdk.trading import Connection as Trading
from flask import redirect, request
from flask_appbuilder import IndexView
from flask_appbuilder.views import expose

from fbc.util import add_ebay_listing
from fbc.util import add_ebay_order
from fbc.util import add_discogs_release

logger = logging.getLogger()


class MyIndexView(IndexView):
    index_template = "index.html"

    @expose("/")
    def index(self):
        self.update_redirect()
        from fbc import db
        from fbc.models.inventory import PurchaseLot, StorageBox, Unit
        from fbc.models.supplies import PurchaseOrder

        stock_total = db.session.query(Unit).filter_by(sold=False).count()
        unit_total = db.session.query(Unit).count()
        lots = db.session.query(PurchaseLot).all()
        boxes = db.session.query(StorageBox).all()
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
            box_totals[b.id]["instock"] = (
                db.session.query(Unit).filter_by(storage_box=b, sold=False).count()
            )
            box_totals[b.id]["capital"] = 0.0
            box_totals[b.id]["gross"] = 0.0
            box_totals[b.id]["net"] = 0.0
        box_totals[0] = {}
        box_totals[0]["name"] = "No Box Assigned"
        box_totals[0]["instock"] = (
            db.session.query(Unit).filter_by(storage_box=None, sold=False).count()
        )
        box_totals[0]["capital"] = 0.0
        box_totals[0]["gross"] = 0.0
        box_totals[0]["net"] = 0.0
        for lot in lots:
            totals["capital"] += lot.price
            lot_totals[lot.id] = {}
            lot_totals[lot.id]["gross"] = 0
            lot_totals[lot.id]["net"] = 0
            plcount = db.session.query(Unit).filter_by(purchase_lot=lot).count()
            if plcount > 0:
                lot_totals[lot.id]["perunit"] = round(lot.price / plcount, 2)
            else:
                lot_totals[lot.id]["perunit"] = 0
        for po in pos:
            supply_total += po.price
        for u in db.session.query(Unit).all():
            if u.sales_receipt:
                if u.storage_box:
                    box_totals[u.storage_box.id]["gross"] += u.sales_receipt.sold_price
                    box_totals[u.storage_box.id]["net"] += u.sales_receipt.net_sold
                else:
                    box_totals[0]["gross"] += u.sales_receipt.sold_price
                    box_totals[0]["net"] += u.sales_receipt.net_sold
                lot_totals[u.purchase_lot.id]["gross"] += u.sales_receipt.sold_price
                totals["gross"] += u.sales_receipt.sold_price
                lot_totals[u.purchase_lot.id]["net"] += u.sales_receipt.net_sold
                totals["net"] += u.sales_receipt.net_sold
            if u.storage_box:
                box_totals[u.storage_box.id]["capital"] += lot_totals[
                    u.purchase_lot.id
                ]["perunit"]
            else:
                box_totals[0]["capital"] += lot_totals[u.purchase_lot.id]["perunit"]
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
        for lot in lots:
            lot_totals[lot.id]["instock"] = (
                db.session.query(Unit).filter_by(purchase_lot=lot, sold=False).count()
            )
        return self.render_template(
            self.index_template,
            appbuilder=self.appbuilder,
            stock_total=stock_total,
            lots=lots,
            lot_totals=lot_totals,
            totals=totals,
            supply_total=supply_total,
            total_net_profit=total_net_profit,
            unit_total=unit_total,
            box_totals=box_totals,
        )

    @expose("/syncdiscogs")
    def syncdiscogs(self):
        self.update_redirect()
        from fbc import app, db
        from fbc.models.discogs import DiscogsRelease, DiscogsInstance, Folder
        from fbc.models.inventory import Unit

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
                u = (
                    db.session.query(Unit)
                    .filter_by(discogs_instance_id=item.instance_id)
                    .first()
                )
                if u:
                    logger.info("Updating Unit " + str(u))
                    u.discogs_instance_id = None
                dr = (
                    db.session.query(DiscogsRelease)
                    .filter(DiscogsRelease.discogs_instances.contains(item))
                    .first()
                )
                if dr:
                    logger.info("Updating DiscogsRelease " + str(dr))
                    for di in dr.discogs_instances:
                        if di.instance_id == item.instance_id:
                            logger.debug("Removing instance from release")
                            dr.discogs_instances.remove(di)
                    if len(dr.discogs_instances) == 0:
                        logger.info(
                            "All instances are gone, removing DiscogsRelease " + str(dr)
                        )
                        u = (
                            db.session.query(Unit)
                            .filter_by(discogs_release_id=dr.release_id)
                            .first()
                        )
                        if u:
                            logger.info("Updating Unit " + str(u))
                            u.discogs_release_id = None
                        db.session.delete(dr)
                logger.debug("Removing instance")
                db.session.delete(item)
                db.session.commit()

        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/discogsreleasemodelview/list/")

    @expose("/syncebaylistings")
    def syncebaylistings(self):
        self.update_redirect()
        from fbc import app, db
        from fbc.models.ebay import EbayListing

        ebayconfig = app.config["EBAY_SETTINGS"]
        totalpages = 1
        currentpage = 1
        while currentpage <= totalpages:
            logger.info("Getting page " + str(currentpage) + "...")
            try:
                api = Trading(
                    debug=False,
                    config_file=None,
                    appid=ebayconfig["APP_ID"],
                    domain="api.ebay.com",
                    certid=ebayconfig["CERT_ID"],
                    devid=ebayconfig["DEV_ID"],
                    token=ebayconfig["USER_TOKEN"],
                )

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
                listings = resp["ActiveList"]["ItemArray"]["Item"]
                local_listings = db.session.query(EbayListing).all()
                logger.info("Total listings: " + str(len(listings)))
                logger.info("Total local listings: " + str(len(local_listings)))
                for item in listings:
                    logger.debug("Checking ebay: " + str(item["ItemID"]))
                    if (
                        not db.session.query(EbayListing)
                        .filter_by(item_id=item["ItemID"])
                        .first()
                    ):
                        logger.info("Adding " + str(item["ItemID"]))
                        add_ebay_listing(item)
            except ConnectionError as e:
                logging.error(e)
                logging.error(e.response.dict())
            currentpage += 1
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
        from fbc import app

        doupdate = request.args.get("doupdate")
        self.update_redirect()
        if doupdate == "True":
            logger.info("Updating eBay orders...")
        else:
            logger.info("Adding new eBay orders...")
        ebayconfig = app.config["EBAY_SETTINGS"]
        try:
            currentTime = datetime.datetime.now() + datetime.timedelta(days=1)
            startTime = currentTime + datetime.timedelta(days=-10)
            api = Trading(
                debug=False,
                config_file=None,
                appid=ebayconfig["APP_ID"],
                domain="api.ebay.com",
                certid=ebayconfig["CERT_ID"],
                devid=ebayconfig["DEV_ID"],
                token=ebayconfig["USER_TOKEN"],
            )

            response = api.execute(
                "GetOrders",
                {
                    "CreateTimeFrom": str(startTime)[0:19],
                    "CreateTimeTo": str(currentTime)[0:19],
                    "OrderStatus": "Completed",
                },
            )
            self.doebaysync(doupdate, api, response)
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
        from fbc import app

        logger.info("Deep updating eBay orders...")
        ebayconfig = app.config["EBAY_SETTINGS"]
        try:
            currentTime = datetime.datetime.now() + datetime.timedelta(days=1)
            startTime = currentTime + datetime.timedelta(days=-88)
            api = Trading(
                debug=False,
                config_file=None,
                appid=ebayconfig["APP_ID"],
                domain="api.ebay.com",
                certid=ebayconfig["CERT_ID"],
                devid=ebayconfig["DEV_ID"],
                token=ebayconfig["USER_TOKEN"],
            )

            response = api.execute(
                "GetOrders",
                {
                    "CreateTimeFrom": str(startTime)[0:19],
                    "CreateTimeTo": str(currentTime)[0:19],
                    "OrderStatus": "Completed",
                },
            )
            self.doebaysync("False", api, response)
        except ConnectionError as e:
            logging.error(e)
            logging.error(e.response.dict())
        ref = request.referrer
        if ref:
            return redirect(ref)
        else:
            return redirect("/ebayordermodelview/list/")
