"""
Identify Record: find the Discogs release a record in hand is, from its
artist, title, catalogue number or barcode, country and vinyl matrix.

What Discogs's API gives (checked against its docs and python3-discogs-client
2.10's source; nothing here has been run against the real site):

- GET /database/search can filter on artist, title, release_title, label,
  catno, barcode, format, country, year and a free-text q. It cannot filter
  on matrix or runout. A matrix is only in each release's own record, in
  `identifiers` (type "Matrix / Runout"), so ranking by matrix costs one
  GET /releases/{id} per candidate.
- Discogs allows 60 requests a minute when signed in. One identify is one
  search plus up to MAX_CANDIDATES release fetches. When Discogs says 429 (too
  many requests) the candidates not yet fetched are listed unranked, with a
  note. The client library doesn't retry or throttle itself.
- A release can be added to the collection by POST, but the library's
  CollectionFolder.add_release returns nothing, so fbc couldn't tell which
  instance it made. fbc never writes to the Discogs account from here. Picking
  a release saves it in fbc as a Release with no collection copy, which is
  all Add Unit needs.

Everything that reaches Discogs goes through discogs.client(), which refuses
to run in dev mode; search() also checks devmode itself, so the page can say
so plainly. Searches and release fetches are kept for a few minutes.

How a matrix is scored: both sides are put in capitals with everything but
letters and digits removed ("A-1 / ST-12345 " is "A1ST12345").
- Exact: it equals a matrix listed on the release.
- Partial: the longest run of characters the two share is at least 4 long and
  covers at least 60% of the shorter one. Partial scores between 20 and 49
  by how much of the shorter one is covered.
- No matrix listed: the release has none, so it can't be ruled in or out;
  look at its pictures. Scores 0.
- Mismatch: it lists matrices and none is close. Scores -20.
A matching country adds 30. Higher scores come first.
"""

import datetime
import json
import logging
import threading
import time
from difflib import SequenceMatcher

from markupsafe import Markup, escape

from fbc import devmode

logger = logging.getLogger()

CACHE_SECONDS = 300
MAX_CANDIDATES = 12
SEARCH_URL = "https://api.discogs.com/database/search"
RELEASE_URL = "https://api.discogs.com/releases/"
DEFAULT_COUNTRY = "US"
# Discogs spells the country out in releases; the search filter takes either.
COUNTRY_NAMES = {"US": "US", "USA": "US", "UNITED STATES": "US", "UK": "UK"}

MIN_PARTIAL_RUN = 4
MIN_PARTIAL_COVER = 0.6
EXACT_SCORE = 50
COUNTRY_SCORE = 30
COLOR_SCORE = 10
# What the Type field offers: (label, the word Discogs lists in a release's
# format descriptions, or None for any vinyl). The first is the default.
TYPES = [("LP", "LP"), ('7" (45 RPM)', '7"'), ('12"', '12"'), ("Any vinyl", None)]
DEFAULT_TYPE = "LP"
# Reissue: blank is either, so a record that might be either isn't hidden.
REISSUE_CHOICES = [("", "Either"), ("yes", "Reissue"), ("no", "Not a reissue")]
MISMATCH_SCORE = -20

EXACT, PARTIAL, NOT_LISTED, MISMATCH, NOT_ASKED = (
    "exact",
    "partial",
    "not listed",
    "mismatch",
    "not asked",
)
MATRIX_LABELS = {
    EXACT: "Exact matrix match",
    PARTIAL: "Partial matrix match",
    NOT_LISTED: "No matrix listed: check the pictures",
    MISMATCH: "Matrix listed, doesn't match",
    NOT_ASKED: "",
}

_cache = {}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def _cached(key, fetch):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = fetch()
    with _lock:
        _cache[key] = (now + CACHE_SECONDS, value)
    return value


class RateLimited(Exception):
    pass


# --- Matrix matching -------------------------------------------------------


