import json
import logging

from flask_appbuilder import Model
from fbc import localtime
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
    false,
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
    # The listings sync sets these when a listing is no longer in eBay's
    # active list. end_time is the last sync that still saw it active (or
    # start_time if there was none), so "ended no later than"; see
    # fbc/util.py mark_ended_listings. Times are UTC, as eBay sends them.
    ended = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    end_time = Column(DateTime)
    # When the listing renews next, from eBay's Time Left at the last
    # listings sync (UTC); see insertions.renewal_time.
    renews_at = Column(DateTime)
    # A mystery box listing sells `pick_count` random Units from one Storage
    # Box with each sale, so none of those Units is linked to it. Columns,
    # not a SKU: fbc doesn't keep SKUs, and these show on the edit page.
    # util.relist_ebay_listing carries them to the relisted listing. Both set
    # or both blank; see fbc/mystery.py.
    pick_from_box_id = Column(Integer, ForeignKey("storage_box.id"), index=True)
    pick_from_box = relationship("StorageBox")
    pick_count = Column(Integer)
    # Hidden from the lists by default; see fbc/archive.py.
    archived = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    def __unicode__(self):
        return self.item_id + ": " + self.title + " $" + f"{self.price:.2f}"

    def __repr__(self):
        return self.item_id + ": " + self.title + " $" + f"{self.price:.2f}"

    def fmt_price(self):
        return "$" + f"{self.price:.2f}"

    def fmt_url(self):
        return Markup('<a href="' + self.url + '">Listing</a>')

    def is_mystery_box(self):
        return bool(self.pick_from_box_id and self.pick_count)

    def fmt_boxes_available(self):
        """How many more mystery boxes the Storage Box can fill, or blank."""
        from fbc import mystery

        if not self.is_mystery_box():
            return ""
        return mystery.boxes_available(self)


class InsertionCalibration(Model):
    """
    What the eBay app said was left of a month's free insertions, and how far
    fbc's own count was from it then (see fbc/insertions.py). One row a month,
    keyed by the month's start, so a new month starts with none.
    """

    __tablename__ = "insertion_calibration"
    # UTC start of the month, from insertions.month_bounds.
    month_start = Column(DateTime, primary_key=True)
    # Added to the month's used count: eBay's number less fbc's.
    adjustment = Column(Integer, nullable=False)
    # The free insertions the eBay app showed as left, and when it was entered.
    remaining = Column(Integer, nullable=False)
    entered = Column(DateTime, nullable=False)


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
    # From eBay once the order has shipped; see fbc/tracking.py.
    shipping_carrier = Column(String(40))
    tracking_number = Column(String(40))
    # The first item's title and listing; every item is in items.
    title = Column(String(100))
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"))
    # Hidden from the lists by default; see fbc/archive.py.
    archived = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
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
        ret = localtime.fmt_date(self.created_time) + " " + self.order_id
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

    def fmt_tracking(self):
        """The carrier and tracking number, linked to the carrier's page."""
        from markupsafe import escape

        from fbc.tracking import carrier_name, tracking_url

        if not self.tracking_number:
            return ""
        text = escape(
            (carrier_name(self.shipping_carrier) + " " + self.tracking_number).strip()
        )
        url = tracking_url(self.shipping_carrier, self.tracking_number)
        if not url:
            return text
        return Markup('<a href="{}" target="_blank" rel="noopener">{}</a>').format(
            url, text
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

    def fmt_title(self):
        """The title, or for an order of several items, all of them."""
        from markupsafe import escape

        if len(self.items) <= 1 and not any(
            (item.quantity or 1) > 1 for item in self.items
        ):
            return self.title
        units = sum(item.quantity or 1 for item in self.items)
        return Markup(
            "<b>"
            + str(units)
            + " Units:</b><br>"
            + "<br>".join(
                str(escape(item.title or item.ebay_listing_id or ""))
                + ("" if (item.quantity or 1) == 1 else " &times;" + str(item.quantity))
                for item in self.items
            )
        )

    def add_receipt_url(self):
        """Add SR with this order filled in, for an order shipped by hand."""
        return url_for(
            "SalesReceiptModelView.add",
            sold_price=self.price,
            ebay_orders=self.order_id,
            date=self.created_time.date(),
        )

    def links(self):
        """
        Its Sales Receipt once it's shipped, and Ship This until then, which
        also offers the buyer's other orders and says when it can't help.
        """
        if self.sales_receipts:
            return Markup(
                '<a href="'
                + url_for(
                    "SalesReceiptModelView.show", pk=str(self.sales_receipts[0].id)
                )
                + '">Sales&nbsp;Receipt</a>'
            )
        return Markup(
            '<a href="'
            + url_for("EbayOrderModelView.ship", pk=self.order_id)
            + '">Ship&nbsp;This</a>'
        )


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
