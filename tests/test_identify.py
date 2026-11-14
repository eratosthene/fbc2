"""Identify Record, with Discogs mocked: no test reaches it."""

import datetime
import json
from urllib.parse import urlencode

from types import SimpleNamespace

import pytest
from fake_discogs import FakeAccount, FakeDiscogs, api_error, collection_item

import fbc
from fbc import discogs, identify
from fbc.models.discogs import DiscogsRelease
from fbc.models.inventory import PurchaseLot


@pytest.fixture(autouse=True)
def fresh_cache():
    identify.clear_cache()
    yield
    identify.clear_cache()


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


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)


def record(rid, country="US", matrices=(), year=1978):
    return {
        "id": rid,
        "title": "Band - Album",
        "year": year,
        "country": country,
        "artists": [{"id": 5, "name": "The Band"}],
        "genres": ["Rock"],
        "styles": ["Prog"],
        "master_id": 77,
        "formats": [{"name": "Vinyl", "descriptions": ["LP"], "text": "Red"}],
        "labels": [{"name": "Lbl", "catno": "L-1"}],
        "images": [{"uri150": "http://img/%d.jpg" % rid}],
        "identifiers": [{"type": "Matrix / Runout", "value": m} for m in matrices]
        + [{"type": "Barcode", "value": "123"}],
    }


def _path(name, args, kwargs):
    """The request a call to fbc.discogs stands for, to read tests by."""
    if name == "search":
        return "/database/search?" + urlencode(kwargs)
    if name == "release":
        return "/releases/%d" % args[0]
    if name == "master":
        return "/masters/%d" % args[0]
    if name == "master_versions":
        return "/masters/%d/versions?%s" % (
            args[0],
            urlencode(dict(kwargs, page=args[1])),
        )
    return name


class FakeClient(FakeDiscogs):
    """
    Discogs's search hits and release records (masters too, by id). `urls` is
    each call as the request it stands for; `fail` takes one of them and
    returns an exception to raise, or None.
    """

    def __init__(self, hits, records, fail=None, **kwargs):
        def failing(name, args, kw):
            return fail and fail(_path(name, args, kw))

        super().__init__(
            hits=hits, releases=records, masters=records, fail=failing, **kwargs
        )

    @property
    def urls(self):
        return [_path(*call) for call in self.calls]


def use(monkeypatch, fake):
    return fake.install(monkeypatch)


# --- matching ---


def test_normalise_ignores_case_spaces_and_punctuation():
    assert identify.normalise(" a-1 / st.12 ") == "A1ST12"


def test_exact_partial_none_listed_and_mismatch():
    assert identify.match_matrix("sr 1001 a1", ["SR-1001-A-1"])[0] == identify.EXACT
    assert identify.match_matrix("1001-A", ["SR-1001-A-1 RE"])[0] == identify.PARTIAL
    assert identify.match_matrix("1001A", [])[0] == identify.NOT_LISTED
    assert identify.match_matrix("QQQQ", ["ZZZ 99"])[0] == identify.MISMATCH
    assert identify.match_matrix("", ["ZZZ"])[0] == identify.NOT_ASKED


def test_a_short_shared_run_is_not_a_partial_match():
    assert identify.match_matrix("AB1", ["XAB1X-LONGTEXT"])[0] == identify.MISMATCH


def test_highlight_marks_the_shared_run_and_escapes():
    status, _, text, span = identify.match_matrix("1001-A", ["SR-1001-A-1 <RE>"])
    assert str(identify.highlight(text, span)) == "SR-<mark>1001-A</mark>-1 &lt;RE&gt;"


def test_ranking_orders_by_matrix_then_country():
    ranked = identify.rank(identify.SAMPLE_CANDIDATES, "US", "sr1001a1re")
    ids = [c["id"] for c in ranked]
    assert ids[0] == 1000001  # exact and US
    assert ids.index(1000001) < ids.index(1000002)  # same matrix, wrong country
    assert ids.index(1000005) < ids.index(1000004)  # none listed beats mismatch
    assert ranked[ids.index(1000003)]["status"] == identify.PARTIAL
    assert identify.country_matches("us", "US") and identify.country_matches(
        "United States", "US"
    )


# --- type, reissue and color ---


def hit(rid, *formats):
    return {"id": rid, "format": list(formats)}


