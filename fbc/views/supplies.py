from fbc import localtime
from fbc.util import CustomModelView
from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose, has_access
from flask_appbuilder.fieldwidgets import Select2Widget
from flask_appbuilder.models.sqla.interface import SQLAInterface
from wtforms import SelectField
from wtforms.validators import NumberRange, Optional, ValidationError

from fbc.listing import SIZES
from fbc.views.expenses import ExpenseModelView
from fbc.models.supplies import (
    RECONCILE,
    USED_FOR_LABELS,
    PurchaseOrder,
    PurchaseOrderSupply,
    Supply,
    SupplyUse,
    fmt_stock,
    uses_left,
)

USED_FOR_CHOICES = [(value or "", label) for value, label in USED_FOR_LABELS.items()]


def pack_used_fits(form, field):
    """Used From The One In Use has to be less than Uses Each, and 0 with none in stock."""
    if field.data is None:
        return
    if form.pack_size.data and field.data >= form.pack_size.data:
        raise ValidationError(
            "Must be less than Uses Each: once all of one are used, Quantity goes"
            " down by one instead."
        )
    if form.quantity.data == 0 and field.data:
        raise ValidationError("Must be 0 when Quantity is 0, since none is in use.")


# used_for and record_size are stored as NULL when left blank; coercing the
# blank choice to None too keeps it the selected (and a valid) choice.
SUPPLY_FORM_EXTRA_FIELDS = {
    "used_for": SelectField(
        "Used For",
        choices=USED_FOR_CHOICES,
        coerce=lambda value: value or None,
        widget=Select2Widget(),
        description="Where it's offered to be taken out of stock: Ship This, or"
        " the Supplies Used on Add Unit.",
    ),
    "record_size": SelectField(
        "Record Size",
        choices=[("", "Any")] + [(s, s) for s in SIZES],
        coerce=lambda value: value or None,
        widget=Select2Widget(),
        description='When shipping, only count Units of this size, like a 7"'
        ' sleeve for 7" records.',
    ),
}


class NoAutoflushForm:
    """
    Fills in its object without flushing it part way through, as loading
    Purchase Orders would. A flush forgets what Quantity and Used From The
    One In Use were, which pre_update needs to record the change.
    """

    def populate_obj(self, obj):
        from fbc import db

        with db.session.no_autoflush:
            super().populate_obj(obj)


class SupplyUseModelView(CustomModelView):
    """
    Every change to a supply's stock, newest first. It's a record of what
    happened, so it can't be added to or changed here.
    """

    datamodel = SQLAInterface(SupplyUse)
    list_title = "Supply Use"
    show_title = "Supply Use"
    base_permissions = ["can_list", "can_show"]
    base_order = ("date", "desc")
    formatters_columns = localtime.formatters("date")
    list_columns = [
        "date",
        "fmt_supply",
        "count",
        "fmt_kind",
        "sales_receipt",
        "unit",
    ]
    show_columns = list_columns
    search_columns = ["date", "supply", "count", "kind", "sales_receipt", "unit"]
    label_columns = {
        "count": "Uses",
        "fmt_supply": "Supply",
        "fmt_kind": "For",
        "kind": "For",
        "sales_receipt": "Sales Receipt",
    }
    description_columns = {
        "count": "Below 0 when a Supply List edit or Reconcile Supplies added stock.",
    }