def normalise(text):
    """Capitals, letters and digits only."""
    return "".join(c for c in (text or "").upper() if c.isalnum())


def _longest_run(a, b):
    """(start in a, start in b, length) of the longest run they share."""
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)
    )
    return m.a, m.b, m.size


def match_matrix(wanted, listed):
    """
    How the matrix typed in matches the matrices a release lists:
    (status, score, best listed text or None, (start, end) of the shared run
    in that text's normalised form or None).
    """
    want = normalise(wanted)
    if not want:
        return NOT_ASKED, 0, None, None
    if not listed:
        return NOT_LISTED, 0, None, None
    best = None
    for text in listed:
        have = normalise(text)
        if not have:
            continue
        if have == want:
            return EXACT, EXACT_SCORE, text, (0, len(have))
        _, start, size = _longest_run(want, have)
        cover = size / min(len(want), len(have))
        if size >= MIN_PARTIAL_RUN and cover >= MIN_PARTIAL_COVER:
            if not best or cover > best[0]:
                best = (cover, text, (start, start + size))
    if best:
        cover, text, span = best
        return PARTIAL, 20 + int(cover * 29), text, span
    return MISMATCH, MISMATCH_SCORE, None, None


def highlight(text, span):
    """
    `text` as safe HTML with the part of it that `span` (in its normalised
    form) covers wrapped in <mark>. Punctuation inside the run stays in it.
    """
    if not span:
        return escape(text)
    start, end = span
    out, seen, open_ = [], 0, False
    for char in text:
        if char.isalnum() and seen == start and not open_:
            out.append("<mark>")
            open_ = True
        out.append(str(escape(char)))
        if char.isalnum():
            seen += 1
            if seen == end and open_:
                out.append("</mark>")
                open_ = False
    if open_:
        out.append("</mark>")
    return Markup("".join(out))


def country_matches(wanted, country):
    wanted = (wanted or "").strip().upper()
    if not wanted:
        return False
    wanted = COUNTRY_NAMES.get(wanted, wanted)
    have = COUNTRY_NAMES.get((country or "").strip().upper(), (country or "").upper())
    return wanted == have


def listed_matrices(release):
    """The matrix and runout texts in a Discogs release's identifiers."""
    out = []
    for identifier in release.get("identifiers") or []:
        kind = (identifier.get("type") or "").lower()
        if "matrix" in kind or "runout" in kind:
            if identifier.get("value"):
                out.append(identifier["value"])
    return out


def color_matches(wanted, format_text):
    """The colour typed in is in the release's format text ("Red Translucent")."""
    wanted = " ".join((wanted or "").lower().split())
    return bool(wanted) and wanted in (format_text or "").lower()


def keep_hit(hit, type_word, reissue):
    """
    Whether a search hit is the type of vinyl wanted (type_word, None for any
    vinyl) and, if reissue is "yes" or "no", is or isn't a reissue. Always
    vinyl: the search asks for it, and this checks again.
    """
    formats = hit.get("format") or []
    if "Vinyl" not in formats:
        return False
    if type_word and type_word not in formats:
        return False
    if reissue == "yes":
        return "Reissue" in formats
    if reissue == "no":
        return "Reissue" not in formats
    return True


def rank(candidates, country, matrix, color=""):
    """
    Candidates as {"id", "title", "year", "country", "formats", "labels",
    "catno", "thumb", "images", "matrices"} in best-first order, each given
    "status", "status_label", "score", "matrix_html", "country_match" and
    "color_match" (a matching colour adds 10, a missing one costs nothing).
    """
    ranked = []
    for c in candidates:
        status, score, text, span = match_matrix(matrix, c.get("matrices"))
        c = dict(c)
        c["country_match"] = country_matches(country, c.get("country"))
        c["status"] = status
        c["status_label"] = MATRIX_LABELS[status]
        c["color_match"] = color_matches(color, c.get("format_text"))
        c["score"] = (
            score
            + (COUNTRY_SCORE if c["country_match"] else 0)
            + (COLOR_SCORE if c["color_match"] else 0)
        )
        c["matrix_html"] = [
            highlight(t, span if t == text else None) for t in c.get("matrices") or []
        ]
        ranked.append(c)
    # Stable: Discogs's own order breaks ties.
    ranked.sort(key=lambda c: -c["score"])
    return ranked


