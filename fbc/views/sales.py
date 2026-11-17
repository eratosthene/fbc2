from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose, has_access
from flask_appbuilder.actions import action
from flask_appbuilder.models.filters import BaseFilter
import sqlalchemy as sa
from markupsafe import Markup
from flask_appbuilder.models.sqla.filters import get_field_setup_query
from sqlalchemy import or_
from wtforms.validators import Optional, ValidationError

from fbc.ebay import mystery
from fbc.core import localtime
from fbc.sales.archive import ArchivableViewMixin
from fbc.sync.notes import EBAY_LISTINGS, EBAY_ORDERS, SyncedViewMixin
from fbc.core.forms import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.other import listing_ids as other_listing_ids
from fbc.models.sales import MANUAL, ListingEnd, SalesReceipt
from fbc.ebay.finances import shipment_earnings
from fbc.sales.ship import (
    combinable_orders,
    combined_discount,
    order_receipt,
    make_other_items,
    other_item_candidates,
    other_items,
    other_marketplace_notes,
    shipment_units,
    ship_order,
)
from fbc.sales.stock import (
    fmt_supplies,
    other_supplies_plan,
    stock_problems,
    supplies_plan,
    supply_counts,
)
from fbc.discogs.removal import discogs_plan, remove_from_discogs
from fbc.core.widgets import (
    LocalDateTimeField,
    EbayListingListWidget,
    EbayOrderListWidget,
)
from fbc.views.inventory import UnitModelView
from fbc.core.forms import CustomForm


class FilterNotInSelect(BaseFilter):
    name = "Filter view where field is not in a SELECT returned by a function"
    arg_name = "not_in_sel"

    def apply(self, query, func):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        # NOT IN is never true for a NULL field, which should stay in the list.
        return query.filter(or_(field.is_(None), field.notin_(func())))


