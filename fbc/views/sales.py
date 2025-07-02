from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.sales import SalesReceipt
from fbc.widgets import (
    EbayListingListWidget,
    EbayOrderListWidget,
)
from fbc.views.inventory import UnitModelView
from fbc.util import CustomForm


class SalesReceiptModelView(ModelView):
    datamodel = SQLAInterface(SalesReceipt)
    related_views = [UnitModelView]
    list_columns = ["date", "link_column", "fmt_sold_price", "fmt_net_sold"]
    label_columns = {
        "link_column": "Order Link",
        "fmt_sold_price": "Sold Price",
        "fmt_net_sold": "Net Sold",
    }
    show_columns = ["date", "ebay_order", "sold_price", "net_sold"]

    def _init_forms(self):
        super(ModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})


class EbayListingModelView(ModelView):
    datamodel = SQLAInterface(EbayListing)
    list_widget = EbayListingListWidget
    list_columns = ["item_id", "title", "start_time", "fmt_price", "fmt_url"]
    search_columns = ["item_id", "price", "title"]
    label_columns = {"fmt_price": "Price", "fmt_url": "URL"}
    related_views = [UnitModelView]


class EbayOrderModelView(ModelView):
    datamodel = SQLAInterface(EbayOrder)
    list_widget = EbayOrderListWidget
    list_columns = ["created_time", "fmt_url", "title", "buyer", "fmt_price", "links"]
    search_columns = ["order_id", "price", "title", "buyer"]
    label_columns = {"fmt_price": "Price", "fmt_url": "Order Id", "links": "Links"}
    related_views = [SalesReceiptModelView]
