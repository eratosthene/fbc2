from wtforms import StringField

from fbc.util import CustomForm


class _NameForm(CustomForm):
    name = StringField("Name")


def test_refresh_reads_formdata_from_query_args_on_get(app):
    with app.test_request_context("/?name=Test+Unit"):
        form = _NameForm.refresh()
        assert form.name.data == "Test Unit"


def test_refresh_ignores_query_args_on_post(app):
    with app.test_request_context("/?name=Should+Not+Be+Used", method="POST", data={}):
        form = _NameForm.refresh()
        assert form.name.data != "Should Not Be Used"