def test_only_lps_are_searched_by_default_and_never_cds(monkeypatch):
    fake = use(
        monkeypatch,
        FakeClient(
            [
                hit(1, "Vinyl", "LP", "Album"),
                hit(2, "Vinyl", '7"', "45 RPM"),
                hit(3, "CD", "Album"),
                hit(4, "Cassette", "LP"),
            ],
            {1: record(1), 2: record(2), 3: record(3), 4: record(4)},
        ),
    )
    r = identify.search(artist="Band")
    assert [c["id"] for c in r["candidates"]] == [1]
    assert "format=Vinyl" in fake.urls[0]
    assert r["searched"] == 2  # filtered hits cost no release fetch


def test_the_type_can_be_a_seven_inch_or_any_vinyl(monkeypatch):
    hits = [hit(1, "Vinyl", "LP"), hit(2, "Vinyl", '7"'), hit(3, "CD")]
    use(monkeypatch, FakeClient(hits, {1: record(1), 2: record(2), 3: record(3)}))
    sevens = identify.search(artist="Band", vinyl_type='7" (45 RPM)')
    assert [c["id"] for c in sevens["candidates"]] == [2]
    identify.clear_cache()
    anyvinyl = identify.search(artist="Band", vinyl_type="Any vinyl")
    assert sorted(c["id"] for c in anyvinyl["candidates"]) == [1, 2]


def test_reissue_is_blank_for_either_or_yes_or_no(monkeypatch):
    hits = [hit(1, "Vinyl", "LP", "Reissue"), hit(2, "Vinyl", "LP")]
    use(monkeypatch, FakeClient(hits, {1: record(1), 2: record(2)}))
    ids = lambda **kw: sorted(  # noqa: E731
        c["id"] for c in identify.search(artist="Band", **kw)["candidates"]
    )
    assert ids() == [1, 2]
    assert ids(reissue="yes") == [1]
    assert ids(reissue="no") == [2]


def test_a_matching_color_ranks_higher_but_nothing_is_hidden(monkeypatch):
    red, blue = record(1), record(2)
    blue["formats"][0]["text"] = "Blue"
    use(monkeypatch, FakeClient([2, 1], {1: red, 2: blue}))
    r = identify.search(artist="Band", color=" RED ")
    assert [c["id"] for c in r["candidates"]] == [1, 2]
    assert r["candidates"][0]["color_match"] and not r["candidates"][1]["color_match"]
    assert identify.color_matches("", "Red") is False


# --- search ---


def test_search_ranks_what_discogs_returns(monkeypatch):
    fake = use(
        monkeypatch,
        FakeClient(
            [1, 2],
            {1: record(1, "US", []), 2: record(2, "US", ["A-1 XYZ"])},
        ),
    )
    r = identify.search(artist="Band", country="US", matrix="a1xyz")
    assert r["ok"] and [c["id"] for c in r["candidates"]] == [2, 1]
    assert r["candidates"][0]["status"] == identify.EXACT
    assert r["candidates"][0]["thumb"] == "http://img/2.jpg"
    assert r["searched"] == 4  # two searches (one on the matrix) and two releases
    assert "q=a1xyz" in fake.urls[0] and "artist=Band" in fake.urls[1]


def test_a_second_search_with_the_same_matrix_reuses_the_calls(monkeypatch):
    fake = use(monkeypatch, FakeClient([1], {1: record(1, "US", ["A1"])}))
    identify.search(artist="Band", country="US", matrix="A1")
    calls = len(fake.urls)
    r = identify.search(artist="Band", country="US", matrix="A1", color="red")
    assert len(fake.urls) == calls
    assert r["candidates"][0]["status"] == identify.EXACT


def test_search_needs_something_to_search_for(monkeypatch):
    fake = use(monkeypatch, FakeClient([], {}))
    assert not identify.search(country="US", color="red")["ok"]
    assert fake.urls == []


def test_a_matrix_alone_is_enough_and_is_searched_as_text(monkeypatch):
    fake = use(monkeypatch, FakeClient([4], {4: record(4, "US", ["\u039415109"])}))
    r = identify.search(matrix="\u039415109")
    assert r["ok"] and [c["id"] for c in r["candidates"]] == [4]
    assert len(fake.urls) == 2 and "q=%CE%9415109" in fake.urls[0]
    assert r["candidates"][0]["found_by_matrix"]
    assert r["candidates"][0]["status"] == identify.EXACT