# Why a receipt whose eBay order is on other receipts gets no lines.
SHARED_ORDER = (
    "This Sales Receipt's eBay order is on other Sales Receipts too, so eBay's"
    " earnings for the order can't be split between them. Its fees aren't"
    " broken down until they're combined: select them in the Sales Receipts"
    " list and choose Combine."
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


def unit_names(units):
    """One Unit's name, or how many Units."""
    if len(units) == 1:
        return units[0].name
    return str(len(units)) + " Units"


def _lines_total(earnings):
    """eBay's lines' total as the earnings page shows it, to compare on POST."""
    return f"{sum(line['amount'] for line in earnings['lines']):.2f}"


def not_both_marketplaces(form, field):
    """A Sales Receipt is for one marketplace: not an eBay and a Discogs order."""
    if field.data and form.ebay_orders.data:
        raise ValidationError(
            "A Sales Receipt can't have both an eBay order and a Discogs order."
            " Clear one of them."
        )


class FilterHasNoOrder(BaseFilter):
    """
    Sales Receipts with no eBay order and no Discogs order: the ones sold in
    person or entered by hand. Flask-AppBuilder has no "is empty" filter for
    a many-to-many link, so this is one. It sits on the eBay Orders column and
    ignores the value, which is only there because every filter has one.
    """

    name = "Has no order"
    arg_name = "no_order"

    def apply(self, query, value):
        from fbc.models.sales import (
            sales_receipt_discogs_order,
            sales_receipt_ebay_order,
        )

        def linked(table):
            return sa.select(table.c.sales_receipt_id)

        return query.filter(
            SalesReceipt.id.notin_(linked(sales_receipt_ebay_order)),
            SalesReceipt.id.notin_(linked(sales_receipt_discogs_order)),
        )


class FilterHasEbayOrder(BaseFilter):
    """Sales Receipts of an eBay order, for the home page's eBay row."""

    name = "Has an order"
    arg_name = "has_ebay_order"

    def apply(self, query, value):
        from fbc.models.sales import sales_receipt_ebay_order

        return query.filter(
            SalesReceipt.id.in_(sa.select(sales_receipt_ebay_order.c.sales_receipt_id))
        )


class FilterHasDiscogsOrder(BaseFilter):
    """Sales Receipts of a Discogs order, for the home page's Discogs row."""

    name = "Has an order"
    arg_name = "has_discogs_order"

    def apply(self, query, value):
        from fbc.models.sales import sales_receipt_discogs_order

        return query.filter(
            SalesReceipt.id.in_(
                sa.select(sales_receipt_discogs_order.c.sales_receipt_id)
            )
        )


class SalesReceiptModelView(ArchivableViewMixin, CustomModelView):
    datamodel = SQLAInterface(SalesReceipt)
    related_views = [UnitModelView]
    show_template = "sales_receipt/show.html"
    base_order = ("date", "desc")
    list_columns = ["date", "link_column", "fmt_sold_price", "fmt_net_sold"]
    label_columns = {
        "link_column": "Order Link",
        "fmt_sold_price": "Sold Price",
        "fmt_net_sold": "Net Sold",
        "ebay_orders": "eBay Orders",
        "discogs_orders": "Discogs Orders",
    }
    show_columns = [
        "date",
        "ebay_orders",
        "discogs_orders",
        "sold_price",
        "net_sold",
        "notes",
    ]
    # Lines come from eBay or Discogs (Ship This and Refresh Earnings), not the form.
    add_columns = [
        "ebay_orders",
        "discogs_orders",
        "date",
        "sold_price",
        "net_sold",
        "notes",
    ]
    edit_columns = add_columns
    search_columns = add_columns + ["archived"]
    validators_columns = {"discogs_orders": [not_both_marketplaces]}

    def _init_properties(self):
        super()._init_properties()
        # First, so the home page can link to them by position: Has no order
        # is _flt_0_ebay_orders, Has an order is _flt_1_ebay_orders and
        # _flt_0_discogs_orders.
        for table in (self._filters._search_filters, self._filters._all_filters):
            table["ebay_orders"] = [
                FilterHasNoOrder("ebay_orders", self.datamodel),
                FilterHasEbayOrder("ebay_orders", self.datamodel),
            ] + table["ebay_orders"]
            table["discogs_orders"] = [
                FilterHasDiscogsOrder("discogs_orders", self.datamodel)
            ] + table["discogs_orders"]

    def _init_forms(self):
        super(CustomModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})

    def pre_update(self, item):
        # Net Sold changed by hand: keep the lines adding up to it.
        item.balance_lines()

    @action(
        "combine",
        "Combine",
        "Combine the selected Sales Receipts into one, for a shipment recorded"
        " as several?",
        "fa-compress",
        single=False,
    )
    def combine(self, items):
        """
        Put receipts that stand for one shipment together (see
        combine.combine_receipts), for the ones flask combine-receipts
        doesn't find by itself.
        """
        from fbc import db
        from fbc.sales.combine import combine_receipts

        if not isinstance(items, list) or len(items) < 2:
            flash("Select two or more Sales Receipts to combine.", "warning")
            return redirect(url_for("SalesReceiptModelView.list"))
        receipt = combine_receipts(items)
        db.session.commit()
        flash(
            "Combined "
            + str(len(items))
            + " Sales Receipts into this one. Sold Price is the orders' total and"
            " Net Sold the receipts'.",
            "success",
        )
        if not receipt.lines:
            flash("It has no earnings lines yet; refresh them from eBay below.", "info")
        return redirect(url_for("SalesReceiptModelView.show", pk=receipt.id))

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
            discogs_note=bool(receipt.discogs_orders),
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
        if not receipt.ebay_orders:
            flash("This Sales Receipt isn't linked to an eBay order.", "warning")
            return back
        if receipt.order_receipts() > 1:
            flash(SHARED_ORDER, "warning")
            return back
        earnings = shipment_earnings(receipt.order_ids())
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


