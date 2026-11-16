import pytest
from click.testing import CliRunner

import fbc
from fbc import db
from fbc.core import devmode
from fbc.ebay import api, picture_import, pictures
from fbc.models.ebay import EbayListing
from fbc.models.inventory import StorageBox, Unit
from fbc.models.pictures import UnitPicture
from fbc.pictures import store
from fake_pictures import jpeg


def _listing(item_id="111", units=1, **kwargs):
    listing = EbayListing(
        item_id=item_id,
        title="Listing " + item_id,
        price=5.0,
        url="https://www.ebay.com/itm/" + item_id,
        **kwargs,
    )
    db.session.add(listing)
    made = []
    for _ in range(units):
        unit = Unit(name="A record", ebay_listing_id=item_id)
        db.session.add(unit)
        made.append(unit)
    db.session.commit()
    return listing, made


@pytest.fixture
def ebay(monkeypatch):
    """Fake eBay: `listings` maps an item id to its picture addresses."""
    state = type("Ebay", (), {})()
    state.listings = {}
    state.fetched = []
    state.broken = {}

    def item_pictures(item_id):
        if item_id not in state.listings:
            return [], ["eBay couldn't find it."]
        return state.listings[item_id], []

    def fetch(url):
        state.fetched.append(url)
        if url in state.broken:
            return state.broken[url]
        return jpeg(60, 40), None

    monkeypatch.setattr(api, "item_pictures", item_pictures)
    monkeypatch.setattr(pictures, "fetch_picture", fetch)
    return state


def _urls(*names):
    return ["https://i.ebayimg.com/00/s/" + n for n in names]


def test_a_listings_pictures_are_copied_to_its_unit_in_order(app, ebay):
    _, (unit,) = _listing()
    ebay.listings["111"] = _urls("front.jpg?set=1", "back.jpg")

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    stored = store.for_unit(unit)
    assert [p.upload_name for p in stored] == ["front.jpg", "back.jpg"]
    assert [p.position for p in stored] == [1, 2]
    assert counts["listings"] == 1 and counts["units"] == 1
    assert counts["pictures"] == 2


def test_the_ebay_address_is_not_saved_so_publishing_uploads_the_stored_copy(app, ebay):
    _, (unit,) = _listing()
    ebay.listings["111"] = _urls("a.jpg")

    picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert store.for_unit(unit)[0].ebay_url is None


def test_a_dry_run_reads_the_listing_but_copies_nothing(app, ebay):
    _, (unit,) = _listing()
    ebay.listings["111"] = _urls("a.jpg", "b.jpg")
    lines = []

    counts = picture_import.import_pictures(echo=lines.append)

    assert store.for_unit(unit) == []
    assert ebay.fetched == []
    assert counts["pictures"] == 2
    assert "would copy 2 pictures to 1 Unit" in lines[0]


def test_running_again_does_nothing_more(app, ebay):
    _listing()
    ebay.listings["111"] = _urls("a.jpg")
    picture_import.import_pictures(apply=True, echo=lambda *_: None)
    ebay.fetched.clear()

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert ebay.fetched == []
    assert counts["listings"] == 0 and counts["have"] == 1


def test_every_unit_of_a_lot_gets_the_pictures_from_one_fetch(app, ebay):
    _, units = _listing(units=2)
    ebay.listings["111"] = _urls("a.jpg")

    picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert [len(store.for_unit(u)) for u in units] == [1, 1]
    assert ebay.fetched == _urls("a.jpg")
    assert store.path(store.for_unit(units[0])[0]) != store.path(
        store.for_unit(units[1])[0]
    )


def test_a_unit_that_has_pictures_is_left_alone_but_its_lot_mate_gets_them(app, ebay):
    _, (kept, bare) = _listing(units=2)
    store.add(kept, jpeg(), "mine.jpg")
    db.session.commit()
    ebay.listings["111"] = _urls("a.jpg")

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert [p.upload_name for p in store.for_unit(kept)] == ["mine.jpg"]
    assert len(store.for_unit(bare)) == 1
    assert counts["have"] == 1


def test_ended_listings_are_included(app, ebay):
    _, (unit,) = _listing(ended=True)
    ebay.listings["111"] = _urls("a.jpg")

    picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert len(store.for_unit(unit)) == 1


def test_sold_units_are_left_out_unless_asked_for(app, ebay):
    _, (unit,) = _listing()
    unit.sold = True
    db.session.commit()
    ebay.listings["111"] = _urls("a.jpg")

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)
    assert store.for_unit(unit) == [] and counts["sold"] == 1
    assert ebay.fetched == []

    picture_import.import_pictures(apply=True, include_sold=True, echo=lambda *_: None)
    assert len(store.for_unit(unit)) == 1


def test_a_listing_eBay_cant_read_is_reported_and_the_rest_go_on(app, ebay):
    _listing("111")
    _, (second,) = _listing("222")
    ebay.listings["222"] = _urls("b.jpg")
    lines = []

    counts = picture_import.import_pictures(apply=True, echo=lines.append)

    assert counts["unreadable"] == 1
    assert (
        "111 (Listing 111): couldn't read it from eBay: eBay couldn't find it." in lines
    )
    assert len(store.for_unit(second)) == 1


