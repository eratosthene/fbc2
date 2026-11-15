"""Other Items, under Sales: what's sold on eBay that isn't a record."""

from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.core.forms import CustomModelView
from fbc.models.other import OtherItem
from fbc.sync.notes import EBAY_LISTINGS, EBAY_ORDERS, SyncedViewMixin


class OtherItemModelView(SyncedViewMixin, CustomModelView):
    """
    The listings sync makes these from listings outside Music > Vinyl Records
    (see fbc/ebay/sorting.py), and Ship This marks them sold. One can also be
    added by hand. Cost is blank unless entered: with no cost an Item has no
    profit, and entering one later brings it into the profit shown on the home
    page.
    """

    synced_by = [EBAY_LISTINGS, EBAY_ORDERS]
    datamodel = SQLAInterface(OtherItem)
    list_title = "Other Items"
    show_title = "Other Item"
    add_title = "Add Other Item"
    edit_title = "Edit Other Item"
    base_order = ("id", "desc")
    list_columns = ["name", "fmt_cost", "sold", "fmt_listing", "links"]
    show_columns = ["name", "cost", "sold", "ebay_listing", "sales_receipt"]
    add_columns = ["name", "cost", "ebay_listing"]
    edit_columns = ["name", "cost"]
    search_columns = ["name", "cost", "sold", "ebay_listing"]
    label_columns = {
        "fmt_cost": "Cost",
        "fmt_listing": "eBay Listing",
        "links": "Links",
        "ebay_listing": "eBay Listing",
        "sales_receipt": "Sales Receipt",
    }
    description_columns = {
        "cost": "Leave blank when you don't know it. An Item with no cost has"
        " no profit, and isn't counted in profit on the home page.",
    }
