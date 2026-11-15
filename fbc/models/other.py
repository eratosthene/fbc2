"""
Other Items: things sold on eBay that aren't vinyl records (see
fbc/ebay/sorting.py). They have no Units and no Discogs side, and no cost
unless one is entered, so they are kept out of every record figure: stock,
cost and profit. Their sales are shown on their own.
"""

from flask import url_for
from flask_appbuilder import Model
from markupsafe import Markup
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    false,
    select,
)
from sqlalchemy.orm import relationship


class OtherItem(Model):
    __tablename__ = "other_item"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # What it cost, when that is known. Most of these have none, and then they
    # have no profit either.
    cost = Column(Float)
    # One Other Item for each eBay listing; unique so a sync run twice can't
    # make it twice. Blank for one added by hand.
    ebay_listing_id = Column(
        String(20), ForeignKey("ebay_listing.item_id"), unique=True
    )
    ebay_listing = relationship("EbayListing")
    sales_receipt_id = Column(
        Integer, ForeignKey("sales_receipt.id", ondelete="SET NULL"), index=True
    )
    sales_receipt = relationship("SalesReceipt")
    sold = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name

    def fmt_cost(self):
        return "" if self.cost is None else "$" + f"{self.cost:.2f}"

    def fmt_listing(self):
        if not self.ebay_listing:
            return ""
        return Markup('<a href="{}">Listing</a>').format(self.ebay_listing.url)

    def order(self):
        """The eBay order that sold it which has no Sales Receipt yet, or None."""
        from fbc import db
        from fbc.models.ebay import EbayOrder, EbayOrderItem

        if not self.ebay_listing_id:
            return None
        return (
            db.session.query(EbayOrder)
            .filter(
                (EbayOrder.ebay_listing_id == self.ebay_listing_id)
                | EbayOrder.items.any(
                    EbayOrderItem.ebay_listing_id == self.ebay_listing_id
                )
            )
            .filter(~EbayOrder.sales_receipts.any())
            .order_by(EbayOrder.created_time)
            .first()
        )

    def links(self):
        """Its Sales Receipt once it's sold, or Ship This for its order."""
        if self.sales_receipt_id:
            return Markup('<a href="{}">Sales&nbsp;Receipt</a>').format(
                url_for("SalesReceiptModelView.show", pk=str(self.sales_receipt_id))
            )
        order = self.order()
        if order:
            return Markup('<a href="{}">Ship&nbsp;This</a>').format(
                url_for("EbayOrderModelView.ship", pk=order.order_id)
            )
        return ""


def listing_ids():
    """A SELECT of the eBay listings that are Other Items, for filters."""
    return select(OtherItem.ebay_listing_id).where(
        OtherItem.ebay_listing_id.isnot(None)
    )


def receipt_ids():
    """A SELECT of the Sales Receipts that are Other Items' sales."""
    return select(OtherItem.sales_receipt_id).where(
        OtherItem.sales_receipt_id.isnot(None)
    )