# --- Discogs ---------------------------------------------------------------


def _failure(message, **extra):
    return dict({"ok": False, "error": message}, **extra)


def _get(client, url):
    from discogs_client.exceptions import HTTPError

    try:
        return client._get(url)
    except HTTPError as e:
        if getattr(e, "status_code", None) == 429:
            raise RateLimited() from e
        raise


def _summary(release, result=None):
    """What the page shows about a release, from its record or a search hit."""
    result = result or {}
    formats = release.get("formats") or []
    labels = release.get("labels") or []
    images = [i.get("uri150") or i.get("uri") for i in release.get("images") or []]
    return {
        # "Red Translucent", "Clear": where Discogs puts a record's colour.
        "format_text": ", ".join(f["text"] for f in formats if f.get("text")),
        "id": release.get("id") or result.get("id"),
        "title": release.get("title") or result.get("title"),
        "year": release.get("year") or result.get("year"),
        "country": release.get("country") or result.get("country"),
        "formats": [
            ", ".join([f.get("name", "")] + (f.get("descriptions") or [])).strip(", ")
            for f in formats
        ]
        or list(result.get("format") or []),
        "labels": [lb.get("name") for lb in labels] or list(result.get("label") or []),
        "catno": ", ".join(
            sorted({lb.get("catno") for lb in labels if lb.get("catno")})
        )
        or result.get("catno"),
        "thumb": result.get("thumb") or (images[0] if images else None),
        "images": [i for i in images if i],
        "matrices": listed_matrices(release),
        "url": release.get("uri")
        or "https://www.discogs.com/release/"
        + str(release.get("id") or result.get("id")),
    }


def search(
    artist="",
    title="",
    catno="",
    barcode="",
    country="",
    matrix="",
    vinyl_type=DEFAULT_TYPE,
    reissue="",
    color="",
):
    """
    Look for releases on Discogs and rank them: {"ok": True, "candidates",
    "searched", "note"} or {"ok": False, "error", "dev_mode"}. "searched" is
    how many Discogs calls it took. Only vinyl is searched; vinyl_type is a
    label from TYPES, and reissue "yes" or "no" filters on Discogs's Reissue
    format description. color only ranks. "note" is set when Discogs's rate limit
    stopped the matrix check part way.
    """
    from urllib.parse import urlencode

    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    from fbc import discogs

    if devmode.on():
        return _failure("Not available in dev mode.", dev_mode=True)
    type_word = dict(TYPES).get(vinyl_type, "LP")
    params = {"type": "release", "per_page": 25}
    for key, value in (
        ("artist", artist),
        ("release_title", title),
        ("catno", catno),
        ("barcode", barcode),
    ):
        if (value or "").strip():
            params[key] = value.strip()
    # Both are narrowed again on the hits, which carry their formats.
    params["format"] = "Vinyl"
    if len(params) == 3:
        return _failure("Give an artist, title, catalogue number or barcode.")
    # Searching with the country keeps other pressings from filling the page.
    # Matching countries are ranked first either way, so a wrong country filter
    # only costs the other countries' releases; leaving it blank searches all.
    if (country or "").strip():
        params["country"] = country.strip()

    def fetch():
        try:
            client = discogs.client()
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        calls = 0
        try:
            calls += 1
            hits = (_get(client, SEARCH_URL + "?" + urlencode(params)) or {}).get(
                "results"
            ) or []
        except RateLimited:
            return _failure(
                "Discogs says too many requests; wait a minute and try again."
            )
        except (DiscogsAPIError, HTTPError) as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs search failed")
            return _failure("Couldn't reach Discogs: " + str(e))
        candidates, note = [], None
        hits = [h for h in hits if keep_hit(h, type_word, reissue)]
        for hit in hits[:MAX_CANDIDATES]:
            if not hit.get("id"):
                continue
            try:
                calls += 1
                release = _release_record(client, hit["id"])
            except RateLimited:
                note = (
                    "Discogs's rate limit stopped the matrix check, so some"
                    " releases below are unchecked. Try again in a minute."
                )
                candidates.append(_summary({}, hit) | {"unchecked": True})
                continue
            except Exception as e:
                logger.warning("Couldn't fetch release %s: %s", hit.get("id"), e)
                candidates.append(_summary({}, hit) | {"unchecked": True})
                continue
            candidates.append(_summary(release, hit))
        return {
            "ok": True,
            "candidates": candidates,
            "searched": calls,
            "note": note,
            "total": len(hits),
        }

    key = ("search", tuple(sorted(params.items())), type_word, reissue)
    found = _cached(key, fetch)
    if not found["ok"]:
        # Failures aren't kept, so fixing the cause and retrying works at once.
        with _lock:
            _cache.pop(key, None)
        return found
    # Ranking is cheap and depends on the matrix, which isn't in the key.
    return dict(found, candidates=rank(found["candidates"], country, matrix, color))


