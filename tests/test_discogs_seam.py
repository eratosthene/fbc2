"""
What fbc.discogs sends, and what it makes of the answers. This is the one test
file that fakes the Discogs library (at its _get, _post and _delete): every
other test fakes fbc.discogs itself (tests/fake_discogs.py).
"""

import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from discogs_client.exceptions import HTTPError

from fbc import discogs

BASE = "https://api.discogs.com"
ME = BASE + "/users/me"


class FakeLibrary:
    """discogs_client.Client: answers GETs from `answers`, keyed by path."""

    _base_url = BASE

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.requests = []

    def identity(self):
        lib = self

        class Me:
            username = "me"

            def fetch(self, key):
                assert key == "resource_url"
                lib.requests.append(("IDENTITY", None, None))
                return ME

        return Me()

    def _get(self, url):
        parsed = urlparse(url)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.requests.append(("GET", parsed.path, query))
        answer = self.answers[parsed.path]
        if callable(answer):
            answer = answer(query)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _post(self, url, data):
        self.requests.append(("POST", urlparse(url).path, data))
        answer = self.answers.get(("POST", urlparse(url).path))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _delete(self, url):
        self.requests.append(("DELETE", urlparse(url).path, None))


@pytest.fixture
def library(monkeypatch):
    def use(answers=None):
        lib = FakeLibrary(answers)
        monkeypatch.setattr(discogs, "client", lambda: lib)
        return lib

    return use


