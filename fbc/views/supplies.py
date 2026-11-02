from fbc.util import CustomModelView
from flask_appbuilder.fieldwidgets import Select2Widget
from flask_appbuilder.models.sqla.interface import SQLAInterface
from wtforms import SelectField
from wtforms.validators import NumberRange, Optional, ValidationError

from fbc.listing import SIZES
from fbc.models.supplies import USED_FOR_LABELS, PurchaseOrder, Supply, SupplyUse

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
        "count": "Below 0 when a Supply List edit added stock.",
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
        "purchase_orders",
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


class PurchaseOrderModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseOrder)
    list_columns = ["date", "purchase_price", "notes", "link"]
    label_columns = {"purchase_price": "Price"}