def test_what_the_matrix_search_finds_is_checked_first_and_not_twice(monkeypatch):
    class Two(FakeClient):
        def search(self, **params):
            self._record("search", **params)
            ids = [9] if "q" in params else [1, 2, 9]
            return [{"id": i, "format": ["Vinyl", "LP"]} for i in ids]

    use(
        monkeypatch,
        Two([], {i: record(i, matrices=["Z9"] if i == 9 else []) for i in (1, 2, 9)}),
    )
    r = identify.search(artist="Band", matrix="Z9")
    assert [c["id"] for c in r["candidates"]] == [9, 1, 2]
    assert [c["found_by_matrix"] for c in r["candidates"]] == [True, False, False]


def test_more_releases_are_checked_when_there_is_a_matrix(monkeypatch):
    ids = list(range(1, 31))
    fake = use(monkeypatch, FakeClient(ids, {i: record(i) for i in ids}))
    assert len(identify.search(artist="Band")["candidates"]) == 12
    identify.clear_cache()
    assert len(identify.search(artist="Band", matrix="A1")["candidates"]) == 20
    assert fake.urls


def test_a_matrix_match_outweighs_a_matching_country():
    ranked = identify.rank(
        [
            {"id": 1, "country": "US", "matrices": ["ZZZ 99"]},
            {"id": 2, "country": "UK", "matrices": ["A-1 15109"]},
            {"id": 3, "country": "UK", "matrices": ["A-1 15109 X"]},
        ],
        "US",
        "a1 15109",
    )
    assert [c["id"] for c in ranked] == [2, 3, 1]


def test_dev_mode_never_reaches_discogs(dev_mode, monkeypatch):
    monkeypatch.setattr(
        discogs, "client", lambda: pytest.fail("reached Discogs in dev mode")
    )
    r = identify.search(artist="Band")
    assert not r["ok"] and r["dev_mode"] and "dev mode" in r["error"]
    assert identify.save_release(5)[0] is None


def test_rate_limit_on_a_release_keeps_the_rest(monkeypatch):
    err = api_error(429, "slow down")
    fake = FakeClient(
        [1, 2],
        {1: record(1, matrices=["A1"])},
        fail=lambda u: err if u.endswith("/2") else None,
    )
    use(monkeypatch, fake)
    r = identify.search(artist="Band", matrix="A1")
    assert r["ok"] and r["note"] and len(r["candidates"]) == 2
    assert r["candidates"][-1]["unchecked"]


def test_rate_limit_on_the_search_is_a_message_not_an_error(monkeypatch):
    use(monkeypatch, FakeClient([], {}, fail=lambda u: api_error(429, "no")))
    r = identify.search(artist="Band")
    assert not r["ok"] and "too many" in r["error"]


def test_no_discogs_connection_is_a_message(monkeypatch):
    def client():
        raise discogs.DiscogsAuthError("Connect fbc to Discogs first")

    monkeypatch.setattr(discogs, "client", client)
    assert identify.search(artist="x")["error"].startswith("Connect fbc")


# --- picking ---


def test_picking_saves_the_release_without_an_instance(monkeypatch, client):
    use(monkeypatch, FakeClient([], {9: record(9)}))
    lot = PurchaseLot(name="Lot", date=datetime.date(2026, 1, 1), price=10)
    fbc.db.session.add(lot)
    fbc.db.session.commit()
    r = client.post(
        "/identifyrecord/pick",
        data={"release_id": "9", "lot": str(lot.id), "matrix": "A-1"},
    )
    assert r.status_code == 302
    location = r.headers["Location"]
    assert "/unitmodelview/add" in location
    assert "discogs_release=9" in location and "discogs_master=77" in location
    assert "purchase_lot=%d" % lot.id in location and "matrix=A-1" in location
    saved = fbc.db.session.get(DiscogsRelease, 9)
    assert saved.discogs_instances == [] and saved.country == "US"
    assert [str(a) for a in saved.artists] == ["Band, The"]
    # Picking it again doesn't fetch or duplicate it.
    assert (
        client.post("/identifyrecord/pick", data={"release_id": "9"}).status_code == 302
    )