def paged(key, items, per_page=100, claimed=None):
    """An answer that serves `items` a page at a time, the way Discogs does."""

    def answer(query):
        page, per = int(query["page"]), int(query["per_page"])
        assert per == per_page
        return {
            "pagination": {
                "page": page,
                "pages": -(-len(items) // per) or 1,
                "per_page": per,
                "items": len(items) if claimed is None else claimed,
            },
            key: items[(page - 1) * per : page * per],
        }

    return answer


# --- Only this module uses the library ---


def test_no_other_module_imports_the_library():
    fbc_folder = Path(discogs.__file__).parent
    others = [
        path.relative_to(fbc_folder).as_posix()
        for path in fbc_folder.rglob("*.py")
        if path.name != "discogs.py"
        and (
            "import discogs_client" in path.read_text()
            or "from discogs_client" in path.read_text()
        )
    ]

    assert others == []


# --- Errors ---


def test_the_librarys_errors_become_api_errors_with_their_status(library):
    library({"/releases/1": HTTPError("Release not found.", 404)})

    with pytest.raises(discogs.ApiError, match="Release not found.") as caught:
        discogs.release(1)

    assert caught.value.status_code == 404
    assert caught.value.refused and not caught.value.rate_limited


@pytest.mark.parametrize(
    "status, refused, rate_limited",
    [(404, True, False), (422, True, False), (429, False, True), (500, False, False)],
)
def test_a_4xx_but_not_a_429_is_a_refusal(status, refused, rate_limited):
    error = discogs.ApiError("x", status)

    assert (error.refused, error.rate_limited) == (refused, rate_limited)


def test_anything_else_is_passed_on_as_it_is(library):
    lib = library()
    lib.answers["/releases/1"] = OSError("down")

    with pytest.raises(OSError):
        discogs.release(1)


# --- The database ---


def test_release_master_and_price_calls_ask_for_their_endpoints(library):
    lib = library(
        {
            "/releases/5": {"id": 5},
            "/masters/8": {"id": 8},
            "/marketplace/price_suggestions/5": {"Mint (M)": {"value": 9}},
            "/marketplace/stats/5": {"num_for_sale": 2},
        }
    )

    assert discogs.release("5") == {"id": 5}
    assert discogs.master(8) == {"id": 8}
    assert discogs.price_suggestions(5) == {"Mint (M)": {"value": 9}}
    assert discogs.marketplace_stats(5) == {"num_for_sale": 2}
    assert [(m, p) for m, p, _ in lib.requests] == [
        ("GET", "/releases/5"),
        ("GET", "/masters/8"),
        ("GET", "/marketplace/price_suggestions/5"),
        ("GET", "/marketplace/stats/5"),
    ]


def test_search_passes_every_parameter_and_returns_the_hits(library):
    lib = library({"/database/search": {"results": [{"id": 1}]}})

    hits = discogs.search(type="release", artist="The Band", per_page=50, q=None)

    assert hits == [{"id": 1}]
    assert lib.requests == [
        (
            "GET",
            "/database/search",
            {"type": "release", "artist": "The Band", "per_page": "50"},
        )
    ]


def test_search_without_results_is_an_empty_list(library):
    library({"/database/search": {}})

    assert discogs.search(artist="x") == []


def test_master_versions_come_a_page_at_a_time_with_their_filters(library):
    lib = library({"/masters/8/versions": {"versions": [], "pagination": {"pages": 1}}})

    discogs.master_versions(8, 2, country="UK", format="Vinyl")

    assert lib.requests == [
        (
            "GET",
            "/masters/8/versions",
            {"page": "2", "per_page": "100", "country": "UK", "format": "Vinyl"},
        )
    ]


# --- The collection ---


def test_the_account_is_asked_for_once(library):
    lib = library({"/users/me/collection/folders": {"folders": []}})
    account = discogs.Account(lib)

    account.collection_folders()
    account.collection_folders()

    assert [r[0] for r in lib.requests].count("IDENTITY") == 1


def test_folders_and_fields_are_the_accounts(library):
    lib = library(
        {
            "/users/me/collection/folders": {"folders": [{"id": 0, "name": "All"}]},
            "/users/me/collection/fields": {"fields": [{"id": 3, "name": "Notes"}]},
        }
    )
    account = discogs.Account(lib)

    assert account.collection_folders() == [{"id": 0, "name": "All"}]
    assert account.collection_fields() == [{"id": 3, "name": "Notes"}]


def test_the_whole_collection_is_every_page_of_folder_zero(library):
    items = [{"instance_id": i} for i in range(1, 251)]
    lib = library({"/users/me/collection/folders/0/releases": paged("releases", items)})

    got = discogs.Account(lib).collection_items()

    assert got == items
    pages = [q["page"] for m, p, q in lib.requests if m == "GET"]
    assert pages == ["1", "2", "3"]


def test_a_collection_that_came_back_short_is_an_error(library):
    items = [{"instance_id": i} for i in range(1, 11)]
    lib = library(
        {
            "/users/me/collection/folders/0/releases": paged(
                "releases", items, claimed=12
            )
        }
    )

    with pytest.raises(discogs.IncompleteRead, match="12 items but 10"):
        discogs.Account(lib).collection_items()


def test_the_copies_of_one_release(library):
    items = [{"instance_id": 1}, {"instance_id": 2}]
    lib = library({"/users/me/collection/releases/9": paged("releases", items)})

    assert discogs.Account(lib).collection_items(9) == items


def test_a_release_with_no_copy_left_has_no_items(library):
    lib = library({"/users/me/collection/releases/9": HTTPError("Not found", 404)})

    assert discogs.Account(lib).collection_items(9) == []


def test_other_errors_reading_a_releases_copies_are_raised(library):
    lib = library({"/users/me/collection/releases/9": HTTPError("Boom", 500)})

    with pytest.raises(discogs.ApiError):
        discogs.Account(lib).collection_items(9)


def test_adding_posts_to_the_folder_and_gives_the_instance(library):
    lib = library(
        {("POST", "/users/me/collection/folders/2/releases/9"): {"instance_id": 55}}
    )

    instance_id = discogs.Account(lib).add_to_collection(2, 9)

    assert instance_id == 55
    assert ("POST", "/users/me/collection/folders/2/releases/9", None) in lib.requests


def test_a_custom_field_is_posted_as_a_value(library):
    lib = library()

    discogs.Account(lib).set_collection_field(2, 9, 55, 3, "ex-radio")

    assert (
        "POST",
        "/users/me/collection/folders/2/releases/9/instances/55/fields/3",
        {"value": "ex-radio"},
    ) in lib.requests


def test_deleting_names_the_folder_release_and_instance(library):
    lib = library()

    discogs.Account(lib).delete_collection_instance(7, 1, 1002)

    assert (
        "DELETE",
        "/users/me/collection/folders/7/releases/1/instances/1002",
        None,
    ) in lib.requests


# --- The Marketplace ---


def test_the_whole_inventory_asks_for_every_status_100_at_a_time(library):
    items = [{"id": i} for i in range(1, 251)]
    lib = library({"/users/me/inventory": paged("listings", items)})

    got = discogs.Account(lib).inventory()

    assert got == items
    gets = [q for m, p, q in lib.requests if m == "GET"]
    assert [q["page"] for q in gets] == ["1", "2", "3"]
    assert all(q["status"] == "All" and q["per_page"] == "100" for q in gets)


def test_an_inventory_that_came_back_short_is_an_error(library):
    items = [{"id": i} for i in range(1, 6)]
    lib = library({"/users/me/inventory": paged("listings", items, claimed=7)})

    with pytest.raises(discogs.IncompleteRead, match="7 items but 5"):
        discogs.Account(lib).inventory()


def test_an_empty_inventory_is_empty(library):
    lib = library({"/users/me/inventory": paged("listings", [])})

    assert discogs.Account(lib).inventory() == []


def test_the_newest_items_are_one_page_sorted_by_listed_date(library):
    lib = library({"/users/me/inventory": {"listings": [{"id": 1}, {"id": 2}]}})

    got = discogs.Account(lib).newest_inventory(100)

    assert got == [{"id": 1}, {"id": 2}]
    ((_, _, query),) = [r for r in lib.requests if r[0] == "GET"]
    assert query == {
        "status": "All",
        "sort": "listed",
        "sort_order": "desc",
        "per_page": "100",
        "page": "1",
    }


def test_creating_a_listing_posts_the_fields_and_gives_discogs_answer(library):
    answer = {"listing_id": 7001, "resource_url": BASE + "/marketplace/listings/7001"}
    lib = library({("POST", "/marketplace/listings"): answer})
    fields = {"release_id": 1, "condition": "Mint (M)", "price": 12.5}

    assert discogs.Account(lib).create_listing(fields) == answer
    assert ("POST", "/marketplace/listings", fields) in lib.requests


def test_a_refused_create_is_an_api_error(library):
    lib = library({("POST", "/marketplace/listings"): HTTPError("Bad condition", 422)})

    with pytest.raises(discogs.ApiError) as caught:
        discogs.Account(lib).create_listing({})

    assert caught.value.refused


def test_reading_and_editing_a_listing(library):
    lib = library({"/marketplace/listings/7001": {"id": 7001, "status": "Draft"}})
    account = discogs.Account(lib)

    assert account.listing(7001) == {"id": 7001, "status": "Draft"}
    account.update_listing(7001, {"status": "For Sale"})

    assert (
        "POST",
        "/marketplace/listings/7001",
        {"status": "For Sale"},
    ) in lib.requests


# --- Timestamps ---


def test_discogs_timestamps_keep_their_offset():
    got = discogs.parse_timestamp("2026-10-01T09:30:00-07:00")

    assert got == datetime.datetime(
        2026, 10, 1, 9, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=-7))
    )
