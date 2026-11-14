import logging

from flask import flash, jsonify, redirect, request, url_for
from flask_appbuilder import IndexView
from flask_appbuilder.views import expose

from fbc import insertions, sync, syncjobs, tasks

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
    stock_total = sum(1 for u in units if u.sold is False)
    unit_total = len(units)
    lots = db.session.query(PurchaseLot).all()
    boxes = db.session.query(StorageBox).all()
    box_ids = {b.id for b in boxes}
    box_totals = {}
    pos = db.session.query(PurchaseOrder).all()
    supply_total = 0
    totals = {
        "units": len(units),
        "sold_units": 0,
        "instock": 0,
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
        box_totals[b.id]["units"] = 0
        box_totals[b.id]["sold_units"] = 0
        box_totals[b.id]["instock"] = 0
        box_totals[b.id]["capital"] = 0.0
        box_totals[b.id]["gross"] = 0.0
        box_totals[b.id]["net"] = 0.0
    box_totals[0] = {}
    box_totals[0]["name"] = "No Box Assigned"
    box_totals[0]["units"] = 0
    box_totals[0]["sold_units"] = 0
    box_totals[0]["instock"] = 0
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
        box["units"] += 1
        if u.sold is True:
            box["sold_units"] += 1
            totals["sold_units"] += 1
        if u.sold is False:
            box["instock"] += 1
            totals["instock"] += 1
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
        "tasks": tasks.counts(),
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

    def _start_sync(self, job, fallback_url):
        """
        Start a sync (a fbc.syncjobs Job) as a background job (see fbc.sync).
        A request from the Sync buttons' script gets the job as JSON, to show
        in a modal; any other request gets a page that shows the job's log.
        In dev mode the job only logs that syncing is off.
        """
        job_id = sync.start(
            job.kind, job.title, job.fn, request.referrer or fallback_url
        )
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
            syncjobs.discogs_job(), "/discogsreleasemodelview/list/"
        )

    @expose("/syncdiscogslistings")
    def syncdiscogslistings(self):
        return self._start_sync(
            syncjobs.discogs_listings_job(), "/discogslistingmodelview/list/"
        )

    @expose("/syncebaylistings")
    def syncebaylistings(self):
        return self._start_sync(
            syncjobs.ebay_listings_job(), "/ebaylistingmodelview/list/"
        )

    @expose("/syncebayorders")
    def syncebayorders(self):
        update = request.args.get("doupdate") == "True"
        return self._start_sync(
            syncjobs.ebay_orders_job(update=update), "/ebayordermodelview/list/"
        )

    @expose("/syncebayordersdeep")
    def syncebayordersdeep(self):
        return self._start_sync(
            syncjobs.ebay_orders_job(deep=True), "/ebayordermodelview/list/"
        )
