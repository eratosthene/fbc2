"""
Per-user home page layouts (#214, step 2): storage, the endpoints and their
permissions, and Customize mode.
"""

import dataclasses

import fbc
import pytest
from flask import g
from auth import signed_in_client

from fbc import db
from fbc.models.home import HomeLayout
from fbc.sales import cards, index, layout

KEYS = [c.key for c in cards.CARDS]


def _user(name):
    sm = fbc.appbuilder.sm
    sm.create_db()
    sm.add_user(name, "N", name, name + "@example.com", sm.find_role("Admin"), "pw")
    return sm.find_user(name).id


def _admin_id():
    signed_in_client()
    return fbc.appbuilder.sm.find_user("admin").id


def _forget_who_is_signed_in():
    # The tests share one application context, so g keeps the last user
    # Flask-Login loaded across test clients; a real request starts clean.
    g.pop("_login_user", None)
    g.pop("user", None)


def _client_for(name):
    _forget_who_is_signed_in()
    client = fbc.app.test_client()
    client.post("/login/", data={"username": name, "password": "pw"})
    _forget_who_is_signed_in()
    return client


def test_no_saved_layout_is_the_default_order(app):
    uid = _admin_id()
    arranged = layout.for_user(uid)
    assert [c.key for c in arranged.visible] == KEYS
    assert arranged.hidden_cards == []
    assert db.session.query(HomeLayout).count() == 0


def test_two_users_do_not_see_each_others_layouts(app):
    one, two = _admin_id(), _user("second")
    layout.reorder(one, ["money", "stock"])
    layout.set_hidden(one, "ytd", True)
    assert [c.key for c in layout.for_user(two).visible] == KEYS
    assert db.session.query(HomeLayout).filter_by(user_id=two).count() == 0
    assert "ytd" not in [c.key for c in layout.for_user(one).visible]


def test_reorder_puts_the_cards_in_the_places_they_had(app):
    uid = _admin_id()
    layout.reorder(uid, ["money", "stock"])
    order = list(layout.for_user(uid).order)
    assert order[KEYS.index("stock")] == "money"
    assert order[KEYS.index("money")] == "stock"
    assert sorted(order) == sorted(KEYS)


def test_a_hidden_card_keeps_its_place_and_comes_back_there(app):
    uid = _admin_id()
    layout.set_hidden(uid, "ytd", True)
    arranged = layout.for_user(uid)
    assert [c.key for c in arranged.hidden_cards] == ["ytd"]
    assert "ytd" not in [c.key for c in arranged.visible]
    layout.set_hidden(uid, "ytd", False)
    assert [c.key for c in layout.for_user(uid).visible] == KEYS


def test_a_card_added_later_shows_after_the_one_before_it_by_default(app, monkeypatch):
    uid = _admin_id()
    layout.reorder(uid, ["money", "stock"])
    saved = layout.for_user(uid).order
    new = dataclasses.replace(cards.CARDS[0], key="newcomer")
    # Added to the registry after "ytd" in the default order.
    registry = [*cards.CARDS[:3], new, *cards.CARDS[3:]]
    monkeypatch.setattr(cards, "CARDS", registry)
    monkeypatch.setattr(cards, "BY_KEY", {c.key: c for c in registry})
    arranged = layout.for_user(uid)
    order = list(arranged.order)
    assert order.index("newcomer") == order.index("ytd") + 1
    assert "newcomer" in [c.key for c in arranged.visible]
    assert [k for k in order if k != "newcomer"] == list(saved)


def test_a_new_first_card_goes_first(app, monkeypatch):
    uid = _admin_id()
    layout.reorder(uid, ["money", "stock"])
    new = dataclasses.replace(cards.CARDS[0], key="newcomer")
    registry = [new, *cards.CARDS]
    monkeypatch.setattr(cards, "CARDS", registry)
    monkeypatch.setattr(cards, "BY_KEY", {c.key: c for c in registry})
    assert layout.for_user(uid).order[0] == "newcomer"


def test_a_key_that_is_no_longer_a_card_is_ignored(app):
    uid = _admin_id()
    db.session.add(
        HomeLayout(
            user_id=uid,
            layout={"order": ["gone", "money", "stock"], "hidden": ["gone2", "ytd"]},
        )
    )
    db.session.commit()
    arranged = layout.for_user(uid)
    assert "gone" not in arranged.order
    assert sorted(arranged.order) == sorted(KEYS)
    assert arranged.hidden == {"ytd"}


@pytest.mark.parametrize("saved", [None, "text", ["x"], {"order": 3, "hidden": "no"}])
def test_an_unreadable_layout_is_the_default(saved):
    assert layout.resolve(saved) == layout.default()


def test_reset_deletes_the_saved_layout(app):
    one, two = _admin_id(), _user("second")
    layout.set_hidden(one, "ytd", True)
    layout.set_hidden(two, "ytd", True)
    layout.reset(one)
    assert db.session.query(HomeLayout).filter_by(user_id=one).count() == 0
    assert [c.key for c in layout.for_user(one).visible] == KEYS
    assert db.session.query(HomeLayout).filter_by(user_id=two).count() == 1


def test_hidden_cards_figures_are_not_worked_out(app, monkeypatch):
    uid = _admin_id()
    layout.set_hidden(uid, "money", True)
    calls = []
    watched = [
        dataclasses.replace(
            c, figures=lambda s, c=c, f=c.figures: calls.append(c.key) or f(s)
        )
        for c in cards.CARDS
    ]
    monkeypatch.setattr(cards, "CARDS", watched)
    monkeypatch.setattr(cards, "BY_KEY", {c.key: c for c in watched})
    context = index.home_context(user_id=uid)
    assert "money" not in calls
    assert [s.card.key for s in context["cards"]] == [k for k in KEYS if k != "money"]
    assert [c.key for c in context["hidden_cards"]] == ["money"]


