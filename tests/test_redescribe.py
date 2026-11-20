"""`flask update-listing-descriptions`: new descriptions onto active listings."""

import fbc
from fbc.ebay import describe, redescribe
from fbc.models.ebay import EbayDraft, EbayListing
from fbc.models.inventory import Unit

db = fbc.db


def _listing(item_id="1", notes="Plays great.", sold=False, **listing_fields):
    db.session.add(EbayListing(item_id=item_id, title="T " + item_id, **listing_fields))
    unit = Unit(
        name="U" + item_id, record_note=notes, sold=sold, ebay_listing_id=item_id
    )
    db.session.add(unit)
    db.session.commit()
    return unit


class FakeEbay:
    def __init__(self, failing=()):
        self.failing = failing
        self.revised = []

    def call(self, verb, data):
        assert verb == "ReviseFixedPriceItem"
        assert set(data["Item"]) == {"ItemID", "Description"}
        item = data["Item"]["ItemID"]
        self.revised.append((item, data["Item"]["Description"]))
        errors = ["nope"] if item in self.failing else []
        return {}, {"errors": errors, "warnings": [], "fees": []}


def _run(monkeypatch, fake=None, **kwargs):
    fake = fake or FakeEbay()
    monkeypatch.setattr(redescribe.api, "_call", fake.call)
    lines = []
    result = redescribe.update_all(echo=lines.append, pause=0, **kwargs)
    return result["counts"], fake, lines


def test_dry_run_sends_nothing(monkeypatch):
    _listing()
    counts, fake, lines = _run(monkeypatch)
    assert counts[redescribe.SET] == 1
    assert fake.revised == []
    assert lines == ["Would set 1: T 1"]


def test_apply_sends_the_template_description_and_nothing_else(monkeypatch):
    unit = _listing()
    counts, fake, _ = _run(monkeypatch, apply=True)
    assert counts[redescribe.SET] == 1
    assert fake.revised == [("1", describe.build_description(unit))]


def test_apply_updates_the_drafts_text_and_then_it_is_already_right(monkeypatch):
    unit = _listing()
    draft = EbayDraft(unit=unit, title="T", description="old", ebay_listing_id="1")
    db.session.add(draft)
    db.session.commit()
    _run(monkeypatch, apply=True)
    assert draft.description == describe.build_description(unit)
    counts, fake, _ = _run(monkeypatch, apply=True)
    assert counts[redescribe.RIGHT] == 1 and fake.revised == []


def test_a_failed_revise_is_counted_and_the_draft_is_kept(monkeypatch):
    unit = _listing()
    draft = EbayDraft(unit=unit, title="T", description="old", ebay_listing_id="1")
    db.session.add(draft)
    db.session.commit()
    counts, _, lines = _run(monkeypatch, FakeEbay(failing={"1"}), apply=True)
    assert counts[redescribe.FAILED] == 1 and counts[redescribe.SET] == 0
    assert draft.description == "old"
    assert lines == ["Failed 1: nope"]


def test_listings_that_cant_be_redescribed_are_skipped(monkeypatch):
    _listing("1", notes=None)
    _listing("2", sold=True)
    _listing("4", ended=True)
    _listing("5", archived=True)
    counts, fake, _ = _run(monkeypatch, apply=True)
    assert counts[redescribe.NO_NOTES] == 1
    assert counts[redescribe.NO_UNIT] == 1
    assert sum(counts.values()) == 2  # ended and archived listings aren't looked at
    assert fake.revised == []


def test_a_mystery_box_is_skipped(monkeypatch):
    from fbc.models.inventory import StorageBox

    box = StorageBox(name="Bin")
    db.session.add(box)
    db.session.commit()
    _listing("1", pick_from_box_id=box.id, pick_count=3)
    counts, fake, _ = _run(monkeypatch, apply=True)
    assert counts[redescribe.MYSTERY] == 1 and fake.revised == []


def test_item_and_limit(monkeypatch):
    for n in "123":
        _listing(n)
    counts, fake, _ = _run(monkeypatch, apply=True, item="2")
    assert [i for i, _ in fake.revised] == ["2"]
    counts, fake, _ = _run(monkeypatch, apply=True, limit=1)
    assert len(fake.revised) == 1


def test_apply_is_refused_in_dev_mode(app, monkeypatch):
    monkeypatch.setitem(app.config, "DEV_MODE", True)
    _listing()
    result = app.test_cli_runner().invoke(
        args=["update-listing-descriptions", "--apply"]
    )
    assert result.exit_code != 0
    assert "Dev Mode is turned on" in result.output


def test_command_says_it_was_a_dry_run(app, monkeypatch):
    _listing()
    result = app.test_cli_runner().invoke(args=["update-listing-descriptions"])
    assert "Would set 1." in result.output and "dry run" in result.output
