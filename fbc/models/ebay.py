import logging

from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from sqlalchemy import (
    Column,
    String,
    ForeignKey,
    Float,
    DateTime,
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
            + '">Add SR</a>'
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
                    + '">Unit</a>'
                )

        return Markup(retval)
