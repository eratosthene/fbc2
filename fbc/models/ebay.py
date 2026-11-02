import json
import logging

from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

logger = logging.getLogger()


class EbayListing(Model):
    __tablename__ = "ebay_listing"
    item_id = Column(String(20), primary_key=True, unique=True)
    title = Column(String(100))
    price = Column(Float)
    url = Column(String(255))
    start_time = Column(DateTime)

    def __unicode__(self):
        return self.item_id + ": " + self.title + " $" + f"{self.price:.2f}"

    def __repr__(self):
        return self.item_id + ": " + self.title + " $" + f"{self.price:.2f}"

    def fmt_price(self):
        return "$" + f"{self.price:.2f}"

    def fmt_url(self):
        return Markup('<a href="' + self.url + '">Listing</a>')


class EbayOrder(Model):
    __tablename__ = "ebay_order"
    order_id = Column(String(20), primary_key=True, unique=True)
    created_time = Column(DateTime)
    buyer = Column(String(50))
    # The items' subtotal, without shipping.
    price = Column(Float)
    # What the buyer paid for shipping. None for orders synced before fbc
    # kept it.
    shipping = Column(Float)
    # The first item's title and listing; every item is in items.
    title = Column(String(100))
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"))
    ebay_listing = relationship("EbayListing")
    items = relationship(
        "EbayOrderItem",
        back_populates="ebay_order",
        cascade="all, delete-orphan",
        order_by="EbayOrderItem.id",
    )
    sales_receipts = relationship(
        "SalesReceipt",
        secondary="sales_receipt_ebay_order",
        back_populates="ebay_orders",
    )

    def __unicode__(self):
        return self.__repr__()

    def __repr__(self):
        ret = str(self.created_time.date()) + " " + self.order_id
        if self.title:
            ret = ret + " [" + self.title[:45] + "]"
        return ret + ": " + self.buyer + " $" + f"{self.price:.2f}"

    def fmt_price(self):
        return "$" + f"{self.price:.2f}"

    def fmt_url(self):
        return Markup(
            '<a href="https://www.ebay.com/mesh/ord/details?orderid='
            + self.order_id
            + '">'
            + self.order_id
            + "</a>"
        )

    def listing_ids(self):
        """
        {listing id: quantity} for every item on the order. An order synced
        before fbc kept every item has only its first item's listing.
        """
        if self.items:
            ids = {}
            for item in self.items:
                if item.ebay_listing_id:
                    ids[item.ebay_listing_id] = ids.get(item.ebay_listing_id, 0) + (
                        item.quantity or 1
                    )
            return ids
        if self.ebay_listing_id:
            return {self.ebay_listing_id: 1}
        return {}

    def links(self):
        retval = (
            '<a href="'
            + url_for(
                "SalesReceiptModelView.add",
                sold_price=self.price,
                ebay_orders=self.order_id,
                date=self.created_time.date(),
            )
            + '">Add&nbsp;SR</a>'
        )
        from fbc.shipping import can_ship, combinable_orders

        if can_ship(self):
            others = combinable_orders(self)
            if others:
                retval = (
                    retval
                    + '<br><a href="'
                    + url_for(
                        "EbayOrderModelView.ship",
                        pk=self.order_id,
                        combine=[o.order_id for o in others],
                    )
                    + '">Ship&nbsp;This&nbsp;Combined</a> ('
                    + str(len(others) + 1)
                    + "&nbsp;orders)"
                    + '<br><a href="'
                    + url_for("EbayOrderModelView.ship", pk=self.order_id)
                    + '">Ship&nbsp;This&nbsp;Alone</a>'
                )
            else:
                retval = (
                    retval
                    + '<br><a href="'
                    + url_for("EbayOrderModelView.ship", pk=self.order_id)
                    + '">Ship&nbsp;This</a>'
                )
        if self.ebay_listing:
            from fbc.models.inventory import Unit
            from fbc import db

            o = (
                db.session.query(Unit)
                .filter_by(ebay_listing_id=self.ebay_listing_id)
                .first()
            )
            if o:
                retval = (
                    retval
                    + '<br><a href="'
                    + url_for("UnitModelView.edit", pk=o.id)
                    + '">Edit&nbsp;Unit</a>'
                )
                if o.discogs_release:
                    retval = retval + "<br>" + o.discogs_release.release_show(False)

        return Markup(retval)


class EbayOrderItem(Model):
    """
    One item on an eBay order: a listing, and how many of it were bought at
    what price each.
    """

    __tablename__ = "ebay_order_item"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ebay_order_id = Column(
        String(20),
        ForeignKey("ebay_order.order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ebay_order = relationship("EbayOrder", back_populates="items")
    # Not a foreign key: the order sync keeps an item even when its listing
    # isn't in fbc.
    ebay_listing_id = Column(String(20), index=True)
    title = Column(String(100))
    quantity = Column(Integer, nullable=False, default=1)
    # For one of them.
    price = Column(Float)

    def __repr__(self):
        return (self.title or self.ebay_listing_id or "") + " x" + str(self.quantity)


class EbayDraft(Model):
    """
    A listing being prepared for a Unit. It stays here, never on eBay, until
    it has pictures and is published.
    """

    __tablename__ = "ebay_draft"
    id = Column(Integer, primary_key=True, autoincrement=True)
    unit_id = Column(Integer, ForeignKey("unit.id"), nullable=False, index=True)
    unit = relationship("Unit")
    title = Column(String(80), nullable=False)
    price = Column(Float)
    best_offer = Column(Boolean, default=True)
    description = Column(Text)
    item_specifics = Column(Text)
    # Package weight in ounces, packing included.
    weight_oz = Column(Integer)
    # JSON list of eBay-hosted picture URLs, in listing order.
    pictures = Column(Text)
    status = Column(String(20), default="draft", index=True)
    # The last Verify/Publish outcome from eBay: errors, warnings and fees.
    messages = Column(Text)
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"))
    ebay_listing = relationship("EbayListing")

    def __unicode__(self):
        return self.title

    def __repr__(self):
        return self.title

    def picture_list(self):
        if not self.pictures:
            return []
        return json.loads(self.pictures)

    def set_picture_list(self, urls):
        self.pictures = json.dumps(urls)

    def fmt_price(self):
        if self.price is None:
            return None
        return "$" + f"{self.price:.2f}"

    def picture_count(self):
        return len(self.picture_list())

    def link_column(self):
        ret = (
            '<a href="'
            + url_for("UnitModelView.show", pk=str(self.unit_id))
            + '">Unit</a>'
        )
        if self.ebay_listing:
            ret = ret + '<br><a href="' + self.ebay_listing.url + '">eBay:Listing</a>'
        return Markup(ret)


class EbayOAuthToken(Model):
    """
    An OAuth refresh token from Connect eBay, which every eBay call fbc
    makes is signed in with; the newest row is the one used.
    """

    __tablename__ = "ebay_oauth_token"
    id = Column(Integer, primary_key=True, autoincrement=True)
    refresh_token = Column(Text, nullable=False)
    expires = Column(DateTime)
    created = Column(DateTime)

    def __repr__(self):
        return "EbayOAuthToken " + str(self.id)
