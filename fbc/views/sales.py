from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import expose, has_access
from wtforms.validators import Optional

from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.sales import SalesReceipt
from fbc.shipping import (
    discogs_plan,
    fmt_supplies,
    order_earnings,
    order_receipt,
    order_unit,
    remove_from_discogs,
    ship_order,
    supplies_plan,
    supply_counts,
)
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
    base_order = ("date", "desc")
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
    base_order = ("start_time", "desc")
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
    base_order = ("created_time", "desc")
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

    def _order(self, pk):
        order = self.datamodel.get(pk)
        if not order:
            abort(404)
        return order

    @expose("/ship/<pk>", methods=["GET", "POST"])
    @has_access
    def ship(self, pk):
        """
        Ship This: once the label is bought on eBay, record the Sales Receipt
        for the order's earnings, mark its Unit sold, take the supplies it
        used out of stock and remove the Unit's instance from Discogs. GET
        shows what will happen; POST does it.
        """
        order = self._order(pk)
        back = redirect(url_for("EbayOrderModelView.list"))
        if order_receipt(order):
            flash("This order already has a Sales Receipt.", "warning")
            return back
        unit = order_unit(order)
        if not unit:
            flash(
                "This order doesn't have exactly one unsold Unit on its listing;"
                " ship it by hand.",
                "warning",
            )
            return back

        supplies = supplies_plan([unit])
        if request.method == "POST":
            again = redirect(url_for("EbayOrderModelView.ship", pk=pk))
            try:
                net_sold = float(request.form.get("net_sold", "").strip().lstrip("$"))
            except ValueError:
                flash("Enter Net Sold as a number of dollars.", "danger")
                return again
            try:
                used = supply_counts(request.form, [s for s, _ in supplies])
            except ValueError as e:
                flash(str(e), "danger")
                return again
            ship_order(order, unit, net_sold, used)
            flash(
                "Recorded a Sales Receipt for $"
                + f"{net_sold:.2f}"
                + " and marked "
                + unit.name
                + " sold.",
                "success",
            )
            if used:
                flash("Took " + fmt_supplies(used) + " out of supplies.", "success")
            if request.form.get("remove_discogs"):
                message, category = remove_from_discogs(unit)
                flash(message, category)
            return back

        earnings = order_earnings(order.order_id)
        instance, discogs_problem = discogs_plan(unit)
        return self.render_template(
            "ebay_order/ship.html",
            title="Ship This",
            order=order,
            unit=unit,
            earnings=earnings,
            instance=instance,
            discogs_problem=discogs_problem,
            supplies=supplies,
        )
