from wtforms.validators import Optional

from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.sales import SalesReceipt
from fbc.widgets import (
    DateTimeLocalField,
    EbayListingListWidget,
    EbayOrderListWidget,
)
from fbc.views.inventory import UnitModelView
from fbc.util import CustomForm


class SalesReceiptModelView(CustomModelView):
    datamodel = SQLAInterface(SalesReceipt)
    related_views = [UnitModelView]
    base_order = ('date', 'desc')
    list_columns = ["date", "link_column", "fmt_sold_price", "fmt_net_sold"]
    label_columns = {
        "link_column": "Order Link",
        "fmt_sold_price": "Sold Price",
        "fmt_net_sold": "Net Sold",
    }
    show_columns = ["date", "ebay_order", "sold_price", "net_sold"]

    def _init_forms(self):
        super(CustomModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})


class EbayListingModelView(CustomModelView):
    datamodel = SQLAInterface(EbayListing)
    base_order = ('start_time', 'desc')
    list_widget = EbayListingListWidget
    list_columns = ["item_id", "title", "start_time", "fmt_price", "fmt_url"]
    search_columns = ["item_id", "price", "title"]
    label_columns = {"fmt_price": "Price", "fmt_url": "URL"}
    related_views = [UnitModelView]
    add_columns = ["item_id", "title", "price", "url", "start_time"]
    edit_columns = ["title", "price", "url", "start_time"]
    add_form_extra_fields = {
        "start_time": DateTimeLocalField("Start Time", validators=[Optional()])
    }
    edit_form_extra_fields = add_form_extra_fields


class EbayOrderModelView(CustomModelView):
    datamodel = SQLAInterface(EbayOrder)
    base_order = ('created_time', 'desc')
    list_widget = EbayOrderListWidget
    list_columns = ["created_time", "fmt_url", "title", "buyer", "fmt_price", "links"]
    search_columns = ["order_id", "price", "title", "buyer"]
    label_columns = {"fmt_price": "Price", "fmt_url": "Order Id", "links": "Links"}
    related_views = [SalesReceiptModelView]
    add_columns = [
        "order_id",
        "created_time",
        "buyer",
        "price",
        "title",
        "ebay_listing",
    ]
    edit_columns = ["created_time", "buyer", "price", "title", "ebay_listing"]
    add_form_extra_fields = {
        "created_time": DateTimeLocalField("Created Time", validators=[Optional()])
    }
    edit_form_extra_fields = add_form_extra_fields
