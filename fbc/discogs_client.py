"""
Everything fbc does with Discogs: signing in, and every call it makes.

This is the only module that imports python3-discogs-client, so moving to
another library means changing this file (and its tests) and nothing else.
tests/test_discogs_seam.py checks that no other module imports it.

Signing in: fbc talks to Discogs as the collection's owner through an OAuth
token from Connect Discogs (Discogs > Connect Discogs), made with the app keys
in DISCOGS_SETTINGS; DISCOGS_SETTINGS["USER_TOKEN"], a personal access token,
is used only until then.

Calls: the module-level functions (release, master, search, ...) and the
Account methods (collection, inventory, listings) take plain values and hand
back the JSON Discogs sent, as dicts and lists. They raise ApiError when
Discogs answers with an error, and DiscogsAuthError when fbc isn't connected
(or in dev mode, which refuses before any request is made). Anything else
(a timeout, a dropped connection) is passed on as it is, and callers treat it
as "couldn't reach Discogs".
"""

import contextlib
import datetime
import logging
from urllib.parse import urlencode

from fbc import devmode

logger = logging.getLogger()


class DiscogsAuthError(Exception):
    pass


class ApiError(Exception):
    """Discogs answered with an error. `status_code` is its HTTP status."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def rate_limited(self):
        return self.status_code == 429

    @property
    def refused(self):
        """Discogs said no (a 4xx other than a rate limit), so nothing was done."""
        status = self.status_code
        return isinstance(status, int) and 400 <= status < 500 and status != 429


class IncompleteRead(Exception):
    """A list wasn't read in full, so nothing may be decided from it."""


@contextlib.contextmanager
def _api_errors():
    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    try:
        yield
    except (DiscogsAPIError, HTTPError) as e:
        raise ApiError(str(e), getattr(e, "status_code", None)) from e


def _settings():
    from fbc import app

    return app.config["DISCOGS_SETTINGS"]


def has_app_keys():
    settings = _settings()
    return bool(settings.get("CONSUMER_KEY") and settings.get("CONSUMER_SECRET"))


def _app_client(token=None, secret=None):
    import discogs_client

    devmode.check(DiscogsAuthError, "Discogs")
    settings = _settings()
    if not has_app_keys():
        raise DiscogsAuthError(
            "Set DISCOGS_SETTINGS['CONSUMER_KEY'] and ['CONSUMER_SECRET'] in"
            " config.py to your Discogs app's keys (discogs.com > Settings >"
            " Developers)."
        )
    return discogs_client.Client(
        settings["USER_AGENT"],
        consumer_key=settings["CONSUMER_KEY"],
        consumer_secret=settings["CONSUMER_SECRET"],
        token=token,
        secret=secret,
    )


def saved_oauth_token():
    """The newest DiscogsOAuthToken saved by Connect Discogs, or None."""
    from fbc import db
    from fbc.models.discogs import DiscogsOAuthToken

    return (
        db.session.query(DiscogsOAuthToken)
        .order_by(DiscogsOAuthToken.id.desc())
        .first()
    )


def client():
    """
    The library's client (used only in this module), signed in as the collection's owner: through the
    token Connect Discogs saved, or DISCOGS_SETTINGS["USER_TOKEN"] before
    there is one. Raises DiscogsAuthError if there's neither.
    """
    import discogs_client

    devmode.check(DiscogsAuthError, "Discogs")
    saved = saved_oauth_token()
    if saved:
        return _app_client(saved.token, saved.secret)
    settings = _settings()
    if settings.get("USER_TOKEN"):
        return discogs_client.Client(
            settings["USER_AGENT"], user_token=settings["USER_TOKEN"]
        )
    raise DiscogsAuthError("Connect fbc to Discogs first (Discogs > Connect Discogs).")


def authorize_url(callback_url):
    """
    Start a sign-in: (request_token, request_secret, url), where url is the
    Discogs page that asks the owner to let fbc at their account. Discogs then
    sends the browser to callback_url with a verifier for exchange_verifier().
    """
    from discogs_client.exceptions import DiscogsAPIError

    d = _app_client()
    try:
        return d.get_authorize_url(callback_url)
    except DiscogsAPIError as e:
        raise DiscogsAuthError("Discogs wouldn't start a sign-in: " + str(e))


