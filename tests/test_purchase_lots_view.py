import datetime

import pytest
from sqlalchemy import event

import fbc
from fbc import finance
from fbc.index import home_totals
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.sales import SalesReceipt


@pytest.fixture
def client():
    """A test client logged in as an Admin."""
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _add_lot(i, price=100.0):
    """A lot with a two-Unit sale, a one-Unit sale and an unsold Unit."""
    db = fbc.db
    lot = PurchaseLot(name=f"Lot {i}", date=datetime.date(2024, 1, 1), price=price)
    pair = SalesReceipt(date=datetime.date(2024, 2, 1), sold_price=30.0, net_sold=26.0)
    single = SalesReceipt(
        date=datetime.date(2024, 2, 2), sold_price=20.0, net_sold=18.0
    )
    db.session.add_all([lot, pair, single])
    for name, receipt, sold in [
        ("A", pair, True),
        ("B", pair, True),
        ("C", single, True),
        ("D", None, False),
    ]:
        db.session.add(
            Unit(
                name=f"{name}{i}",
                unit_type='12" LP',
                pressing="",
                grading="",
                purchase_lot=lot,
                retail_price=25.0,
                sold=sold,
                sales_receipt=receipt,
            )
        )
    db.session.commit()
    return lot


def _queries_for_list(client):
    statements = []

    def count(*args, **kwargs):
        # Flask-AppBuilder checks permissions for each row's buttons; that's
        # its own cost, not the lots' figures.
        if "ab_permission" not in args[2]:
            statements.append(args[2])

    fbc.db.session.expire_all()
    event.listen(fbc.db.engine, "before_cursor_execute", count)
    try:
        response = client.get("/purchaselotmodelview/list/")
    finally:
        event.remove(fbc.db.engine, "before_cursor_execute", count)
    assert response.status_code == 200
    return len(statements), response.get_data(as_text=True)


def test_list_query_count_does_not_grow_with_lots(client):
    _add_lot(0)
    small, _ = _queries_for_list(client)
    for i in range(1, 8):
        _add_lot(i)
    assert _queries_for_list(client)[0] == small


def test_list_shows_the_figures_the_home_page_does(client):
    _add_lot(0, price=100.0)
    _add_lot(1, price=37.5)
    lots = fbc.db.session.query(PurchaseLot).order_by(PurchaseLot.id).all()
    home = home_totals()["lot_totals"]
    _, html = _queries_for_list(client)

    for lot in lots:
        shared = finance.lot_figures()[lot.id]
        for key in ("gross", "net", "fees", "feepc", "profit", "roi", "perunit"):
            assert shared[key] == home[lot.id][key]
        assert f"${home[lot.id]['profit']:.2f}" in html
    # Lot 0: 50 sold for, 44 net, so a 12% fee and -56 profit; 4 Units, so
    # 100 / 4 / 0.88 to break even.
    first = finance.lot_figures()[lots[0].id]
    assert first["feepc"] == 12.0
    assert first["breakeven"] == pytest.approx(100 / 4 / 0.88)
    assert "$28.41" in html


def test_breakdown_shows_percent_figures(client):
    lot = _add_lot(0)
    html = client.get(f"/purchaselotmodelview/show/{lot.id}").get_data(as_text=True)
    assert "12.00%" in html
    assert "-56.00%" in html