def test_a_listing_with_no_pictures_is_reported(app, ebay):
    _listing()
    ebay.listings["111"] = []

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert counts["no_pictures"] == 1


def test_one_bad_picture_leaves_the_whole_listing_alone_for_the_next_run(app, ebay):
    _, (unit,) = _listing()
    ebay.listings["111"] = _urls("a.jpg", "b.jpg")
    ebay.broken[_urls("b.jpg")[0]] = (None, "eBay's picture host answered 404.")
    lines = []

    counts = picture_import.import_pictures(apply=True, echo=lines.append)

    assert store.for_unit(unit) == []
    assert counts["unreadable"] == 1
    assert "left alone" in lines[0] and "404" in lines[0]

    # Once it can be fetched, the next run does it all.
    ebay.broken.clear()
    picture_import.import_pictures(apply=True, echo=lambda *_: None)
    assert len(store.for_unit(unit)) == 2


def test_a_file_that_isnt_a_picture_leaves_the_listing_alone(app, ebay):
    _, (unit,) = _listing()
    ebay.listings["111"] = _urls("a.jpg")
    ebay.broken[_urls("a.jpg")[0]] = (b"<html>nope</html>", None)

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert store.for_unit(unit) == []
    assert counts["unreadable"] == 1


def test_mystery_boxes_and_units_without_listings_are_skipped(app, ebay):
    db.session.add(Unit(name="No listing"))
    storage = StorageBox(name="Mystery")
    db.session.add(storage)
    db.session.commit()
    _, (unit,) = _listing(pick_from_box_id=storage.id, pick_count=2)
    ebay.listings["111"] = _urls("a.jpg")

    counts = picture_import.import_pictures(apply=True, echo=lambda *_: None)

    assert counts["mystery"] == 1
    assert store.for_unit(unit) == []
    assert ebay.fetched == []


def test_limit_and_item_narrow_what_is_read(app, ebay):
    for item_id in ("111", "222", "333"):
        _listing(item_id)
        ebay.listings[item_id] = _urls(item_id + ".jpg")

    counts = picture_import.import_pictures(apply=True, limit=1, echo=lambda *_: None)
    assert counts["listings"] == 1
    assert ebay.fetched == _urls("111.jpg")

    counts = picture_import.import_pictures(
        apply=True, item="333", echo=lambda *_: None
    )
    assert counts["listings"] == 1
    with_pictures = {p.unit_id for p in UnitPicture.query.all()}
    assert with_pictures == {
        Unit.query.filter_by(ebay_listing_id=i).one().id for i in ("111", "333")
    }


def test_dev_mode_never_reaches_ebay(app, ebay, monkeypatch):
    monkeypatch.setitem(app.config, "DEV_MODE", True)
    _listing()
    ebay.listings["111"] = _urls("a.jpg")
    monkeypatch.setattr(
        api, "item_pictures", lambda *a: pytest.fail("reached eBay in dev mode")
    )
    lines = []

    picture_import.import_pictures(apply=True, echo=lines.append)

    assert lines == [devmode.message("Importing pictures from eBay")]
    assert ebay.fetched == []


def test_without_a_pictures_folder_it_says_so(app, ebay, monkeypatch):
    monkeypatch.setitem(app.config, "PICTURES_DIR", None)
    _listing()
    lines = []

    picture_import.import_pictures(apply=True, echo=lines.append)

    assert "PICTURES_DIR isn't set" in lines[0]
    assert ebay.fetched == []


def test_the_command_prints_a_summary_and_says_when_it_was_a_dry_run(app, ebay):
    _listing()
    ebay.listings["111"] = _urls("a.jpg")
    runner = CliRunner()

    dry = runner.invoke(fbc.app.cli, ["pictures-import"])
    real = runner.invoke(fbc.app.cli, ["pictures-import", "--apply"])

    assert "Would copy 1 pictures to 1 Units from 1 listings." in dry.output
    assert "This was a dry run. Add --apply to copy them." in dry.output
    assert "Copied 1 pictures to 1 Units from 1 listings." in real.output
    assert "dry run" not in real.output


def test_item_pictures_reads_one_or_many_addresses(app, monkeypatch):
    monkeypatch.undo()
    replies = [
        {"Item": {"PictureDetails": {"PictureURL": "https://i.ebayimg.com/a.jpg"}}},
        {"Item": {"PictureDetails": {"PictureURL": ["a", "b"]}}},
        {"Item": {}},
    ]
    calls = iter(replies)
    import fbc.ebay.api as real_api

    monkeypatch.setattr(
        real_api,
        "_call",
        lambda verb, data: (next(calls), {"errors": [], "warnings": [], "fees": []}),
    )

    assert real_api.item_pictures("1") == (["https://i.ebayimg.com/a.jpg"], [])
    assert real_api.item_pictures("1") == (["a", "b"], [])
    assert real_api.item_pictures("1") == ([], [])


def test_item_pictures_reports_an_ebay_error(app, monkeypatch):
    monkeypatch.setattr(
        api,
        "_call",
        lambda verb, data: (None, {"errors": ["Item not found."], "warnings": []}),
    )

    assert api.item_pictures("1") == ([], ["Item not found."])