class EbayListingModelView(SyncedViewMixin, ArchivableViewMixin, CustomModelView):
    synced_by = [EBAY_LISTINGS]
    datamodel = SQLAInterface(EbayListing)
    # Other Items' listings are on the Other Items list instead.
    base_filters = [["item_id", FilterNotInSelect, other_listing_ids]]
    base_order = ("start_time", "desc")
    list_widget = EbayListingListWidget
    formatters_columns = localtime.formatters("start_time")
    list_columns = ["item_id", "title", "start_time", "ended", "fmt_price", "fmt_url"]
    search_columns = ["item_id", "price", "title", "ended", "archived"]
    label_columns = {
        "fmt_price": "Price",
        "fmt_url": "URL",
        "pick_from_box": "Mystery Box: Pick From Storage Box",
        "pick_count": "Mystery Box: Units Per Sale",
    }
    description_columns = {
        "pick_from_box": "For a mystery box listing: the Storage Box its Units are"
        " picked from at random. Leave blank for any other listing.",
        "pick_count": "How many Units each sale gets, for a mystery box listing.",
    }
    related_views = [UnitModelView]
    show_columns = [
        "item_id",
        "title",
        "price",
        "url",
        "start_time",
        "ended",
        "pick_from_box",
        "pick_count",
    ]
    add_columns = [
        "item_id",
        "title",
        "price",
        "url",
        "start_time",
        "pick_from_box",
        "pick_count",
    ]
    edit_columns = [
        "title",
        "price",
        "url",
        "start_time",
        "pick_from_box",
        "pick_count",
    ]
    add_form_extra_fields = {
        "start_time": LocalDateTimeField("Start Time", validators=[Optional()])
    }
    edit_form_extra_fields = add_form_extra_fields

    def _check_mystery_box(self, item):
        if bool(item.pick_from_box) != bool(item.pick_count):
            raise Exception(
                "Set both the Storage Box and Units Per Sale for a mystery box, or"
                " neither."
            )
        if item.pick_count is not None and item.pick_count < 1:
            raise Exception("Units Per Sale must be 1 or more.")

    pre_add = _check_mystery_box
    pre_update = _check_mystery_box


