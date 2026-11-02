from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import expose, has_access
from wtforms.validators import Optional

from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.sales import MANUAL, SalesReceipt
from fbc.shipping import (
    discogs_plan,
    fmt_supplies,
    order_earnings,
    order_receipt,
    order_unit,
    remove_from_discogs,
    ship_order,
    stock_problems,
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


# Why a receipt whose eBay order is on other receipts gets no lines.
SHARED_ORDER = (
    "This Sales Receipt's eBay order is on other Sales Receipts too, so eBay's"
    " earnings for the order can't be split between them. Its fees aren't"
    " broken down."
)


def flash_adjustment(receipt):
    """Say so when a receipt keeps a manual adjustment line."""
    for line in receipt.lines:
        if line.kind == MANUAL:
            flash(
                "Net Sold differs from eBay's earnings by "
                + line.fmt_amount()
                + ", so the Sales Receipt keeps that as a manual adjustment.",
                "info",
            )


def _lines_total(earnings):
    """eBay's lines' total as the earnings page shows it, to compare on POST."""
    return f"{sum(line['amount'] for line in earnings['lines']):.2f}"


class SalesReceiptModelView(CustomModelView):
    datamodel = SQLAInterface(SalesReceipt)
    related_views = [UnitModelView]
    show_template = "sales_receipt/show.html"
    base_order = ("date", "desc")
    list_columns = ["date", "link_column", "fmt_sold_price", "fmt_net_sold"]
    label_columns = {
        "link_column": "Order Link",
        "fmt_sold_price": "Sold Price",
        "fmt_net_sold": "Net Sold",
    }
    show_columns = ["date", "ebay_order", "sold_price", "net_sold"]
    # Lines come from eBay (Ship This and Refresh Earnings), not the form.
    add_columns = ["ebay_order", "old_id", "date", "sold_price", "net_sold"]
    edit_columns = add_columns
    search_columns = add_columns

    def _init_forms(self):
        super(CustomModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})

    def pre_update(self, item):
        # Net Sold changed by hand: keep the lines adding up to it.
        item.balance_lines()

    def _receipt(self, pk):
        receipt = self.datamodel.get(pk)
        if not receipt:
            abort(404)
        return receipt

    @expose("/show/<pk>", methods=["GET"])
    @has_access
    def show(self, pk):
        receipt = self._receipt(pk)
        widgets = self._show(pk)
        return self.render_template(
            self.show_template,
            pk=pk,
            title=self.show_title,
            widgets=widgets,
            related_views=self._related_views,
            receipt=receipt,
            shared_order=SHARED_ORDER if receipt.order_receipts() > 1 else None,
        )

    @expose("/earnings/<pk>", methods=["GET", "POST"])
    @has_access
    def earnings(self, pk):
        """
        Refresh Earnings: read the order's transactions from eBay again, for
        a refund or a charge that came after shipping, and replace the
        receipt's lines with them. GET shows what will change; POST saves
        it, and sets Net Sold to eBay's total if that's ticked.
        """
        from fbc import db

        receipt = self._receipt(pk)
        back = redirect(url_for("SalesReceiptModelView.show", pk=receipt.id))
        if not receipt.ebay_order_id:
            flash("This Sales Receipt isn't linked to an eBay order.", "warning")
            return back
        if receipt.order_receipts() > 1:
            flash(SHARED_ORDER, "warning")
            return back
        earnings = order_earnings(receipt.ebay_order_id)
        if request.method == "POST":
            if not earnings["lines"]:
                flash(
                    "eBay didn't give any earnings for this order, so nothing"
                    " was changed.",
                    "warning",
                )
            elif request.form.get("total") != _lines_total(earnings):
                flash(
                    "eBay's earnings for this order changed since this page was"
                    " shown. Check them again before saving.",
                    "warning",
                )
            else:
                if request.form.get("update_net") and earnings["net"] is not None:
                    receipt.net_sold = earnings["net"]
                receipt.set_lines(earnings["lines"])
                db.session.commit()
                flash(
                    "Saved eBay's earnings. Net Sold is "
                    + receipt.fmt_net_sold()
                    + ".",
                    "success",
                )
                flash_adjustment(receipt)
                return back
        return self.render_template(
            "sales_receipt/earnings.html",
            title="Refresh Earnings",
            receipt=receipt,
            earnings=earnings,
            total=_lines_total(earnings),
        )


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
        # Asked for again on a POST, so the receipt keeps eBay's lines as
        # they are when it's saved.
        earnings = order_earnings(order.order_id)
        # A POST with anything wrong shows the page again with what was
        # entered, so it doesn't all need typing in again.
        values = request.form if request.method == "POST" else None
        if values is not None:
            problems = []
            try:
                net_sold = float(values.get("net_sold", "").strip().lstrip("$"))
            except ValueError:
                problems.append("Enter Net Sold as a number of dollars.")
            try:
                used = supply_counts(values, [s for s, _ in supplies])
                problems.extend(stock_problems(used))
            except ValueError as e:
                problems.append(str(e))
            if not problems:
                receipt = ship_order(order, unit, net_sold, used, earnings["lines"])
                flash(
                    "Recorded a Sales Receipt for $"
                    + f"{net_sold:.2f}"
                    + " and marked "
                    + unit.name
                    + " sold.",
                    "success",
                )
                flash_adjustment(receipt)
                if used:
                    flash("Took " + fmt_supplies(used) + " out of supplies.", "success")
                if values.get("remove_discogs"):
                    message, category = remove_from_discogs(unit)
                    flash(message, category)
                return back
            for problem in problems:
                flash(problem, "danger")

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
            values=values,
        )