def test_the_page_renders_the_form_results_and_sample(monkeypatch, client):
    html = client.get("/identifyrecord/").get_data(as_text=True)
    assert "Search Discogs" in html and 'value="US"' in html
    use(monkeypatch, FakeClient([2], {2: record(2, matrices=["SR-1001-A-1"])}))
    html = client.get("/identifyrecord/?go=1&artist=Band&matrix=1001a").get_data(
        as_text=True
    )
    assert "<mark>" in html and "Partial matrix match" in html
    assert "This one: add to Discogs" in html and "Just add the Unit" in html


def test_the_page_says_dev_mode_and_offers_the_sample(dev_mode, client):
    html = client.get("/identifyrecord/?go=1&artist=Band").get_data(as_text=True)
    assert "Not available in dev mode." in html and "Try the sample data" in html
    html = client.get("/identifyrecord/?sample=1&matrix=SR-1001-A-1RE").get_data(
        as_text=True
    )
    assert "Exact matrix match" in html and "Sample data, not from Discogs." in html


def test_the_sample_is_off_outside_dev_mode(client):
    html = client.get("/identifyrecord/?sample=1").get_data(as_text=True)
    assert "Sample data" not in html


def test_the_form_has_type_reissue_and_color(client):
    html = client.get("/identifyrecord/").get_data(as_text=True)
    assert 'name="type"' in html and "7&#34; (45 RPM)" in html or '7" (45 RPM)' in html
    assert 'name="reissue"' in html and 'name="color"' in html
    assert "<option selected>LP</option>" in html


# --- adding to the Discogs collection ---


FOLDERS = [
    {"id": 0, "name": "All"},
    {"id": 1, "name": "Uncategorized"},
    {"id": 2, "name": "Records"},
]
FIELDS = [
    {
        "id": 1,
        "name": "Media Condition",
        "type": "dropdown",
        "options": ["Mint (M)", "Near Mint (NM or M-)"],
    },
    {
        "id": 2,
        "name": "Sleeve Condition",
        "type": "dropdown",
        "options": ["Very Good Plus (VG+)"],
    },
    {"id": 3, "name": "Notes", "type": "textarea"},
    {
        "id": 4,
        "name": "Dust Jacket Condition",
        "type": "dropdown",
        "options": ["Good (G)"],
    },
    {"id": 9, "name": "Something Else", "type": "textarea"},
]


def use_collection(monkeypatch, fail_field=None):
    """An account that can add a copy of release 9 (instance 555) and set its fields."""

    def fail(name, args, kwargs):
        if name == "set_collection_field" and args[3] == fail_field:
            return RuntimeError("nope")

    account = FakeAccount(
        folders=FOLDERS,
        fields=FIELDS,
        items=[
            collection_item(
                9, 555, folder_id=2, artists=[(5, "Band")], master_id=77, year=1978
            )
        ],
        next_instance_id=555,
        fail=fail,
    )
    FakeDiscogs(
        releases={9: {"id": 9, "country": "US", "released": "1978-05-01"}},
        masters={77: {"id": 77, "year": 1977}},
        account=account,
    ).install(monkeypatch)
    return account


def writes(account):
    """What was written to the account: adds, then field values."""
    return account.calls_to("add_to_collection") + account.calls_to(
        "set_collection_field"
    )


def test_the_form_offers_the_account_folders_and_the_named_fields(monkeypatch):
    use_collection(monkeypatch)
    form = identify.collection_form()
    assert [f[1] for f in form["folders"]] == ["Uncategorized", "Records"]
    assert [f["name"] for f in form["fields"]] == [
        "Media Condition",
        "Sleeve Condition",
        "Notes",
        "Dust Jacket Condition",
    ]
    assert form["missing"] == ["Extras Included"]


def test_collect_adds_sets_fields_and_saves_the_instance(monkeypatch):
    fake = use_collection(monkeypatch)
    release, instance_id, warnings, problem = identify.collect(
        9,
        2,
        {
            "Media Condition": "Near Mint (NM or M-)",
            "Notes": "ex-radio",
            "Dust Jacket Condition": "Excellent (E)",  # not a choice
            "Sleeve Condition": "",
        },
    )
    assert problem is None and instance_id == 555
    assert fake.calls_to("add_to_collection") == [(2, 9)]
    assert sorted(fake.calls_to("set_collection_field")) == [
        (2, 9, 555, 1, "Near Mint (NM or M-)"),
        (2, 9, 555, 3, "ex-radio"),
    ]
    assert len(writes(fake)) == 3  # blank and unoffered values aren't sent
    assert warnings == ["Dust Jacket Condition: Discogs has no choice 'Excellent (E)'"]
    assert [i.instance_id for i in release.discogs_instances] == [555]