class EbayOrderModelView(SyncedViewMixin, ArchivableViewMixin, CustomModelView):
    # An order links to its listing, which the listings sync keeps.
    synced_by = [EBAY_ORDERS, EBAY_LISTINGS]
    datamodel = SQLAInterface(EbayOrder)
    # Orders for Other Items are on the Other Items list instead (and still on
    # Orders to Ship, which has its own filter).
    base_filters = [["ebay_listing_id", FilterNotInSelect, other_listing_ids]]
    base_order = ("created_time", "desc")
    list_widget = EbayOrderListWidget
    formatters_columns = localtime.formatters("created_time")
    list_columns = [
        "created_time",
        "fmt_url",
        "fmt_title",
        "buyer",
        "fmt_price",
        "fmt_tracking",
        "links",
    ]
    search_columns = [
        "order_id",
        "price",
        "title",
        "buyer",
        "tracking_number",
        "archived",
    ]
    label_columns = {
        "fmt_price": "Price",
        "fmt_url": "Order Id",
        "fmt_title": "Title",
        "links": "Links",
        "fmt_tracking": "Tracking",
    }
    show_columns = [
        "order_id",
        "created_time",
        "buyer",
        "fmt_title",
        "fmt_price",
        "fmt_tracking",
        "fmt_url",
    ]
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
        "created_time": LocalDateTimeField("Created Time", validators=[Optional()])
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
        Ship This: once the label is bought on eBay, record one Sales Receipt
        for the order, and any of the buyer's other orders going in the same
        package (`combine`), for their earnings. Mark their Units sold, take
        the supplies used out of stock and remove the Units' instances from
        Discogs. GET shows what will happen; POST does it.
        """
        order = self._order(pk)
        back = redirect(url_for("EbayOrderModelView.list"))
        if order_receipt(order):
            flash("This order already has a Sales Receipt.", "warning")
            return back
        sold_items, item_problems = other_items(order)
        if sold_items:
            return self._ship_other_items(order, sold_items, item_problems, back)
        candidates = other_item_candidates(order)
        if candidates:
            # Nothing says what this order is: offer to make Other Items of it.
            if request.form.get("action") == "make_other":
                made = make_other_items(order, candidates)
                flash(
                    "Made "
                    + ", ".join(i.name for i in made)
                    + " an Other Item"
                    + ("" if len(made) == 1 else "s")
                    + ". Ship it now.",
                    "success",
                )
                return redirect(url_for("EbayOrderModelView.ship", pk=order.order_id))
            return self.render_template(
                "ebay_order/make_other.html",
                title="Ship This",
                order=order,
                candidates=candidates,
            )
        values = request.form if request.method == "POST" else None
        others = combinable_orders(order)
        combine = set((values or request.args).getlist("combine"))
        orders = sorted(
            [order] + [o for o in others if o.order_id in combine],
            key=lambda o: (o.created_time, o.order_id),
        )
        units, problems = shipment_units(orders)
        if units is None:
            for problem in problems:
                flash(problem, "warning")
            flash(
                Markup(
                    "Ship This can't tell which Units these are, so ship it by"
                    ' hand: mark the Units sold and <a href="{0}">add its Sales'
                    " Receipt</a>."
                ).format(order.add_receipt_url()),
                "warning",
            )
            return back

        # Mystery boxes have no Units yet: pick them, or keep the picks posted.
        boxes = []
        for box_listing, wanted in mystery.needs(orders):
            picks = mystery.fill(box_listing, wanted, order.buyer, values)
            boxes.append(dict(picks, listing=box_listing, wanted=wanted))
        fixed_units = units
        units = fixed_units + [u for b in boxes for u in b["units"]]
        for b in boxes:
            for note in b["notes"]:
                flash(note, "warning")
            b["other_boxes"] = (
                mystery.other_boxes(b["listing"]) if b["short"] or b["fill_box"] else []
            )

        supplies = supplies_plan(units)
        # Asked for again on a POST, so the receipt keeps eBay's lines as
        # they are when it's saved.
        earnings = shipment_earnings([o.order_id for o in orders])
        # Orders synced before fbc kept every item only know their first.
        known_items = sum(len(o.items) or 1 for o in orders)
        missing_items = earnings["items"] > known_items
        # A POST with anything wrong shows the page again with what was
        # entered, so it doesn't all need typing in again.
        # A mystery box's re-roll and swap buttons post the page too; only the
        # Ship button ships.
        ship_now = values is not None and (not boxes or values.get("action") == "ship")
        if ship_now and not missing_items:
            problems = []
            for b in boxes:
                if b["short"]:
                    problems.append(
                        "Pick "
                        + str(b["short"])
                        + " more Unit"
                        + ("" if b["short"] == 1 else "s")
                        + " for "
                        + b["listing"].title
                        + ": its box doesn't have enough."
                    )
                elif b["changed"]:
                    problems.append(
                        "The picks for "
                        + b["listing"].title
                        + " changed, so check them and press Ship again."
                    )
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
                unit_prices = {}
                for b in boxes:
                    price = mystery.unit_price(
                        [i for o in orders for i in o.items], b["listing"]
                    )
                    if price is not None:
                        unit_prices.update({u.id: price for u in b["units"]})
                receipt = ship_order(
                    orders, units, net_sold, used, earnings["lines"], unit_prices
                )
                flash(
                    "Recorded a Sales Receipt for $"
                    + f"{net_sold:.2f}"
                    + (
                        " for " + str(len(orders)) + " orders"
                        if len(orders) > 1
                        else ""
                    )
                    + " and marked "
                    + (unit_names(units))
                    + " sold.",
                    "success",
                )
                flash_adjustment(receipt)
                if used:
                    flash("Took " + fmt_supplies(used) + " out of supplies.", "success")
                for unit in units:
                    if values.get("remove_discogs-" + str(unit.id)):
                        message, category = remove_from_discogs(unit)
                        flash(message, category)
                return back
            for problem in problems:
                flash(problem, "danger")

        discogs = [(unit,) + discogs_plan(unit) for unit in units]
        return self.render_template(
            "ebay_order/ship.html",
            title="Ship This",
            order=order,
            orders=orders,
            others=others,
            units=units,
            fixed_units=fixed_units,
            boxes=boxes,
            earnings=earnings,
            missing_items=missing_items,
            known_items=known_items,
            discount=combined_discount(orders, earnings),
            discogs=discogs,
            notes=other_marketplace_notes(units, "eBay"),
            supplies=supplies,
            values=values,
        )

    def _ship_other_items(self, order, items, problems, back):
        """
        Ship This for an order of Other Items: one Sales Receipt, the Items
        marked sold, and the supplies typed in by hand. No Units, no
        combining with other orders and no Discogs.
        """
        if problems:
            for problem in problems:
                flash(problem, "warning")
            flash(
                Markup(
                    'Ship it by hand: <a href="{0}">add its Sales Receipt</a>.'
                ).format(order.add_receipt_url()),
                "warning",
            )
            return back
        values = request.form if request.method == "POST" else None
        supplies = other_supplies_plan()
        earnings = shipment_earnings([order.order_id])
        known_items = len(order.items) or 1
        missing_items = earnings["items"] > known_items
        if values is not None and not missing_items:
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
                receipt = ship_order(
                    [order],
                    [],
                    net_sold,
                    used,
                    earnings["lines"],
                    other_items=items,
                )
                flash(
                    "Recorded a Sales Receipt for $"
                    + f"{net_sold:.2f}"
                    + " and marked "
                    + ", ".join(i.name for i in items)
                    + " sold.",
                    "success",
                )
                flash_adjustment(receipt)
                if used:
                    flash("Took " + fmt_supplies(used) + " out of supplies.", "success")
                return back
            for problem in problems:
                flash(problem, "danger")
        return self.render_template(
            "ebay_order/ship_other.html",
            title="Ship This",
            order=order,
            items=items,
            earnings=earnings,
            missing_items=missing_items,
            known_items=known_items,
            supplies=supplies,
            values=values,
        )


class ListingEndModelView(CustomModelView):
    """
    The listings fbc ended, or tried to end, because their Unit sold on the
    other marketplace (see fbc/sync/ending.py). Read only: it's a log.
    """

    datamodel = SQLAInterface(ListingEnd)
    base_permissions = ["can_list", "can_show"]
    base_order = ("updated", "desc")
    formatters_columns = localtime.formatters("created", "updated")
    list_columns = [
        "updated",
        "unit",
        "fmt_order",
        "ended_on",
        "fmt_listing",
        "status",
        "message",
    ]
    show_columns = list_columns + ["created"]
    search_columns = ["unit", "sold_on", "ended_on", "listing_ref", "status"]
    label_columns = {
        "fmt_order": "Sold On",
        "fmt_listing": "Listing",
        "ended_on": "Ended On",
    }


class MysteryBoxesView(BaseView):
    """
    Mystery Boxes, under Sales: each active mystery box listing and how many
    more boxes its Storage Box can fill (see fbc.ebay.mystery).
    """

    route_base = "/mysteryboxes"
    default_view = "boxes"

    @expose("/")
    @has_access
    def boxes(self):
        listings = [
            (listing, mystery.boxes_available(listing))
            for listing in mystery.active_listings()
        ]
        return self.render_template("mystery_boxes.html", listings=listings)
