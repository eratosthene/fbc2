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
    order_id = Column(String(20), primary_key=True, unique=True)
    created_time = Column(DateTime)
    buyer = Column(String(50))
    price = Column(Float)
    title = Column(String(100))
    ebay_listing_id = Column(String(20), ForeignKey("ebay_listing.item_id"))
    ebay_listing = relationship("EbayListing")

    def __unicode__(self):
        if self.title:
            return (
                str(self.created_time.date())
                + " "
                + self.order_id
                + " ["
                + self.title[:45]
                + "]: "
                + self.buyer
                + " $"
                + f"{self.price:.2f}"
            )
        else:
            return (
                str(self.created_time.date())
                + " "
                + self.order_id
                + ": "
                + self.buyer
                + " $"
                + f"{self.price:.2f}"
            )

    def __repr__(self):
        if self.title:
            return (
                str(self.created_time.date())
                + " "
                + self.order_id
                + " ["
                + self.title[:45]
                + "]: "
                + self.buyer
                + " $"
                + f"{self.price:.2f}"
            )
        else:
            return (
                str(self.created_time.date())
                + " "
                + self.order_id
                + ": "
                + self.buyer
                + " $"
                + f"{self.price:.2f}"
            )

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

    def links(self):
        retval = (
            '<a href="'
            + url_for(
                "SalesReceiptModelView.add",
                sold_price=self.price,
                ebay_order=self.order_id,
                date=self.created_time.date(),
            )
            + '">Add&nbsp;SR</a>'
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


class EbayDraft(Model):
    """
    A listing being prepared for a Unit. It stays here, never on eBay, until
    it has pictures and is published.
    """

    id = Column(Integer, primary_key=True, autoincrement=True)
    unit_id = Column(Integer, ForeignKey("unit.id"), nullable=False, index=True)
    unit = relationship("Unit")
    title = Column(String(80), nullable=False)
    price = Column(Float)
    best_offer = Column(Boolean, default=True)
    description = Column(Text)
    item_specifics = Column(Text)
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
    An OAuth refresh token from Connect eBay. eBay's REST APIs (picture
    uploads) need OAuth; the newest row is the one used.
    """

    __tablename__ = "ebay_oauth_token"
    id = Column(Integer, primary_key=True, autoincrement=True)
    refresh_token = Column(Text, nullable=False)
    expires = Column(DateTime)
    created = Column(DateTime)

    def __repr__(self):
        return "EbayOAuthToken " + str(self.id)