def test_a_field_that_fails_is_a_warning_and_the_copy_is_still_saved(monkeypatch):
    use_collection(monkeypatch, fail_field=3)
    release, instance_id, warnings, problem = identify.collect(9, 1, {"Notes": "x"})
    assert problem is None and release and "Notes wasn't set" in warnings[0]


def test_collect_refuses_a_folder_that_isnt_the_accounts(monkeypatch):
    fake = use_collection(monkeypatch)
    assert identify.collect(9, 0, {})[3]
    assert identify.collect(9, 77, {})[3]
    assert writes(fake) == []


def test_collect_never_reaches_discogs_in_dev_mode(dev_mode, monkeypatch):
    monkeypatch.setattr(discogs, "client", lambda: pytest.fail("reached Discogs"))
    assert identify.collect(9, 1, {})[3]
    assert not identify.collection_form()["ok"]


def test_grading_comes_from_the_two_conditions():
    assert (
        identify.grading_from(
            {
                "Media Condition": "Near Mint (NM or M-)",
                "Sleeve Condition": "Very Good Plus (VG+)",
            }
        )
        == "NM or M-/VG+"
    )
    assert identify.grading_from({"Sleeve Condition": "Good (G)"}) == "G"


def test_collect_page_posts_then_goes_to_add_unit(monkeypatch, client):
    fake = use_collection(monkeypatch)
    html = client.get("/identifyrecord/collect?release_id=9&lot=3&matrix=A1").get_data(
        as_text=True
    )
    assert "adds a copy to your Discogs account" in html and "Records" in html
    assert "Extras Included" in html and writes(fake) == []  # showing it writes nothing
    r = client.post(
        "/identifyrecord/collect",
        data={
            "release_id": "9",
            "lot": "3",
            "matrix": "A1",
            "folder": "2",
            "field_1": "Near Mint (NM or M-)",
            "field_2": "Very Good Plus (VG+)",
            "field_3": "ex-radio",
        },
    )
    assert r.status_code == 302
    location = r.headers["Location"]
    assert "discogs_instance=555" in location and "discogs_release=9" in location
    assert "notes=ex-radio" in location and "matrix=A1" in location
    assert "(1977)" in location and "pressing=US+1978+Original" in location
    assert "grading=NM+or+M-/VG%2B" in location


def _lot_with_folder(folder_id):
    from fbc.models.discogs import Folder

    folder = fbc.db.session.get(Folder, folder_id) or Folder(
        folder_id=folder_id, name="Folder %d" % folder_id
    )
    lot = PurchaseLot(
        name="Linked lot",
        date=datetime.date(2026, 1, 1),
        price=10,
        folder=folder,
    )
    fbc.db.session.add(lot)
    fbc.db.session.commit()
    return lot


def _selected(html):
    import re

    return re.findall(r'<option value="(\d+)" selected', html.split('id="folder"')[1])


def test_collect_form_preselects_the_purchase_lots_folder(monkeypatch, client):
    fake = use_collection(monkeypatch)
    lot = _lot_with_folder(2)
    html = client.get("/identifyrecord/collect?release_id=9&lot=%d" % lot.id).get_data(
        as_text=True
    )
    assert _selected(html)[:1] == ["2"]
    assert "Chosen from the Purchase Lot" in html
    assert writes(fake) == []  # choosing a default writes nothing


def test_collect_form_has_no_default_for_a_lot_without_a_folder(monkeypatch, client):
    use_collection(monkeypatch)
    lot = PurchaseLot(name="Bare", date=datetime.date(2026, 1, 1), price=1)
    fbc.db.session.add(lot)
    fbc.db.session.commit()
    for lot_arg in ("%d" % lot.id, "", "abc", "99999"):
        html = client.get(
            "/identifyrecord/collect?release_id=9&lot=" + lot_arg
        ).get_data(as_text=True)
        assert _selected(html) == []
        assert "Chosen from the Purchase Lot" not in html


# --- Unit name and pressing ---