def test_home_totals_ignores_the_layout(app):
    uid = _admin_id()
    layout.set_hidden(uid, "money", True)
    assert "supply_total" in index.home_totals()


# The page and the endpoints.


def test_the_page_follows_the_users_layout(app):
    uid = _admin_id()
    layout.reorder(uid, ["money", "stock"])
    layout.set_hidden(uid, "ytd", True)
    with signed_in_client(app) as client:
        page = client.get("/").get_data(as_text=True)
    assert 'id="card-money"' in page and 'id="card-ytd"' not in page
    assert page.index('id="card-money"') < page.index('id="card-stock"')


def test_a_second_user_sees_the_default_page(app):
    uid = _admin_id()
    layout.set_hidden(uid, "ytd", True)
    _user("second")
    page = _client_for("second").get("/").get_data(as_text=True)
    assert 'id="card-ytd"' in page


def test_edit_mode_markup_is_only_in_edit_mode(app):
    layout.set_hidden(_admin_id(), "ytd", True)
    with signed_in_client(app) as client:
        normal = client.get("/").get_data(as_text=True)
        editing = client.get("/?customize=1").get_data(as_text=True)
    for marker in ("home-handle", "home-hide", "home-show", "home-reset", "Sortable"):
        assert marker not in normal
        assert marker in editing
    assert "Customize" in normal
    assert (
        "Hidden cards" in editing and "Year to date" in editing.split("Hidden cards")[1]
    )
    assert "Done" in editing


def test_the_page_loads_nothing_from_another_host(app):
    with signed_in_client(app) as client:
        page = client.get("/?customize=1").get_data(as_text=True)
    scripts = [part.split('"')[0] for part in page.split("<script src=")[1:]]
    assert all(
        src.startswith("/static/") for src in (x.strip('"') for x in scripts) if src
    )
    assert "/static/js/Sortable.min.js" in page


def test_the_endpoints_save_the_signed_in_users_own_row_only(app):
    other = _user("second")
    layout.set_hidden(other, "money", True)
    with signed_in_client(app) as client:
        assert client.post("/layout/hide/stock").status_code == 200
        assert (
            client.post(
                "/layout/order", json={"order": ["money", "listings"]}
            ).status_code
            == 200
        )
        assert client.post("/layout/show/stock").status_code == 200
        uid = fbc.appbuilder.sm.find_user("admin").id
        assert layout.for_user(uid).order.index("money") < 2
        assert client.post("/layout/reset").status_code == 200
    assert db.session.query(HomeLayout).filter_by(user_id=uid).count() == 0
    row = db.session.query(HomeLayout).filter_by(user_id=other).one()
    assert row.layout["hidden"] == ["money"]


@pytest.mark.parametrize(
    "url, body",
    [
        ("/layout/hide/nope", None),
        ("/layout/show/nope", None),
        ("/layout/order", {"order": ["stock", "nope"]}),
        ("/layout/order", {"order": ["stock", "stock"]}),
        ("/layout/order", {"order": "stock"}),
        ("/layout/order", {"other": []}),
    ],
)
def test_a_bad_card_key_is_refused_and_changes_nothing(app, url, body):
    signed_in_client(app)
    with signed_in_client(app) as client:
        response = client.post(url, json=body)
    assert response.status_code == 400
    assert db.session.query(HomeLayout).count() == 0


def test_a_hidden_card_cannot_be_reordered(app):
    uid = _admin_id()
    layout.set_hidden(uid, "stock", True)
    with signed_in_client(app) as client:
        assert (
            client.post("/layout/order", json={"order": ["money", "stock"]}).status_code
            == 400
        )


def test_the_endpoints_are_post_only(app):
    with signed_in_client(app) as client:
        for url in ("/layout/hide/stock", "/layout/show/stock", "/layout/reset"):
            assert client.get(url).status_code == 405


def test_the_endpoints_need_signing_in_and_their_own_permission(app):
    _admin_id()
    # Signed out: nothing changes.
    _forget_who_is_signed_in()
    anon = fbc.app.test_client()
    for url in ("/layout/hide/stock", "/layout/reset", "/layout/order"):
        assert anon.post(url, json={"order": []}).status_code in (302, 401, 403)
    # A role that can open the home page but hasn't been given the new permissions.
    sm = fbc.appbuilder.sm
    role = sm.add_role("Home viewer")
    sm.add_permission_role(
        role, sm.find_permission_view_menu("can_index", "MyIndexView")
    )
    sm.add_user("viewer", "V", "Viewer", "v@example.com", role, "pw")
    client = _client_for("viewer")
    assert client.get("/").status_code == 200
    for url in ("/layout/hide/stock", "/layout/show/stock", "/layout/reset"):
        _forget_who_is_signed_in()
        assert client.post(url).status_code in (302, 401, 403)
    assert db.session.query(HomeLayout).count() == 0


def test_admin_has_the_new_permissions(app):
    signed_in_client(app)
    sm = fbc.appbuilder.sm
    admin = sm.find_role("Admin")
    have = {
        pv.permission.name
        for pv in admin.permissions
        if pv.view_menu.name == "MyIndexView"
    }
    assert {
        "can_layoutorder",
        "can_layouthide",
        "can_layoutshow",
        "can_layoutreset",
    } <= have