class SupplyModelView(CustomModelView):
    datamodel = SQLAInterface(Supply)
    related_views = [SupplyUseModelView]
    list_columns = [
        "name",
        "fmt_quantity",
        "min_quantity",
        "fmt_used_for",
        "purchase_orders",
    ]
    add_columns = [
        "name",
        "quantity",
        "min_quantity",
        "used_for",
        "record_size",
        "per_units",
        "pack_size",
        "pack_used",
        "cost_per_use",
    ]
    edit_columns = add_columns
    show_columns = [
        "name",
        "fmt_quantity",
        "min_quantity",
        "fmt_used_for",
        "fmt_record_size",
        "per_units",
        "pack_size",
        "pack_used",
        "fmt_cost_per_use",
        "purchase_orders",
    ]
    label_columns = {
        "fmt_quantity": "Quantity",
        "fmt_used_for": "Used For",
        "fmt_record_size": "Record Size",
        "min_quantity": "Reorder At",
        "per_units": "One Per Units",
        "pack_size": "Uses Each",
        "pack_used": "Used From The One In Use",
        "cost_per_use": "Cost Per Use",
        "fmt_cost_per_use": "Cost Per Use",
    }
    description_columns = {
        "min_quantity": "The home page asks you to reorder once Quantity is down"
        " to this. 0 never does.",
        "per_units": "When shipping, use one for every this many Units (rounded"
        " up), like 5 for mailers that hold five records, or 0 for one per"
        " shipment, like a label. Leave blank to fill it in yourself each time,"
        " like packing tape.",
        "pack_size": "How many uses one of Quantity holds, like 220 labels on a"
        " roll. 1 for most supplies.",
        "pack_used": "How many uses are gone from the one in use. When it reaches"
        " Uses Each, Quantity goes down by one.",
        "cost_per_use": "What one use costs, like a roll's price divided by its"
        " 220 labels. Reports use it to cost each sale's supplies. Leave blank"
        " if you don't know.",
    }
    validators_columns = {
        "quantity": [NumberRange(min=0)],
        "min_quantity": [NumberRange(min=0)],
        "per_units": [NumberRange(min=0)],
        "pack_size": [NumberRange(min=1)],
        "pack_used": [NumberRange(min=0), pack_used_fits],
        "cost_per_use": [Optional(), NumberRange(min=0)],
    }
    add_form_extra_fields = SUPPLY_FORM_EXTRA_FIELDS
    edit_form_extra_fields = SUPPLY_FORM_EXTRA_FIELDS

    def _init_forms(self):
        super()._init_forms()
        self.edit_form = type("SupplyEditForm", (NoAutoflushForm, self.edit_form), {})

    def pre_update(self, item):
        # A stock-take or a restock: keep a record of it alongside the uses.
        item.record_adjustment()


def covers_its_expenses(form, field):
    """An order's price can't be less than the Expenses on it."""
    from fbc import db

    # FAB sets _id when editing; a new order has no Expenses yet.
    pk = getattr(form, "_id", None)
    if pk is None or field.data is None:
        return
    total = db.session.get(PurchaseOrder, pk).expense_total()
    if round(field.data, 2) < round(total, 2):
        raise ValidationError(
            "The Expenses on it come to $"
            + f"{total:,.2f}"
            + ", so the price can't be less. Change them first."
        )


class PurchaseOrderSupplyModelView(CustomModelView):
    """
    What a Purchase Order brought of each supply. Adding one adds its
    quantity to the supply's, and changing or deleting it takes back the
    difference, so the Supply List stays right.
    """

    datamodel = SQLAInterface(PurchaseOrderSupply)
    list_title = "Supplies On Purchase Order"
    add_title = "Add Supply To Purchase Order"
    edit_title = "Edit Supply On Purchase Order"
    show_title = "Supply On Purchase Order"
    list_columns = ["supply", "quantity"]
    add_columns = ["purchase_order", "supply", "quantity"]
    edit_columns = ["quantity"]
    show_columns = ["purchase_order", "supply", "quantity"]
    label_columns = {"purchase_order": "Purchase Order", "quantity": "Quantity Added"}
    description_columns = {
        "quantity": "How many came on the order. They're added to the supply's"
        " Quantity, so count packs, like rolls, not the uses in each.",
    }
    validators_columns = {"quantity": [NumberRange(min=1)]}

    def pre_add(self, item):
        item.supply.restock(item.quantity)

    def pre_update(self, item):
        # The supply stays as it was: only how many can be corrected.
        item.supply.restock(item.quantity - item.previous_quantity())

    def pre_delete(self, item):
        item.supply.restock(-item.quantity)


class PurchaseOrderModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseOrder)
    related_views = [PurchaseOrderSupplyModelView, ExpenseModelView]
    list_columns = ["date", "purchase_price", "notes", "link"]
    add_columns = ["date", "price", "notes", "link"]
    edit_columns = add_columns
    show_columns = [
        "date",
        "purchase_price",
        "fmt_supply_cost",
        "notes",
        "link",
        "fmt_supplies",
    ]
    search_columns = ["date", "price", "notes", "supply"]
    label_columns = {
        "purchase_price": "Price",
        "fmt_supply_cost": "Supply Cost",
        "supply": "Supplies",
        "fmt_supplies": "Supplies",
    }
    description_columns = {
        "price": "What the whole order cost. Anything on it that isn't a"
        " supply, like a record crate, is an Expense on it, under Expenses"
        " below once it's saved. Those come out of this price, and the rest"
        " is what the supplies cost. Add the supplies it brought there too,"
        " under Supplies On Purchase Order, to add them to their Quantity.",
    }
    validators_columns = {"price": [covers_its_expenses]}