def test_pressing_is_country_year_and_original_or_reissue():
    def release(country, year, *descriptions):
        formats = [{"name": "Vinyl", "descriptions": list(descriptions)}]
        return SimpleNamespace(country=country, year=year, formats=json.dumps(formats))

    assert identify.pressing_for(release("US", 1970, "LP")) == "US 1970 Original"
    assert (
        identify.pressing_for(release("UK", 1990, "LP", "Reissue")) == "UK 1990 Reissue"
    )
    assert (
        identify.pressing_for(release("Germany", 1985, "Repress")) == "DE 1985 Reissue"
    )
    assert identify.pressing_for(release("Wakanda", 1985)) == "Wakanda 1985 Original"
    assert identify.pressing_for(release(None, None)) == "Original"


def test_the_unit_is_named_with_the_master_year(monkeypatch):
    rec = record(9, year=1990)
    rec["formats"][0]["descriptions"] = ["LP", "Reissue"]
    rec["country"] = "UK"
    use(
        monkeypatch,
        FakeClient([], {9: rec, 77: {"year": 1970}}),
    )
    release, problem = identify.save_release(9)
    assert problem is None and release.master_year == 1970
    params = identify.unit_add_params(release)
    assert params["name"] == "Band, The - Band - Album (1970)"
    assert params["pressing"] == "UK 1990 Reissue"


def test_a_missing_master_falls_back_to_the_release_year(monkeypatch):
    use(
        monkeypatch,
        FakeClient(
            [],
            {9: record(9, year=1978)},
            fail=lambda u: RuntimeError("gone") if "/masters/" in u else None,
        ),
    )
    release, _ = identify.save_release(9)
    assert release.master_year == 1978


# --- Release ID ---


def test_release_ids_are_read_from_numbers_brackets_and_addresses():
    f = identify.release_id_from
    assert f("123") == f(" r123 ") == f("[r123]") == 123
    assert f("https://www.discogs.com/release/123-Some-Band-Album") == 123
    assert f("https://www.discogs.com/ja/release/123-x?foo=1") == 123
    assert f("https://www.discogs.com/master/55-x") is None
    assert f("abc") is None


def test_a_release_id_overrides_the_other_fields_and_gives_one_result(monkeypatch):
    fake = use(monkeypatch, FakeClient([1, 2], {7: record(7, "UK", ["A1"])}))
    r = identify.search(
        artist="Someone Else",
        catno="X",
        country="US",
        matrix="A1",
        vinyl_type="Any vinyl",
        reissue="yes",
        release_id="[r7]",
    )
    assert r["ok"] and [c["id"] for c in r["candidates"]] == [7]
    assert fake.urls == ["/releases/7"]  # no search made
    assert r["candidates"][0]["status"] == identify.EXACT  # still ranked and shown
    assert not r["candidates"][0]["country_match"]


def test_a_bad_or_missing_release_id_is_a_message(monkeypatch):
    use(monkeypatch, FakeClient([], {}, fail=lambda u: api_error(404, "nope")))
    assert "isn't a release id" in identify.search(release_id="master 5")["error"]
    assert identify.search(release_id="99")["error"] == "Discogs has no release 99."


def test_a_release_id_in_dev_mode_never_reaches_discogs(dev_mode, monkeypatch):
    monkeypatch.setattr(discogs, "client", lambda: pytest.fail("reached Discogs"))
    assert identify.search(release_id="7")["dev_mode"]


def test_the_page_takes_a_release_id(monkeypatch, client):
    use(monkeypatch, FakeClient([], {7: record(7)}))
    html = client.get("/identifyrecord/?go=1&release_id=7&artist=zzz").get_data(
        as_text=True
    )
    text = " ".join(html.split())
    assert 'name="release_id"' in html
    assert "Band - Album" in text and "1 release shown" in text


def test_add_unit_from_the_releases_list_uses_the_master_year_and_pressing(app, db):
    from fbc.models.discogs import DiscogsRelease

    release = DiscogsRelease(
        release_id=5,
        title="Album",
        year=1990,
        master_year=1970,
        master_id=1,
        country="UK",
        formats=json.dumps([{"name": "Vinyl", "descriptions": ["LP", "Reissue"]}]),
        notes="[]",
    )
    html = release.unit_add(instance_id=1, markup=False)
    assert "(1970)" in html and "pressing=UK+1990+Reissue" in html


# --- notes, identifiers and pictures ---


