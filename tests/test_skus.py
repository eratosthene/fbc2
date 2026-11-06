import datetime

import pytest

from fbc import db, devmode, listing, skus
from fbc.models.ebay import EbayListing
from fbc.models.inventory import PurchaseLot, Unit


@pytest.fixture
def lot():
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    db.session.add(lot)
    db.session.commit()
    return lot


def make_unit(lot, item_id=None, sold=False):
    if item_id and not db.session.get(EbayListing, item_id):
        db.session.add(EbayListing(item_id=item_id, title="t"))
    unit = Unit(
        name="U",
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        sold=sold,
        ebay_listing_id=item_id,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


class FakeEbay:
    def __init__(self, items, failing=()):
        self.items = items
        self.failing = failing
        self.revised = []
        self.pages = []

    def call(self, verb, data):
        if verb == "GetMyeBaySelling":
            page = data["ActiveList"]["Pagination"]["PageNumber"]
            self.pages.append(page)
            chunk = self.items[(page - 1) * 2 : page * 2]
            resp = {
                "ActiveList": {
                    "PaginationResult": {
                        "TotalNumberOfPages": str((len(self.items) + 1) // 2)
                    },
                    "ItemArray": {"Item": chunk},
                }
            }
            return resp, {"errors": [], "warnings": [], "fees": []}
        assert verb == "ReviseFixedPriceItem"
        assert set(data) == {"Item"}
        assert set(data["Item"]) == {"ItemID", "SKU"}
        self.revised.append((data["Item"]["ItemID"], data["Item"]["SKU"]))
        if data["Item"]["ItemID"] in self.failing:
            return None, {"errors": ["Nope."], "warnings": [], "fees": []}
        return {"Ack": "Success"}, {"errors": [], "warnings": [], "fees": []}


def item(item_id, sku=None):
    d = {"ItemID": item_id, "Title": "Title " + item_id}
    if sku:
        d["SKU"] = sku
    return d


@pytest.fixture
def ebay(monkeypatch):
    def install(items, failing=()):
        fake = FakeEbay(items, failing)
        monkeypatch.setattr(listing, "_call", fake.call)
        return fake

    return install


def run(**kwargs):
    kwargs.setdefault("pause", 0)
    return skus.set_skus(**kwargs)


def test_sets_the_sku_on_a_listing_with_one_unsold_unit(lot, ebay):
    unit = make_unit(lot, "1")
    fake = ebay([item("1")])
    result = run(apply=True)
    assert fake.revised == [("1", "fbc-" + str(unit.id))]
    assert result["counts"][skus.SET] == 1
    assert result["skipped"] == []


def test_a_dry_run_changes_nothing(lot, ebay):
    make_unit(lot, "1")
    fake = ebay([item("1")])
    result = run()
    assert fake.revised == []
    assert result["counts"][skus.SET] == 1


def test_a_listing_that_is_already_right_is_left_alone(lot, ebay):
    unit = make_unit(lot, "1")
    fake = ebay([item("1", "fbc-" + str(unit.id))])
    result = run(apply=True)
    assert fake.revised == []
    assert result["counts"][skus.RIGHT] == 1


def test_a_typed_sku_is_kept_unless_overwrite(lot, ebay):
    unit = make_unit(lot, "1")
    fake = ebay([item("1", "MY-LABEL")])
    result = run(apply=True)
    assert fake.revised == []
    assert result["skipped"][0][:3] == ("1", "Title 1", skus.TYPED)
    run(apply=True, overwrite=True)
    assert fake.revised == [("1", "fbc-" + str(unit.id))]


def test_an_fbc_sku_naming_another_unit_is_a_conflict_even_with_overwrite(lot, ebay):
    make_unit(lot, "1")
    fake = ebay([item("1", "fbc-99999")])
    result = run(apply=True, overwrite=True)
    assert fake.revised == []
    assert result["skipped"][0][2] == skus.CONFLICT


def test_no_unit_several_units_and_only_sold_units_are_skipped(lot, ebay):
    make_unit(lot, "2")
    make_unit(lot, "2")
    make_unit(lot, "3", sold=True)
    fake = ebay([item("1"), item("2"), item("3")])
    result = run(apply=True)
    assert fake.revised == []
    assert [s[2] for s in result["skipped"]] == [
        skus.NO_UNIT,
        skus.SEVERAL,
        skus.ONLY_SOLD,
    ]


def test_one_unsold_unit_among_sold_ones_still_gets_its_sku(lot, ebay):
    make_unit(lot, "1", sold=True)
    unit = make_unit(lot, "1")
    fake = ebay([item("1")])
    run(apply=True)
    assert fake.revised == [("1", "fbc-" + str(unit.id))]


def test_item_and_limit(lot, ebay):
    for n in "1234":
        make_unit(lot, n)
    fake = ebay([item(n) for n in "1234"])
    run(apply=True, item="3")
    assert [r[0] for r in fake.revised] == ["3"]
    fake.revised.clear()
    run(apply=True, limit=2)
    assert [r[0] for r in fake.revised] == ["1", "2"]
    assert fake.pages[-2:] == [1, 2]


def test_an_item_that_isnt_active_is_an_error(lot, ebay):
    ebay([item("1")])
    with pytest.raises(skus.EbayError):
        run(item="9")


def test_a_refused_revise_is_reported_and_the_run_goes_on(lot, ebay):
    make_unit(lot, "1")
    unit2 = make_unit(lot, "2")
    fake = ebay([item("1"), item("2")], failing=("1",))
    result = run(apply=True)
    assert ("2", "fbc-" + str(unit2.id)) in fake.revised
    assert result["counts"][skus.FAILED] == 1
    assert result["counts"][skus.SET] == 1
    assert result["skipped"][0][2] == skus.FAILED


def test_the_csv_lists_every_skipped_listing(lot):
    text = skus.skipped_csv([("1", "A, B", skus.NO_UNIT, "")])
    assert text.splitlines() == [
        "item_id,title,reason,detail",
        '1,"A, B",no Unit linked,',
    ]


def test_the_command_reports_and_writes_the_csv(lot, ebay, app, tmp_path):
    make_unit(lot, "1")
    ebay([item("1"), item("2")])
    path = tmp_path / "out.csv"
    result = app.test_cli_runner().invoke(args=["set-listing-skus", "--csv", str(path)])
    assert result.exit_code == 0, result.output
    assert "Would set 1." in result.output
    assert "dry run" in result.output
    assert "no Unit linked" in path.read_text()


def test_dev_mode_refuses_and_never_reaches_ebay(lot, app, monkeypatch, tmp_path):
    import requests

    monkeypatch.setitem(app.config, "DEV_MODE", True)

    def no_requests(*a, **k):
        raise AssertionError("Dev mode let a request out.")

    monkeypatch.setattr(requests.Session, "send", no_requests)
    result = app.test_cli_runner().invoke(
        args=["set-listing-skus", "--apply", "--csv", str(tmp_path / "o.csv")]
    )
    assert result.exit_code != 0
    assert "Dev Mode is turned on" in result.output
    assert devmode.on()
