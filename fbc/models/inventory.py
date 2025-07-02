from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from flask_appbuilder.models.decorators import renders
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean, Date
from sqlalchemy.orm import relationship
from fbc.util import fpercent, fprice


class PurchaseLot(Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    name = Column(String(100), nullable=False)
    date = Column(Date, nullable=False)
    price = Column(Float, nullable=False)
    notes = Column(String(100))

    @renders("price")
    def purchase_price(self):
        return fprice(self.price)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name

    def compute_profit(self):
        from fbc import db

        ret = {}
        ret["gross"] = 0.0
        ret["net"] = 0.0
        for unit in db.session.query(Unit).filter_by(purchase_lot=self).all():
            if unit.sales_receipt:
                ret["gross"] += unit.sales_receipt.sold_price
                ret["net"] += unit.sales_receipt.net_sold
        ret["fees"] = round(ret["gross"] - ret["net"], 2)
        if ret["gross"] > 0:
            ret["feepc"] = round(ret["fees"] / ret["gross"], 2)
        else:
            ret["feepc"] = 0.0
        ret["profit"] = round(ret["net"] - self.price, 2)
        ret["roi"] = round(ret["profit"] / self.price, 2)
        return ret

    def compute_units(self):
        from fbc import db

        ret = {}
        ret["total"] = db.session.query(Unit).filter_by(purchase_lot=self).count()
        ret["sold"] = (
            db.session.query(Unit).filter_by(purchase_lot=self, sold=True).count()
        )
        ret["left"] = ret["total"] - ret["sold"]
        return ret

    def compute_forecast(self, t, u):
        from fbc import db

        ret = {}
        if u["total"] > 0:
            ret["breakeven"] = self.price / u["total"] / (1 - t["feepc"])
        else:
            ret["breakeven"] = 0
        total_price = 0.0
        total_sold_price = 0.0
        total_num_sold = 0
        for unit in db.session.query(Unit).filter_by(purchase_lot=self).all():
            if unit.retail_price:
                total_price += unit.retail_price
            if unit.sold and unit.sales_receipt:
                total_num_sold += 1
                total_sold_price += unit.sales_receipt.sold_price
        ret["pprofit"] = total_price - (total_price * t["feepc"]) - self.price
        if total_num_sold > 0:
            ret["avgsoldprice"] = total_sold_price / total_num_sold
        else:
            ret["avgsoldprice"] = 0
        ret["avgppu"] = ret["avgsoldprice"] - ret["breakeven"]
        ret["lprofit"] = u["total"] * ret["avgppu"]
        return ret

    def breakdown(self):
        t = self.compute_profit()
        u = self.compute_units()
        c = self.compute_forecast(t, u)
        ret = '<table class="table table-bordered table-condensed table-hover" style="width: auto">'
        ret = (
            ret
            + "<tr><th>Capital</th><th>Sold For</th><th>Fees</th><th>Fee %</th><th>Net Sold</th><th>Profit</th><th>ROI</th></tr>"
        )
        ret = ret + "<tr>"
        ret = ret + "<td>" + fprice(self.price) + "</td>"
        ret = ret + "<td>" + fprice(t["gross"]) + "</td>"
        ret = ret + "<td>" + fprice(t["fees"]) + "</td>"
        ret = ret + "<td>" + fpercent(t["feepc"]) + "</td>"
        ret = ret + "<td>" + fprice(t["net"]) + "</td>"
        ret = ret + "<td>" + fprice(t["profit"]) + "</td>"
        ret = ret + "<td>" + fpercent(t["roi"]) + "</td>"
        ret = ret + "</tr></table>"
        ret = (
            ret
            + '<table class="table table-bordered table-condensed table-hover" style="width: auto">'
        )
        ret = (
            ret + "<tr><th>Total Units</th><th>Sold Units</th><th>Units Left</th></tr>"
        )
        ret = ret + "<tr>"
        ret = ret + "<td>" + str(u["total"]) + "</td>"
        ret = ret + "<td>" + str(u["sold"]) + "</td>"
        ret = ret + "<td>" + str(u["left"]) + "</td>"
        ret = ret + "</tr></table>"
        ret = (
            ret
            + '<table class="table table-bordered table-condensed table-hover" style="width: auto">'
        )
        ret = (
            ret
            + "<tr><th>Breakeven Price</th><th>Potential Profit</th><th>Avg. Sold Price</th><th>Avg. Profit/Unit</th><th>Likely Profit</th></tr>"
        )
        ret = ret + "<tr>"
        ret = ret + "<td>" + fprice(c["breakeven"]) + "</td>"
        ret = ret + "<td>" + fprice(c["pprofit"]) + "</td>"
        ret = ret + "<td>" + fprice(c["avgsoldprice"]) + "</td>"
        ret = ret + "<td>" + fprice(c["avgppu"]) + "</td>"
        ret = ret + "<td>" + fprice(c["lprofit"]) + "</td>"
        ret = ret + "</tr></table>"
        return Markup(ret)

    def list_sold(self):
        u = self.compute_units()
        return u["sold"]

    def list_total(self):
        u = self.compute_units()
        return u["total"]

    def list_profit(self):
        t = self.compute_profit()
        return fprice(t["profit"])

    def list_breakeven(self):
        t = self.compute_profit()
        u = self.compute_units()
        c = self.compute_forecast(t, u)
        return fprice(c["breakeven"])


class StorageBox(Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    name = Column(String(100), nullable=False)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Unit(Model):
    from fbc.models.ebay import EbayListing
    from fbc.models.sales import SalesReceipt
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    name = Column(String(200), nullable=False, index=True)
    unit_type = Column(String(30))
    description = Column(String(300))
    discogs_release_id = Column(Integer, ForeignKey("discogs_release.release_id"))
    discogs_release = relationship("DiscogsRelease")
    discogs_instance_id = Column(Integer, ForeignKey("discogs_instance.instance_id"))
    discogs_instance = relationship("DiscogsInstance")
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"))
    ebay_listing = relationship("EbayListing")
    purchase_lot_id = Column(Integer, ForeignKey("purchase_lot.id"))
    purchase_lot = relationship("PurchaseLot")
    storage_box_id = Column(Integer, ForeignKey("storage_box.id"))
    storage_box = relationship("StorageBox")
    grading = Column(String(20))
    pressing = Column(String(100))
    matrix = Column(String(100))
    notes = Column(String(100))
    retail_price = Column(Float, index=True)
    sold = Column(Boolean, index=True)
    sales_receipt_id = Column(Integer, ForeignKey("sales_receipt.id"))
    sales_receipt = relationship("SalesReceipt")

    def __unicode__(self):
        return (
            self.name + " " + self.unit_type + " " + self.pressing + " " + self.grading
        )

    def __repr__(self):
        return (
            self.name + " " + self.unit_type + " " + self.pressing + " " + self.grading
        )

    def fmt_retail_price(self):
        if self.retail_price:
            return "$" + f"{self.retail_price:.2f}"
        else:
            return None

    def link_column(self):
        ret = ""
        if self.discogs_release:
            ret = (
                ret
                + self.discogs_release.release_show(False)
                + "<br/>"
                + self.discogs_release.master_show(False)
                + "<br/>"
            )
        if self.ebay_listing:
            ret = (
                ret
                + '<a href="'
                + self.ebay_listing.url
                + '">eBay:Listing&nbsp;$'
                + f"{self.ebay_listing.price:.2f}"
                "</a><br/>"
            )
        if self.sales_receipt:
            ret = (
                ret
                + '<a href="'
                + url_for("SalesReceiptModelView.show", pk=str(self.sales_receipt_id))
                + '">SalesReceipt&nbsp$'
                + f"{self.sales_receipt.net_sold:.2f}"
                + "</a><br/>"
            )
            if self.sales_receipt.ebay_order:
                ret = ret + self.sales_receipt.fmt_ebay_order(False)
        return Markup(ret)
