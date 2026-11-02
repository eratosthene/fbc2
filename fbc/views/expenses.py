from fbc.util import CustomModelView
from flask import current_app
from flask_appbuilder.fieldwidgets import BS3TextFieldWidget, Select2Widget
from flask_appbuilder.models.sqla.interface import SQLAInterface
from wtforms import FloatField, SelectField
from wtforms.validators import NumberRange, StopValidation, ValidationError

from fbc.models.expenses import Expense, categories


def amount_or_miles(form, field):
    """
    An Expense needs an Amount, unless Miles works it out. It does
    Optional's job too: Optional clears the errors raised before it.
    """
    if field.raw_data and field.raw_data[0].strip():
        return
    # Blank isn't "Not a valid float value".
    field.errors[:] = []
    if form.miles.data is None:
        raise StopValidation("Enter an Amount, or Miles for mileage.")
    raise StopValidation()


def rate_for_miles(form, field):
    """Miles need a Rate Per Mile, typed in or from config.py."""
    if field.data is None or form.mileage_rate.data is not None:
        return
    if current_app.config.get("MILEAGE_RATE") is None:
        raise ValidationError(
            "Enter a Rate Per Mile too: MILEAGE_RATE isn't set in config.py."
        )


EXPENSE_FORM_EXTRA_FIELDS = {
    # The choices are read when the form is shown, so a change to
    # EXPENSE_CATEGORIES in config.py needs no code change.
    "category": SelectField(
        "Category",
        choices=lambda: list(categories().items()),
        widget=Select2Widget(),
        description="Packing supplies go on a Purchase Order instead, so the"
        " Supply List stays right.",
    ),
    # amount can't be NULL, but it can be left blank when Miles works it out,
    # which Flask-AppBuilder's own field wouldn't allow.
    "amount": FloatField(
        "Amount",
        validators=[amount_or_miles],
        widget=BS3TextFieldWidget(),
        description="Worked out from Miles instead, when they're filled in.",
    ),
}


def fmt_money(amount):
    return "$" + f"{amount:,.2f}"


class FitsPurchaseOrderForm:
    """
    Won't let the Expenses on a Purchase Order come to more than its price.
    A field validator can't do it: Optional on Purchase Order would clear
    its error, and the field is hidden when adding from the order's page.
    """

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        order = self.purchase_order.data
        if order is None:
            return True
        amount = self.amount.data
        if self.miles.data is not None:
            rate = self.mileage_rate.data
            if rate is None:
                rate = current_app.config.get("MILEAGE_RATE")
            amount = round(self.miles.data * rate, 2)
        # Editing: FAB sets _id to the expense's id. It's already on the order.
        editing = getattr(self, "_id", None)
        others = sum(e.amount for e in order.expenses if e.id != editing)
        if round(others + amount, 2) > round(order.price, 2):
            self.amount.errors.append(
                "The Expenses on that Purchase Order would come to "
                + fmt_money(others + amount)
                + ", more than its price of "
                + fmt_money(order.price)
                + "."
            )
            return False
        return True


class ExpenseModelView(CustomModelView):
    datamodel = SQLAInterface(Expense)
    base_order = ("date", "desc")
    list_columns = ["date", "fmt_category", "fmt_amount", "vendor", "note", "link"]
    add_columns = [
        "date",
        "category",
        "amount",
        "miles",
        "mileage_rate",
        "vendor",
        "note",
        "link",
        "purchase_order",
    ]
    edit_columns = add_columns
    show_columns = [
        "date",
        "fmt_category",
        "fmt_amount",
        "miles",
        "fmt_mileage_rate",
        "vendor",
        "note",
        "link",
        "purchase_order",
    ]
    search_columns = [
        "date",
        "category",
        "amount",
        "vendor",
        "note",
        "purchase_order",
    ]
    label_columns = {
        "fmt_category": "Category",
        "fmt_amount": "Amount",
        "mileage_rate": "Rate Per Mile",
        "fmt_mileage_rate": "Rate Per Mile",
        "purchase_order": "Purchase Order",
    }
    description_columns = {
        "miles": "For mileage, like driving to a record fair. Amount is Miles"
        " × Rate Per Mile.",
        "mileage_rate": "Leave blank for MILEAGE_RATE in config.py. It's kept"
        " with the expense, since the IRS rate changes every year.",
        "link": "A receipt or invoice.",
        "purchase_order": "If it was bought on a Purchase Order with other"
        " things, like supplies. Amount is then its part of the order's price,"
        " and the rest is what the supplies cost.",
    }
    validators_columns = {
        "miles": [NumberRange(min=0), rate_for_miles],
        "mileage_rate": [NumberRange(min=0)],
    }
    add_form_extra_fields = EXPENSE_FORM_EXTRA_FIELDS
    edit_form_extra_fields = EXPENSE_FORM_EXTRA_FIELDS

    def _init_forms(self):
        super()._init_forms()
        self.add_form = type(
            "ExpenseAddForm", (FitsPurchaseOrderForm, self.add_form), {}
        )
        self.edit_form = type(
            "ExpenseEditForm", (FitsPurchaseOrderForm, self.edit_form), {}
        )

    def pre_add(self, item):
        item.work_out_mileage(current_app.config.get("MILEAGE_RATE"))

    def pre_update(self, item):
        item.work_out_mileage(current_app.config.get("MILEAGE_RATE"))