def _release_record(client, release_id):
    """One release's full record, kept for a few minutes."""
    return _cached(
        ("release", int(release_id)),
        lambda: _get(client, RELEASE_URL + str(int(release_id))),
    )


# --- Picking ---------------------------------------------------------------


def save_release(release_id):
    """
    Save a release found by search as a Release in fbc, without a copy in the
    Discogs collection (Sync Discogs adds the copy later if the user puts one
    in the collection). Returns (DiscogsRelease, None) or (None, why not).
    Reads from Discogs only.
    """
    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    from fbc import db, discogs
    from fbc.models.discogs import DiscogsRelease

    if devmode.on():
        return None, devmode.message("Discogs")
    existing = db.session.get(DiscogsRelease, int(release_id))
    if existing:
        return existing, None
    try:
        record = _release_record(discogs.client(), release_id)
    except discogs.DiscogsAuthError as e:
        return None, str(e)
    except RateLimited:
        return None, "Discogs says too many requests; wait a minute and try again."
    except (DiscogsAPIError, HTTPError) as e:
        return None, "Discogs said no: " + str(e)
    except Exception as e:
        logger.exception("Couldn't fetch release %s", release_id)
        return None, "Couldn't reach Discogs: " + str(e)
    return save_record(record), None


def save_record(record):
    """The DiscogsRelease for a Discogs release record (the API's JSON)."""
    from fbc import db
    from fbc.models.discogs import Artist, DiscogsRelease, Genre, Style

    def get_or_make(model, **find):
        obj = db.session.query(model).filter_by(**find).first()
        if not obj:
            obj = model(**find)
            db.session.add(obj)
        return obj

    artists = []
    for a in record.get("artists") or []:
        artist = db.session.query(Artist).filter_by(artist_id=a["id"]).first()
        if not artist:
            name = a.get("name") or ""
            artist = Artist(
                artist_id=a["id"],
                name=name,
                sort_name=name.removeprefix("The ") + ", The"
                if name.startswith("The ")
                else "",
            )
            db.session.add(artist)
        artists.append(artist)
    release = DiscogsRelease(
        release_id=record["id"],
        title=record.get("title") or "",
        year=record.get("year"),
        artists=artists,
        genres=[get_or_make(Genre, name=g) for g in record.get("genres") or []],
        styles=[get_or_make(Style, name=s) for s in record.get("styles") or []],
        master_id=record.get("master_id") or 0,
        master_year=record.get("year"),
        formats=json.dumps(record.get("formats") or []),
        labels=json.dumps(
            [
                {"name": lb.get("name"), "catno": lb.get("catno")}
                for lb in record.get("labels") or []
            ]
        ),
        country=record.get("country"),
        released=record.get("released"),
        # DiscogsRelease.unit_add reads the notes as JSON.
        notes="[]",
        date_added=datetime.date.today(),
    )
    db.session.add(release)
    db.session.commit()
    return release


