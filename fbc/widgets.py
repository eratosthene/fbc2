from flask_appbuilder.widgets import ListWidget
from markupsafe import Markup
from wtforms.fields import DateTimeLocalField as WTFDateTimeLocalField
from wtforms.fields import Field
from wtforms.utils import unset_value
from wtforms.widgets import DateTimeLocalInput, TextArea


class BS3DateTimeLocalFieldWidget(DateTimeLocalInput):
    """
    Renders a native HTML5 datetime-local input.

    Flask-AppBuilder's own DateTimePickerWidget wires datetime columns up to
    a date-only bootstrap-datepicker (see appbuilder/js/ab.js), so a value
    picked through it never has a time component and fails the field's
    "%Y-%m-%d %H:%M:%S" validation. The browser's native picker captures
    both, so we use that instead.
    """

    def __call__(self, field, **kwargs):
        kwargs["class"] = "form-control"
        return super().__call__(field, **kwargs)


class DateTimeLocalField(WTFDateTimeLocalField):
    """DateTimeLocalField that treats a blank submission as no value."""

    widget = BS3DateTimeLocalFieldWidget()

    def process_formdata(self, valuelist):
        if valuelist and not valuelist[0].strip():
            valuelist = []
        super().process_formdata(valuelist)


class TallTextAreaWidget(TextArea):
    """
    A textarea with room for many lines. Flask-AppBuilder's own
    BS3TextAreaFieldWidget always renders 3 rows.
    """

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, field, **kwargs):
        kwargs["class"] = "form-control"
        kwargs["rows"] = self.rows
        return super().__call__(field, **kwargs)


class SuppliesUsedWidget:
    """A table of the field's supplies, with a count to enter for each."""

    def __call__(self, field, **kwargs):
        if not field.supplies:
            return Markup("<p>None of the supplies are used for this.</p>")
        rows = Markup("")
        for supply in field.supplies:
            name = field.name + "-" + str(supply.id)
            rows += Markup(
                '<tr><td><label for="{0}">{1}</label></td><td>{2}</td>'
                '<td><input type="number" min="0" step="1" id="{0}" name="{0}"'
                ' value="{3}" class="form-control input-sm" style="width: 6em">'
                "</td></tr>"
            ).format(
                name, supply.name, supply.fmt_quantity(), field.raw.get(supply.id, 0)
            )
        return Markup(
            '<table class="table table-bordered table-condensed" style="width: auto">'
            "<tr><th>Supply</th><th>In stock</th><th>Used</th></tr>{0}</table>"
        ).format(rows)


class SuppliesUsedField(Field):
    """
    How many of each supply that's used_for `used_for` were used, entered as
    a <name>-<supply id> number for each. Its data is {supply: count},
    leaving out 0.
    """

    widget = SuppliesUsedWidget()

    def __init__(self, label=None, validators=None, used_for=None, **kwargs):
        super().__init__(label, validators, **kwargs)
        self.used_for = used_for

    def process(self, formdata, data=unset_value, extra_filters=None):
        from fbc.models.supplies import Supply
        from fbc.shipping import supply_counts

        self.process_errors = []
        self.supplies = Supply.used_for_list(self.used_for)
        self.raw = {}
        self.data = {}
        if formdata is None:
            return
        for supply in self.supplies:
            value = formdata.get(self.name + "-" + str(supply.id))
            if value is not None:
                self.raw[supply.id] = value
        try:
            self.data = supply_counts(formdata, self.supplies, self.name + "-")
        except ValueError as e:
            self.process_errors.append(str(e))


class DiscogsReleaseListWidget(ListWidget):
    template = "widgets/d_r_list.html"


class EbayListingListWidget(ListWidget):
    template = "widgets/e_l_list.html"


class EbayOrderListWidget(ListWidget):
    template = "widgets/e_o_list.html"


class EbayDraftListWidget(ListWidget):
    template = "widgets/e_d_list.html"
