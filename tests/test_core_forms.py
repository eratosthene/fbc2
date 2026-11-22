from wtforms import StringField

from fbc.core.forms import CustomForm


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


def _came_from(app, history, endpoint_list="/ebaydraftmodelview/list/"):
    import fbc

    view = fbc.appbuilder.baseviews[
        [v.endpoint for v in fbc.appbuilder.baseviews].index("EbayDraftModelView")
    ]
    with app.test_request_context("/"):
        from flask import session

        if history is not None:
            session["page_history"] = history
        return view.list_url_came_from()


def test_back_to_list_keeps_the_filters_and_page_of_the_list_it_came_from(app):
    history = [
        "http://localhost/ebaydraftmodelview/list/?_flt_3_status=draft&page_EbayDraftModelView=2",
        "http://localhost/ebaydraftmodelview/show/4",
    ]

    assert _came_from(app, history) == (
        "/ebaydraftmodelview/list/?_flt_3_status=draft&page_EbayDraftModelView=2"
    )


def test_back_to_list_looks_past_other_show_pages_and_takes_the_newest_list(app):
    history = [
        "http://localhost/ebaydraftmodelview/list/?old=1",
        "http://localhost/ebaydraftmodelview/list/?new=1",
        "http://localhost/ebaydraftmodelview/show/4",
        "http://localhost/ebaydraftmodelview/show/5",
        "http://localhost/unitmodelview/list/",
    ]

    assert _came_from(app, history) == "/ebaydraftmodelview/list/?new=1"


def test_back_to_list_falls_back_to_the_plain_list(app):
    assert _came_from(app, None) == "/ebaydraftmodelview/list/"
    assert _came_from(app, []) == "/ebaydraftmodelview/list/"
    other = ["http://localhost/unitmodelview/list/?x=1"]
    assert _came_from(app, other) == "/ebaydraftmodelview/list/"


def test_back_to_list_never_leaves_the_site(app):
    history = [
        "//evil.example/ebaydraftmodelview/list/",
        "https://evil.example/elsewhere/",
        "javascript:alert(1)",
        42,
        None,
        "https://evil.example/ebaydraftmodelview/list/?a=1",
    ]

    # An entry for this view's list keeps only its path and query, so it is
    # local whatever host it names; the rest are ignored.
    assert _came_from(app, history) == "/ebaydraftmodelview/list/?a=1"
    assert _came_from(app, history[:-1]) == "/ebaydraftmodelview/list/"
