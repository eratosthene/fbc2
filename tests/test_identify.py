"""Identify Record, with Discogs mocked: no test reaches it."""

import datetime
import json
from types import SimpleNamespace

import pytest

import fbc
from test_util_discogs_sync import (
    FakeArtist,
    FakeCollectionItem,
    FakeMaster,
    FakeRelease,
)
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


class FakeClient:
    def __init__(self, hits, records, fail=None):
        self.hits, self.records, self.fail, self.urls = hits, records, fail, []

    def _get(self, url):
        self.urls.append(url)
        if self.fail and self.fail(url):
            raise self.fail(url)
        if "/database/search" in url:
            return {
                "results": [
                    h
                    if isinstance(h, dict)
                    else {"id": h, "format": ["Vinyl", "LP", "Album"]}
                    for h in self.hits
                ]
            }
        return self.records[int(url.rsplit("/", 1)[1])]


def use(monkeypatch, fake):
    monkeypatch.setattr(discogs, "client", lambda: fake)
    return fake


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
    assert r["searched"] == 3
    assert "country=US" in fake.urls[0] and "artist=Band" in fake.urls[0]


def test_a_second_search_with_another_matrix_reuses_the_calls(monkeypatch):
    fake = use(monkeypatch, FakeClient([1], {1: record(1, "US", ["A1"])}))
    identify.search(artist="Band", country="US", matrix="A1")
    r = identify.search(artist="Band", country="US", matrix="B2")
    assert len(fake.urls) == 2
    assert r["candidates"][0]["status"] == identify.MISMATCH


def test_search_needs_something_to_search_for(monkeypatch):
    fake = use(monkeypatch, FakeClient([], {}))
    assert not identify.search(country="US", matrix="A")["ok"]
    assert fake.urls == []


def test_dev_mode_never_reaches_discogs(dev_mode, monkeypatch):
    monkeypatch.setattr(
        discogs, "client", lambda: pytest.fail("reached Discogs in dev mode")
    )
    r = identify.search(artist="Band")
    assert not r["ok"] and r["dev_mode"] and "dev mode" in r["error"]
    assert identify.save_release(5)[0] is None


def test_rate_limit_on_a_release_keeps_the_rest(monkeypatch):
    from discogs_client.exceptions import HTTPError

    err = HTTPError("slow down", 429)
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
    from discogs_client.exceptions import HTTPError

    use(monkeypatch, FakeClient([], {}, fail=lambda u: HTTPError("no", 429)))
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


class CollectClient:
    """Enough of Discogs's collection API to add a copy and set its fields."""

    def __init__(self, fail_field=None):
        self.posts, self.fail_field = [], fail_field
        self.base = "https://api.discogs.com/users/me"

    def identity(self):
        client = self
        return SimpleNamespace(
            fetch=lambda key: client.base,
            collection_items=lambda rid: [
                FakeCollectionItem(
                    FakeRelease(
                        rid,
                        "Album",
                        1978,
                        artists=[FakeArtist(5, "Band")],
                        master=FakeMaster(77, 1977),
                    ),
                    folder_id=2,
                    instance_id=555,
                )
            ],
        )

    def _get(self, url):
        if url.endswith("/collection/folders"):
            return {
                "folders": [
                    {"id": 0, "name": "All"},
                    {"id": 1, "name": "Uncategorized"},
                    {"id": 2, "name": "Records"},
                ]
            }
        assert url.endswith("/collection/fields")
        return {
            "fields": [
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
        }

    def _post(self, url, data):
        self.posts.append((url, data))
        if self.fail_field and url.endswith("/fields/%d" % self.fail_field):
            raise RuntimeError("nope")
        return {"instance_id": 555} if "/instances/" not in url else None


def use_collection(monkeypatch, **kw):
    fake = CollectClient(**kw)
    monkeypatch.setattr(discogs, "client", lambda: fake)
    return fake


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
    assert fake.posts[0] == (fake.base + "/collection/folders/2/releases/9", None)
    assert (
        fake.base + "/collection/folders/2/releases/9/instances/555/fields/1",
        {"value": "Near Mint (NM or M-)"},
    ) in fake.posts
    assert (
        fake.base + "/collection/folders/2/releases/9/instances/555/fields/3",
        {"value": "ex-radio"},
    ) in fake.posts
    assert len(fake.posts) == 3  # blank and unoffered values aren't sent
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
    assert fake.posts == []


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
    assert "Extras Included" in html and fake.posts == []  # showing it writes nothing
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
    class NoMaster(FakeClient):
        def _get(self, url):
            if "/masters/" in url:
                raise RuntimeError("gone")
            return super()._get(url)

    use(monkeypatch, NoMaster([], {9: record(9, year=1978)}))
    release, _ = identify.save_release(9)
    assert release.master_year == 1978
