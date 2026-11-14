"""
A stand-in for fbc/discogs.py's calls, for the tests of everything that uses
Discogs. Nothing here reaches Discogs, and nothing here fakes the library:
only tests/test_discogs_seam.py does that, to check what fbc.discogs sends.

    fake = FakeDiscogs(releases={9: record}).install(monkeypatch)
    fake.account.items = [collection_item(9, instance_id=5)]

Every call is recorded in `calls` as (name, args, kwargs). To make a call fail,
give `fail` a function of (name, args, kwargs) that returns an exception to
raise, or None.
"""

from fbc import discogs


def api_error(status_code, message="Discogs said no"):
    return discogs.ApiError(message, status_code)


def failing(name, error):
    """A `fail` function: calls named `name` raise `error`."""
    return lambda called, args, kwargs: error if called == name else None


def collection_item(
    release_id,
    instance_id,
    folder_id=1,
    title="Album",
    year=1978,
    artists=((5, "Band"),),
    genres=("Rock",),
    styles=("Prog",),
    formats=({"name": "Vinyl", "qty": "1", "descriptions": ["LP"]},),
    labels=({"name": "Label", "catno": "L-1"},),
    master_id=0,
    notes=None,
    date_added="2024-03-05T10:00:00-08:00",
):
    """One item of the collection, as Discogs's JSON has it."""
    item = {
        "id": release_id,
        "instance_id": instance_id,
        "folder_id": folder_id,
        "date_added": date_added,
        "basic_information": {
            "id": release_id,
            "title": title,
            "year": year,
            "artists": [{"id": i, "name": n} for i, n in artists],
            "genres": list(genres),
            "styles": list(styles),
            "formats": [dict(f) for f in formats],
            "labels": [dict(lb) for lb in labels],
            "master_id": master_id,
        },
    }
    if notes is not None:
        item["notes"] = notes
    return item


class _Recording:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        error = self.fail and self.fail(name, args, kwargs)
        if error:
            raise error

    def names(self):
        return [name for name, _, _ in self.calls]

    def calls_to(self, name):
        """The positional arguments of each call to `name`."""
        return [args for called, args, _ in self.calls if called == name]


class FakeAccount(_Recording):
    """fbc.discogs.Account, with the account's contents as attributes."""

    def __init__(
        self,
        folders=(),
        fields=(),
        items=(),
        inventory=(),
        next_instance_id=555,
        fail=None,
    ):
        super().__init__(fail)
        self.folders = list(folders)
        self.fields = list(fields)
        self.items = list(items)
        self.inventory_items = list(inventory)
        self.next_instance_id = next_instance_id
        self.listings = {}
        self.created_listing_id = 7001

    def collection_folders(self):
        self._record("collection_folders")
        return self.folders

    def collection_fields(self):
        self._record("collection_fields")
        return self.fields

    def collection_items(self, release_id=None):
        self._record("collection_items", release_id)
        if release_id is None:
            return list(self.items)
        return [i for i in self.items if i["basic_information"]["id"] == release_id]

    def add_to_collection(self, folder_id, release_id):
        self._record("add_to_collection", folder_id, release_id)
        return self.next_instance_id

    def set_collection_field(self, folder_id, release_id, instance_id, field_id, value):
        self._record(
            "set_collection_field", folder_id, release_id, instance_id, field_id, value
        )

    def delete_collection_instance(self, folder_id, release_id, instance_id):
        self._record("delete_collection_instance", folder_id, release_id, instance_id)
        self.items = [i for i in self.items if i["instance_id"] != instance_id]

    def inventory(self):
        self._record("inventory")
        return list(self.inventory_items)

    def newest_inventory(self, count):
        self._record("newest_inventory", count)
        return list(self.inventory_items)[:count]

    def create_listing(self, fields):
        self._record("create_listing", fields)
        listing_id = self.created_listing_id
        self.created_listing_id += 1
        return {
            "listing_id": listing_id,
            "resource_url": "https://api.discogs.com/marketplace/listings/%d"
            % listing_id,
        }

    def listing(self, listing_id):
        self._record("listing", listing_id)
        return self.listings[listing_id]

    def update_listing(self, listing_id, fields):
        self._record("update_listing", listing_id, fields)


class FakeDiscogs(_Recording):
    """fbc.discogs's functions. `account` is what discogs.account() gives."""

    def __init__(
        self,
        releases=None,
        masters=None,
        hits=(),
        versions=(),
        masters_found=(),
        suggestions=None,
        stats=None,
        account=None,
        fail=None,
    ):
        super().__init__(fail)
        self.releases = {} if releases is None else releases
        self.masters = {} if masters is None else masters
        self.hits = list(hits)
        self.versions = list(versions)
        self.masters_found = list(masters_found)
        self.page_size = 100
        self.suggestions = suggestions
        self.stats = stats
        self.account = account or FakeAccount(fail=fail)

    def install(self, monkeypatch):
        for name in (
            "release",
            "master",
            "master_versions",
            "search",
            "price_suggestions",
            "marketplace_stats",
        ):
            monkeypatch.setattr(discogs, name, getattr(self, name))
        monkeypatch.setattr(discogs, "account", lambda: self.account)
        return self

    def release(self, release_id):
        self._record("release", release_id)
        return self.releases[int(release_id)]

    def master(self, master_id):
        self._record("master", master_id)
        return self.masters[int(master_id)]

    def master_versions(self, master_id, page, **filters):
        self._record("master_versions", master_id, page, **filters)
        # Discogs applies the country filter itself.
        wanted = [
            v
            for v in self.versions
            if "country" not in filters or v["country"] == filters["country"]
        ]
        per = self.page_size
        return {
            "pagination": {"pages": -(-len(wanted) // per) or 1},
            "versions": wanted[(page - 1) * per : page * per],
        }

    def search(self, **params):
        self._record("search", **params)
        if params.get("type") == "master":
            return list(self.masters_found)
        return [
            h if isinstance(h, dict) else {"id": h, "format": ["Vinyl", "LP", "Album"]}
            for h in self.hits
        ]

    def price_suggestions(self, release_id):
        self._record("price_suggestions", release_id)
        return self.suggestions or {}

    def marketplace_stats(self, release_id):
        self._record("marketplace_stats", release_id)
        return self.stats or {}