def test_the_results_carry_notes_identifiers_and_every_picture(monkeypatch, client):
    rec = record(5, matrices=["A1"])
    rec["notes"] = "Gatefold sleeve.\nPromo copy."
    rec["identifiers"].append(
        {"type": "Other", "value": "STERLING", "description": "Mastering"}
    )
    rec["images"] = [
        {"uri": "http://img/full%d.jpg" % n, "uri150": "http://img/t%d.jpg" % n}
        for n in range(6)
    ]
    use(monkeypatch, FakeClient([5], {5: rec}))
    found = identify.search(artist="Band")["candidates"][0]
    assert found["notes"].startswith("Gatefold")
    assert {"type": "Barcode", "value": "123", "description": ""} in found[
        "identifiers"
    ]
    assert len(found["images"]) == 6 and found["images"][5]["full"].endswith(
        "full5.jpg"
    )
    html = client.get("/identifyrecord/?go=1&artist=Band").get_data(as_text=True)
    assert html.count('class="identify-image"') == 6
    assert 'href="http://img/full3.jpg"' in html and "identify-viewer" in html
    assert "<details" in html and "STERLING" in html and "Promo copy." in html
    assert "<details open" not in html  # closed until opened


# --- Master ---


class MasterClient(FakeClient):
    """A master's versions (paged) and a master search."""

    def __init__(self, versions, records, masters=(), page_size=None):
        super().__init__([], records, versions=versions, masters_found=masters)
        self.page_size = page_size or 100


def version(rid, country="US", fmt="LP, Album", major=("Vinyl",)):
    return {
        "id": rid,
        "title": "Close To You",
        "country": country,
        "format": fmt,
        "major_formats": list(major),
        "label": "A&M",
        "catno": "SP 4271",
        "released": "1970",
    }


def test_master_ids_are_read_from_numbers_brackets_and_addresses():
    f = identify.master_id_from
    assert f("84990") == f("m84990") == f("[m84990]") == 84990
    assert f("https://www.discogs.com/master/84990-Carpenters-Close-To-You") == 84990
    assert f("https://www.discogs.com/release/5-x") is None
    assert f("nope") is None


def test_a_master_lists_its_filtered_versions_and_ranks_them_by_matrix(monkeypatch):
    versions = [
        version(1, "UK"),
        version(2, "US", "CD, Album", ("CD",)),
        version(3, "US"),
        version(4, "US", "LP, Album, Reissue"),
        version(5, "US", '7"', ("Vinyl",)),
    ]
    records = {
        3: record(3, "US", ["A-1 15109"]),
        4: record(4, "US", ["B-2 99999"]),
        1: record(1, "UK", []),
    }
    fake = use(monkeypatch, MasterClient(versions, records))
    r = identify.search(
        master="https://www.discogs.com/master/84990-Carpenters-Close-To-You",
        artist="ignored",
        country="US",
        matrix="a1 15109",
    )
    assert r["ok"] and [c["id"] for c in r["candidates"]] == [3, 4]
    assert r["candidates"][0]["status"] == identify.EXACT
    assert "/masters/84990/versions" in fake.urls[0]
    assert "country=US" in fake.urls[0] and "format=Vinyl" in fake.urls[0]
    assert not any("database/search" in u for u in fake.urls)  # no plain search
    reissues = identify.search(master="84990", country="US", reissue="yes")
    assert [c["id"] for c in reissues["candidates"]] == [4]


def test_a_big_master_is_checked_twenty_at_a_time(monkeypatch):
    versions = [version(i) for i in range(1, 46)]
    use(monkeypatch, MasterClient(versions, {i: record(i) for i in range(1, 46)}))
    first = identify.search(master="1")
    assert len(first["candidates"]) == 20 and first["next_offset"] == 20
    second = identify.search(master="1", offset=20)
    assert [c["id"] for c in second["candidates"]] == list(range(21, 41))
    third = identify.search(master="1", offset=40)
    assert len(third["candidates"]) == 5 and third["next_offset"] is None


def test_a_bad_or_unknown_master_is_a_message(monkeypatch):
    use(monkeypatch, FakeClient([], {}, fail=lambda u: api_error(404, "nope")))
    assert "isn't a master id" in identify.search(master="xyz")["error"]
    assert identify.search(master="5")["error"] == "Discogs has no master 5."