def unit_add_params(release, purchase_lot_id=None, matrix=""):
    """The Add Unit form's starting values for a release: like
    DiscogsRelease.unit_add, but with no collection instance, and the Purchase
    Lot and matrix the user gave."""
    from fbc.models.inventory import Unit

    master = release.discogs_master_key()
    params = {
        "name": str(release),
        "unit_type": '12" LP',
        "discogs_release": release.release_id,
        "discogs_master": master,
    }
    if purchase_lot_id:
        params["purchase_lot"] = purchase_lot_id
    if (matrix or "").strip():
        params["matrix"] = matrix.strip()
    avg = Unit.average_sold_price(master)
    if avg is not None:
        params["retail_price"] = f"{avg:.2f}"
    return params


# --- Sample data -----------------------------------------------------------


# For trying the ranking in dev mode, where Discogs isn't reachable.
SAMPLE_CANDIDATES = [
    {
        "id": 1000001,
        "title": "Sample Band - Sample Album",
        "year": 1978,
        "country": "US",
        "formats": ["Vinyl, LP, Album"],
        "labels": ["Sample Records"],
        "catno": "SR-1001",
        "thumb": None,
        "images": [],
        "matrices": ["SR-1001-A-1 RE", "SR-1001-B-2 RE"],
        "format_text": "Red Translucent",
    },
    {
        "id": 1000002,
        "title": "Sample Band - Sample Album",
        "year": 1978,
        "country": "UK",
        "formats": ["Vinyl, LP, Album"],
        "labels": ["Sample Records"],
        "catno": "SR 1001",
        "thumb": None,
        "images": [],
        "matrices": ["SR-1001-A-1 RE", "SR-1001-B-2 RE"],
    },
    {
        "id": 1000003,
        "title": "Sample Band - Sample Album",
        "year": 1984,
        "country": "US",
        "formats": ["Vinyl, LP, Album, Reissue"],
        "labels": ["Sample Records"],
        "catno": "SR-1001",
        "thumb": None,
        "images": [],
        "matrices": ["SR-1001-A-1 RE STERLING", "SR-1001-B-1 RE STERLING"],
    },
    {
        "id": 1000004,
        "title": "Sample Band - Sample Album",
        "year": 1990,
        "country": "US",
        "formats": ["Vinyl, LP, Album, Reissue"],
        "labels": ["Other Records"],
        "catno": "OR-9",
        "thumb": None,
        "images": [],
        "matrices": ["ZZZ 99 X"],
    },
    {
        "id": 1000005,
        "title": "Sample Band - Sample Album",
        "year": 1978,
        "country": "US",
        "formats": ["Vinyl, LP, Album"],
        "labels": ["Sample Records"],
        "catno": "SR-1001",
        "thumb": None,
        "images": [],
        "matrices": [],
    },
]


SAMPLE_IDS = {c["id"] for c in SAMPLE_CANDIDATES}


def sample(country="", matrix="", color=""):
    """Ranking of the sample candidates, shaped like search()'s answer. Dev
    mode only, so it never looks like a real result."""
    if not devmode.on():
        return _failure("The sample data is only for Dev Mode.")
    return {
        "ok": True,
        "candidates": rank(SAMPLE_CANDIDATES, country, matrix, color),
        "searched": 0,
        "note": "Sample data, not from Discogs.",
        "total": len(SAMPLE_CANDIDATES),
        "sample": True,
    }