def verifier_from(text):
    """
    The (oauth_token, oauth_verifier) in what Discogs sent the browser back
    with: the whole address, just its query string, or the bare verifier.
    oauth_token is None when there isn't one.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    text = (text or "").strip()
    if "oauth_verifier=" in text:
        query = urlparse(text).query if "?" in text else text
        params = parse_qs(query)
        token = (params.get("oauth_token") or [None])[0]
        verifier = (params.get("oauth_verifier") or [None])[0]
        return token, verifier
    return None, (unquote(text) or None)


def exchange_verifier(request_token, request_secret, verifier):
    """
    Swap a sign-in's request token and verifier for an access token and save
    it. Returns the saved DiscogsOAuthToken; raises DiscogsAuthError if
    Discogs says no.
    """
    from discogs_client.exceptions import DiscogsAPIError

    from fbc import db
    from fbc.models.discogs import DiscogsOAuthToken

    d = _app_client(request_token, request_secret)
    try:
        token, secret = d.get_access_token(verifier)
        username = d.identity().username
    except DiscogsAPIError as e:
        raise DiscogsAuthError("Discogs didn't accept the sign-in: " + str(e))
    saved = DiscogsOAuthToken(
        token=token,
        secret=secret,
        username=username,
        created=datetime.datetime.utcnow(),
    )
    db.session.add(saved)
    db.session.commit()
    logger.info("Saved a new Discogs OAuth token for " + str(username))
    return saved


# --- Calls -------------------------------------------------------------------

PAGE_SIZE = 100


def parse_timestamp(text):
    """A datetime from one of Discogs's ISO 8601 timestamps."""
    return datetime.datetime.fromisoformat(text)


def _url(c, path, **params):
    """Discogs's address for `path` (or a full address), with `params` as the query."""
    query = {k: v for k, v in params.items() if v is not None}
    if not path.startswith("http"):
        path = c._base_url + path
    return path + ("?" + urlencode(query) if query else "")


def _get(c, path, **params):
    with _api_errors():
        return c._get(_url(c, path, **params))


def _read_all(c, path, key, **params):
    """
    Every item under `key` of a paged list, read page by page, 100 a page.
    Raises IncompleteRead if fewer came back than Discogs says there are.
    """
    items, total, page = [], None, 1
    while True:
        body = _get(c, path, page=page, per_page=PAGE_SIZE, **params) or {}
        pagination = body.get("pagination") or {}
        if total is None:
            total = pagination.get("items")
        items.extend(body.get(key) or [])
        logger.info("Read %s page %d of %s.", path, page, pagination.get("pages"))
        if page >= (pagination.get("pages") or 1):
            break
        page += 1
    if total is not None and len(items) != total:
        raise IncompleteRead(
            "Discogs says there are %d items but %d came back." % (total, len(items))
        )
    return items


def release(release_id):
    """One release's record: the JSON of GET /releases/{id}."""
    return _get(client(), "/releases/%d" % int(release_id))


def master(master_id):
    """One master's record: the JSON of GET /masters/{id}."""
    return _get(client(), "/masters/%d" % int(master_id))


def master_versions(master_id, page, **filters):
    """
    One page, 100 versions, of a master's versions: the JSON of
    GET /masters/{id}/versions, with its "pagination" and "versions".
    """
    return _get(
        client(),
        "/masters/%d/versions" % int(master_id),
        page=page,
        per_page=PAGE_SIZE,
        **filters,
    )


def search(**params):
    """The hits of GET /database/search for `params`: a list of dicts."""
    return (_get(client(), "/database/search", **params) or {}).get("results") or []


def price_suggestions(release_id):
    """
    Discogs's suggested price per condition: {"Mint (M)": {"value", "currency"},
    ...}. Discogs only gives them to an account with seller settings filled in.
    """
    return _get(client(), "/marketplace/price_suggestions/%d" % int(release_id)) or {}


