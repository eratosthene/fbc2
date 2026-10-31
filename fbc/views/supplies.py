from fbc.util import CustomModelView
from flask_appbuilder.fieldwidgets import Select2Widget
from flask_appbuilder.models.sqla.interface import SQLAInterface
from wtforms import SelectField
from wtforms.validators import NumberRange

from fbc.listing import SIZES
from fbc.models.supplies import ADDING_UNIT, SHIPPING, PurchaseOrder, Supply

USED_FOR_CHOICES = [
    ("", "Only by hand"),
    (SHIPPING, "Shipping an order"),
    (ADDING_UNIT, "Adding a Unit"),
]

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


class SupplyModelView(CustomModelView):
    datamodel = SQLAInterface(Supply)
    list_columns = [
        "name",
        "fmt_quantity",
        "min_quantity",
        "used_for",
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
        "purchase_orders",
    ]
    edit_columns = add_columns
    show_columns = add_columns
    label_columns = {
        "fmt_quantity": "Quantity",
        "min_quantity": "Reorder At",
        "per_units": "One Per Units",
        "pack_size": "Uses Each",
        "pack_used": "Used From The One In Use",
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
    }
    validators_columns = {
        "quantity": [NumberRange(min=0)],
        "min_quantity": [NumberRange(min=0)],
        "per_units": [NumberRange(min=0)],
        "pack_size": [NumberRange(min=1)],
        "pack_used": [NumberRange(min=0)],
    }
    add_form_extra_fields = SUPPLY_FORM_EXTRA_FIELDS
    edit_form_extra_fields = SUPPLY_FORM_EXTRA_FIELDS


class PurchaseOrderModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseOrder)
    list_columns = ["date", "purchase_price", "notes", "link"]
    label_columns = {"purchase_price": "Price"}
