from flask_appbuilder import Model
from markupsafe import Markup
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Date
from sqlalchemy.orm import relationship


class SalesReceipt(Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    date = Column(Date, nullable=False)
    ebay_order_id = Column(String(20), ForeignKey("ebay_order.order_id"))
    ebay_order = relationship("EbayOrder")
    sold_price = Column(Float)
    net_sold = Column(Float)

    def __unicode__(self):
        ret = str(self.date)
        if self.ebay_order:
            ret = (
                ret
                + " ["
                + self.ebay_order.order_id
                + "|"
                + self.ebay_order.title[:45]
                + "] "
            )
        ret = ret + " $" + f"{self.sold_price:.2f}" + "/$" + f"{self.net_sold:.2f}"
        return ret

    def __repr__(self):
        ret = str(self.date)
        if self.ebay_order:
            ret = (
                ret
                + " ["
                + self.ebay_order.order_id
                + "|"
                + self.ebay_order.title[:45]
                + "] "
            )
        ret = ret + " $" + f"{self.sold_price:.2f}" + "/$" + f"{self.net_sold:.2f}"
        return ret

    def fmt_sold_price(self):
        return "$" + f"{self.sold_price:.2f}"

    def fmt_net_sold(self):
        return "$" + f"{self.net_sold:.2f}"

    def fmt_ebay_order(self, markup=True):
        ret = ""
        if self.ebay_order_id:
            ret = (
                '<a href="https://www.ebay.com/mesh/ord/details?orderid='
                + self.ebay_order_id
                + '">eBay:Order&nbsp;$'
                + f"{self.ebay_order.price:.2f}"
                + "</a>"
            )
        if markup:
            return Markup(ret)
        else:
            return ret

    def link_column(self, markup=True):
        ret = self.fmt_ebay_order(False)
        if markup:
            return Markup(ret)
        else:
            return ret