def marketplace_stats(release_id):
    """The JSON of GET /marketplace/stats/{id}: lowest_price, num_for_sale."""
    return _get(client(), "/marketplace/stats/%d" % int(release_id)) or {}


def account():
    """The collection owner's account on Discogs (no request is made yet)."""
    return Account(client())


class Account:
    """What fbc reads and writes in the owner's collection and Marketplace."""

    def __init__(self, c):
        self._c = c
        self._base = None

    @property
    def base(self):
        """The account's address on Discogs; asking who we are costs a request."""
        if self._base is None:
            with _api_errors():
                self._base = self._c.identity().fetch("resource_url")
        return self._base

    # Collection

    def collection_folders(self):
        """The folders, as dicts with "id" and "name". Folder 0 is "All"."""
        return _get(self._c, self.base + "/collection/folders")["folders"]

    def collection_fields(self):
        """The account's custom fields, as dicts."""
        return _get(self._c, self.base + "/collection/fields")["fields"]

    def collection_items(self, release_id=None):
        """
        The collection's items as the JSON Discogs sent: every copy of every
        release (folder 0), or the copies of one release, which is empty when
        no copy is left. Raises IncompleteRead if some didn't come back.
        """
        if release_id is None:
            return _read_all(
                self._c, self.base + "/collection/folders/0/releases", "releases"
            )
        try:
            return _read_all(
                self._c,
                self.base + "/collection/releases/%d" % int(release_id),
                "releases",
            )
        except ApiError as e:
            # Discogs answers 404 when no copy of the release is left.
            if e.status_code == 404:
                return []
            raise

    def add_to_collection(self, folder_id, release_id):
        """Add a copy of the release to a folder. Returns the new copy's instance_id."""
        path = "/collection/folders/%d/releases/%d" % (int(folder_id), int(release_id))
        with _api_errors():
            added = self._c._post(_url(self._c, self.base + path), None)
        return added["instance_id"]

    def set_collection_field(self, folder_id, release_id, instance_id, field_id, value):
        path = "/collection/folders/%d/releases/%d/instances/%s/fields/%s" % (
            int(folder_id),
            int(release_id),
            instance_id,
            field_id,
        )
        with _api_errors():
            self._c._post(_url(self._c, self.base + path), {"value": value})

    def delete_collection_instance(self, folder_id, release_id, instance_id):
        path = "/collection/folders/%d/releases/%d/instances/%s" % (
            int(folder_id),
            int(release_id),
            instance_id,
        )
        with _api_errors():
            self._c._delete(_url(self._c, self.base + path))

    # Marketplace

    def inventory(self):
        """
        Every item of the seller's inventory, whatever its status, as the JSON
        Discogs sent. (Discogs's inventory shows only For Sale items unless
        asked for another status, so this asks for All: Drafts and Expired
        items are still ours.) Raises IncompleteRead if fewer came back than
        Discogs says there are.
        """
        return _read_all(self._c, self.base + "/inventory", "listings", status="All")

    def newest_inventory(self, count):
        """The newest `count` items of the inventory (up to 100), newest first."""
        body = _get(
            self._c,
            self.base + "/inventory",
            status="All",
            sort="listed",
            sort_order="desc",
            per_page=min(count, PAGE_SIZE),
            page=1,
        )
        return (body or {}).get("listings") or []

    def create_listing(self, fields):
        """
        POST /marketplace/listings with `fields` (release_id, condition,
        sleeve_condition, price, comments, allow_offers, status, external_id,
        location, weight). Returns Discogs's answer, which has "listing_id".
        """
        with _api_errors():
            return self._c._post(_url(self._c, "/marketplace/listings"), fields)

    def listing(self, listing_id):
        """One listing's JSON, as the inventory shows it."""
        return _get(self._c, "/marketplace/listings/%d" % int(listing_id))

    def update_listing(self, listing_id, fields):
        """Edit a listing. Discogs wants release_id, condition and price too."""
        path = "/marketplace/listings/%d" % int(listing_id)
        with _api_errors():
            self._c._post(_url(self._c, path), fields)