def _whole(value):
    """A form value as a whole number, or None when blank. Raises ValueError."""
    value = (value or "").strip()
    return int(value) if value else None


def counted_stock(form, supplies):
    """
    {supply: (quantity, pack_used)} for each of `supplies` counted on
    Reconcile Supplies, from its on_hand-<id> and left-<id> fields, and
    {supply id: what to fix} for any that can't be. Supplies left blank
    are in neither.
    """
    counts = {}
    errors = {}
    for supply in supplies:
        key = str(supply.id)
        try:
            on_hand = _whole(form.get("on_hand-" + key))
            left = _whole(form.get("left-" + key))
        except ValueError:
            errors[supply.id] = "Enter whole numbers."
            continue
        if (supply.pack_size or 1) <= 1:
            left = None
        if on_hand is None:
            if left is not None:
                errors[supply.id] = "Enter how many are on hand too."
            continue
        try:
            counts[supply] = supply.stock_from_count(on_hand, left)
        except ValueError as e:
            errors[supply.id] = str(e)
    return counts, errors


def fmt_difference(supply, uses):
    """A count's difference in `uses`, like "−3", or "+80 uses" for a roll."""
    if not uses:
        return "No change"
    text = ("+" if uses > 0 else "−") + str(abs(uses))
    return text + " uses" if (supply.pack_size or 1) > 1 else text


def _seen(supply):
    """What fbc had for `supply` when the differences were shown."""
    return str(supply.quantity) + "/" + str(supply.pack_used)


def stock_changes(counts):
    """What saving `counts` (from counted_stock) would change, for showing."""
    changes = []
    for supply, (quantity, pack_used) in counts.items():
        after = uses_left(quantity, supply.pack_size, pack_used)
        changes.append(
            {
                "supply": supply,
                "before": supply.fmt_quantity(),
                "after": fmt_stock(quantity, supply.pack_size, pack_used),
                "difference": fmt_difference(supply, after - supply.available()),
                "seen": _seen(supply),
            }
        )
    return changes


class ReconcileSuppliesView(BaseView):
    """
    Reconcile Supplies, under Supplies: correct every supply's stock to a
    count of what's on hand, after seeing the differences. Ship This, Add
    Unit and the home page's reorder banner link here when the Supply List
    looks wrong.
    """

    route_base = "/reconcilesupplies"
    default_view = "reconcile"

    @expose("/", methods=["GET", "POST"])
    @has_access
    def reconcile(self):
        """
        GET shows the counts to fill in, with ?supply=<id> highlighted. A
        POST shows the differences; one with action=save saves them, and
        action=change goes back to the counts.
        """
        from fbc import db

        supplies = db.session.query(Supply).order_by(Supply.name).all()
        highlight = request.args.get("supply", type=int)
        values = request.form if request.method == "POST" else None
        errors = {}
        changes = None
        if values is not None and values.get("action") != "change":
            counts, errors = counted_stock(values, supplies)
            if errors:
                flash("Fix the counts marked below.", "danger")
            elif not counts:
                flash("Fill in what's on hand for at least one supply.", "warning")
            else:
                changes = stock_changes(counts)
                if values.get("action") == "save":
                    # Ship This or Add Unit in another tab may have used some
                    # since the differences were shown.
                    moved = [
                        s.name
                        for s in counts
                        if values.get("seen-" + str(s.id)) != _seen(s)
                    ]
                    if moved:
                        flash(
                            ", ".join(moved)
                            + " changed since you checked the differences."
                            " Check them again before saving.",
                            "warning",
                        )
                    else:
                        return self._save(counts, changes)
        return self.render_template(
            "supply/reconcile.html",
            title="Reconcile Supplies",
            supplies=supplies,
            highlight=highlight,
            values=values,
            errors=errors,
            changes=changes,
        )

    def _save(self, counts, changes):
        from fbc import db

        for supply, (quantity, pack_used) in counts.items():
            supply.quantity = quantity
            supply.pack_used = pack_used
            supply.record_adjustment(RECONCILE)
        db.session.commit()
        corrected = [
            c["supply"].name + ": " + c["before"] + " → " + c["after"]
            for c in changes
            if c["before"] != c["after"]
        ]
        if corrected:
            flash("Corrected " + "; ".join(corrected) + ".", "success")
        else:
            flash("The counts all matched; nothing needed correcting.", "info")
        return redirect(url_for("SupplyModelView.list"))
