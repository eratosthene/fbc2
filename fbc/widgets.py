from flask_appbuilder.widgets import ListWidget
from wtforms.fields import DateTimeLocalField as WTFDateTimeLocalField
from wtforms.widgets import DateTimeLocalInput


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


class DiscogsReleaseListWidget(ListWidget):
    template = "widgets/d_r_list.html"


class EbayListingListWidget(ListWidget):
    template = "widgets/e_l_list.html"


class EbayOrderListWidget(ListWidget):
    template = "widgets/e_o_list.html"
