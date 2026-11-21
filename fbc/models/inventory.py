from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from flask_appbuilder.models.decorators import renders
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    ForeignKey,
    Float,
    Boolean,
    Date,
    false,
)
from sqlalchemy.orm import relationship
from fbc.core.formatting import fprice


class PurchaseLot(Model):
    __tablename__ = "purchase_lot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    date = Column(Date, nullable=False)
    price = Column(Float, nullable=False)
    notes = Column(String(100))
    # The Discogs Folder this lot's records are kept in, if it has one. Many
    # lots may share a folder; Identify Record pre-selects it, nothing more.
    folder_id = Column(Integer, ForeignKey("folder.folder_id"), nullable=True)
    folder = relationship("Folder")

    @renders("price")
    def purchase_price(self):
        return fprice(self.price)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class StorageBox(Model):
    __tablename__ = "storage_box"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Unit(Model):
    __tablename__ = "unit"

    from fbc.models.ebay import EbayListing
    from fbc.models.sales import SalesReceipt

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    unit_type = Column(String(30))
    description = Column(String(300))
    discogs_release_id = Column(
        Integer, ForeignKey("discogs_release.release_id"), index=True
    )
    discogs_release = relationship("DiscogsRelease")
    discogs_instance_id = Column(
        BigInteger,
        ForeignKey("discogs_instance.instance_id", ondelete="CASCADE"),
        index=True,
    )
    discogs_instance = relationship("DiscogsInstance")
    discogs_master = Column(
        String(21), nullable=False, default="", server_default="", index=True
    )
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"), index=True)
    ebay_listing = relationship("EbayListing")
    purchase_lot_id = Column(Integer, ForeignKey("purchase_lot.id"), index=True)
    purchase_lot = relationship("PurchaseLot")
    storage_box_id = Column(Integer, ForeignKey("storage_box.id"), index=True)
    storage_box = relationship("StorageBox")
    # The old combined grade ("VG+/VG+"). Nothing writes or reads it any more:
    # the grades below replace it, and a later change drops the column.
    grading = Column(String(20))
    record_grade = Column(String(20))
    record_note = Column(String(300))
    sleeve_grade = Column(String(20))
    sleeve_note = Column(String(300))
    # The paper or plastic sleeve that holds the disc inside the record sleeve.
    # eBay calls its grade "Inlay Condition".
    dust_jacket_grade = Column(String(20))
    dust_jacket_note = Column(String(300))
    extras = Column(String(300))
    pressing = Column(String(100))
    matrix = Column(String(100))
    notes = Column(String(100))
    retail_price = Column(Float, index=True)
    sold = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    sales_receipt_id = Column(Integer, ForeignKey("sales_receipt.id"), index=True)
    sales_receipt = relationship("SalesReceipt")
    # What this Unit sold for, when its Sales Receipt covers several Units:
    # its order item's price, or its share of a receipt combined from
    # several. A receipt's prices are split between its Units by these
    # (see unit_weights).
    sold_price = Column(Float)
    # Hidden from the lists by default; see fbc/sales/archive.py.
    archived = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    def grading_text(self):
        """The record and sleeve grades as one short text, "VG+/VG"."""
        return "/".join(g for g in (self.record_grade, self.sleeve_grade) if g)

    def __unicode__(self):
        return " ".join((self.name, self.unit_type, self.pressing, self.grading_text()))

    def __repr__(self):
        return self.__unicode__()

    def fmt_retail_price(self):
        if self.retail_price:
            return "$" + f"{self.retail_price:.2f}"
        else:
            return None

    def sold_for(self):
        """
        What this unit actually sold for: the Sales Receipt's sold price,
        falling back to the eBay Order price (either the receipt's order or
        an order placed against this unit's listing). None if unknown.
        """
        from fbc import db
        from fbc.models.ebay import EbayOrder

        receipt = self.sales_receipt
        if receipt:
            if receipt.sold_price is not None:
                return receipt.unit_share(self, receipt.sold_price)
            if receipt.ebay_orders:
                return receipt.unit_share(
                    self, sum(o.price or 0 for o in receipt.ebay_orders)
                )
            if receipt.discogs_orders:
                return receipt.unit_share(
                    self,
                    sum(i.price or 0 for o in receipt.discogs_orders for i in o.items),
                )
        if self.sold and self.ebay_listing_id:
            order = (
                db.session.query(EbayOrder)
                .filter_by(ebay_listing_id=self.ebay_listing_id)
                .first()
            )
            if order:
                return order.price
        return None

    @staticmethod
    def average_sold_price(discogs_master):
        """
        Average sold price of every sold unit sharing `discogs_master` (the
        same grouping the Filter:Master link uses), or None if none of them
        have a known sold price.
        """
        from fbc import db

        units = (
            db.session.query(Unit)
            .filter_by(discogs_master=discogs_master)
            .filter((Unit.sold.is_(True)) | (Unit.sales_receipt_id.isnot(None)))
            .all()
        )
        prices = [p for p in (u.sold_for() for u in units) if p is not None]
        if not prices:
            return None
        return round(sum(prices) / len(prices), 2)

    def master_show(self, markup=True):
        s = (
            '<a href="'
            + url_for(
                "UnitModelView.list", _flt_3_discogs_master=str(self.discogs_master)
            )
            + '">Filter:Master</a><br>'
        )
        if self.discogs_master.startswith("r"):
            s = s + (
                '<a href="https://www.discogs.com/release/'
                + str(self.discogs_master[1:])
                + '">D:Release(NM)</a>'
            )
        else:
            s = s + (
                '<a href="https://www.discogs.com/master/'
                + str(self.discogs_master)
                + '">D:Master</a>'
            )
        if markup:
            return Markup(s)
        else:
            return s

    def link_column(self):
        ret = ""
        if self.discogs_release:
            ret = ret + self.discogs_release.release_show(False) + "<br/>"
        if self.discogs_master:
            ret = ret + self.master_show(False) + "<br/>"
        if self.ebay_listing:
            ret = (
                ret
                + '<a href="'
                + self.ebay_listing.url
                + '">eBay:Listing&nbsp;$'
                + f"{self.ebay_listing.price:.2f}"
                "</a><br/>"
            )
        elif self.id and not self.sold:
            ret = (
                ret
                + '<a href="'
                + url_for("EbayDraftModelView.from_unit", pk=self.id)
                + '">List&nbsp;on&nbsp;eBay</a><br/>'
            )
        ret = ret + self._discogs_listing_link()
        if self.id:
            ret = (
                ret
                + '<a href="'
                + url_for("UnitPicturesView.show", pk=self.id)
                + '">Pictures</a><br/>'
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
            if self.sales_receipt.ebay_orders:
                ret = ret + self.sales_receipt.fmt_ebay_order(False) + "<br/>"
            if self.sales_receipt.discogs_orders:
                ret = ret + self.sales_receipt.fmt_discogs_order(False) + "<br/>"
        return Markup(ret)

    def _discogs_listing_link(self):
        """
        A link to the Unit's open Discogs listing, or to List on Discogs
        when it can be listed (see fbc/discogs/listing.py problem_with).
        """
        # Inside the method: models.discogs imports this module.
        from fbc.models.discogs import DiscogsListing

        if not self.id or self.sold:
            return ""
        listing = DiscogsListing.open_for(self)
        if listing:
            return (
                '<a href="'
                + url_for("DiscogsListingModelView.show", pk=listing.listing_id)
                + '">Discogs:'
                + str(listing.status).replace(" ", "&nbsp;")
                + "</a><br/>"
            )
        if self.discogs_instance_id and self.discogs_release_id:
            return (
                '<a href="'
                + url_for("DiscogsListingModelView.from_unit", pk=self.id)
                + '">List&nbsp;on&nbsp;Discogs</a><br/>'
            )
        return ""

    def discogs_genre_column(self):
        ret = ""
        if self.discogs_release:
            ret = ", ".join(str(g) for g in self.discogs_release.genres)
        return Markup(ret)

    def discogs_style_column(self):
        ret = ""
        if self.discogs_release:
            ret = ", ".join(str(s) for s in self.discogs_release.styles)
        return Markup(ret)