def test_finding_a_master_from_artist_and_title(monkeypatch):
    masters = [{"id": 84990, "title": "Carpenters - Close To You", "year": "1970"}]
    fake = use(monkeypatch, MasterClient([], {}, masters))
    r = identify.find_masters("Carpenters", "Close To You")
    assert r["ok"] and r["masters"][0]["id"] == 84990
    assert "artist=Carpenters" in fake.urls[0] and "type=master" in fake.urls[0]
    assert not identify.find_masters()["ok"]


def test_master_features_never_reach_discogs_in_dev_mode(dev_mode, monkeypatch):
    monkeypatch.setattr(discogs, "client", lambda: pytest.fail("reached Discogs"))
    assert identify.search(master="1")["dev_mode"]
    assert identify.find_masters("a")["dev_mode"]


def test_the_page_finds_a_master_and_uses_it(monkeypatch, client):
    masters = [{"id": 84990, "title": "Carpenters - Close To You", "year": "1970"}]
    use(monkeypatch, MasterClient([version(3)], {3: record(3)}, masters))
    html = client.get(
        "/identifyrecord/?find_master=1&artist=Carpenters&title=Close&matrix=A1&country="
    ).get_data(as_text=True)
    assert "Use this master" in html and "master=84990" in html
    assert "matrix=A1" in html  # the link keeps the form
    assert "country=&amp;" in html  # a blank country stays blank
    html = client.get("/identifyrecord/?go=1&master=84990&country=US").get_data(
        as_text=True
    )
    assert "1 release shown" in " ".join(html.split())
    assert 'name="master"' in html and "Find master from Artist and Title" in html


def test_a_huge_master_stops_at_the_page_limit_and_says_so(monkeypatch):
    monkeypatch.setattr(identify, "MAX_VERSION_PAGES", 2)
    versions = [version(i, "UK") for i in range(1, 11)]
    use(
        monkeypatch,
        MasterClient(versions, {i: record(i, "UK") for i in range(1, 11)}, page_size=2),
    )
    r = identify.search(master="1", country="UK")
    assert (
        len(r["candidates"]) == 4 and "first 200 versions" not in r["note"] or r["note"]
    )
    assert r["next_offset"] is None


def test_a_matrix_keeps_checking_batches_until_an_exact_match(monkeypatch):
    versions = [version(i) for i in range(1, 46)]
    records = {i: record(i) for i in range(1, 46)}
    records[25] = record(25, matrices=["A-1 15109"])  # in the second batch
    fake = use(monkeypatch, MasterClient(versions, records))
    r = identify.search(master="1", matrix="a1 15109")
    ids = [c["id"] for c in r["candidates"]]
    assert ids[0] == 25 and len(ids) == 40  # two batches, stopped at the cap
    assert r["candidates"][0]["status"] == identify.EXACT
    assert r["next_offset"] == 40
    releases = [u for u in fake.urls if "/releases/" in u]
    assert len(releases) == 40


def test_a_matrix_stops_after_the_first_batch_that_has_an_exact_match(monkeypatch):
    versions = [version(i) for i in range(1, 46)]
    records = {i: record(i) for i in range(1, 46)}
    records[3] = record(3, matrices=["A-1 15109"])
    fake = use(monkeypatch, MasterClient(versions, records))
    r = identify.search(master="1", matrix="a1 15109")
    assert len(r["candidates"]) == 20 and r["next_offset"] == 20
    assert len([u for u in fake.urls if "/releases/" in u]) == 20


def test_without_a_matrix_only_one_batch_is_checked(monkeypatch):
    versions = [version(i) for i in range(1, 46)]
    fake = use(
        monkeypatch, MasterClient(versions, {i: record(i) for i in range(1, 46)})
    )
    r = identify.search(master="1")
    assert len(r["candidates"]) == 20
    assert len([u for u in fake.urls if "/releases/" in u]) == 20


def test_the_results_follow_the_lot_and_matrix_changed_after_searching(
    monkeypatch, client
):
    use(monkeypatch, FakeClient([2], {2: record(2)}))
    html = client.get("/identifyrecord/?go=1&artist=Band").get_data(as_text=True)
    # Each result's buttons hold copies; this script keeps them in step.
    assert 'form[action$="/pick"] input[name="' in html
    assert 'sync("lot")' in html and 'sync("matrix")' in html
    assert 'id="lot"' in html and 'id="matrix"' in html
