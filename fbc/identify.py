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
  one or two searches (the second is Discogs's text search for the matrix) plus
  up to MAX_CANDIDATES release fetches. When Discogs says 429 (too
  many requests) the candidates not yet fetched are listed unranked, with a
  note. The client library doesn't retry or throttle itself.
- A release is added to the collection by POST to
  /users/{name}/collection/folders/{folder}/releases/{id}. Discogs answers
  with the new copy's instance_id (the library's add_release throws it away,
  so fbc.discogs posts itself). The copy's condition, notes and other custom fields
  are then set one at a time, by POST to .../instances/{instance}/fields/{field}.
  Field ids are the account's own, so they're looked up by name.

fbc writes to the Discogs account only from collect(), which the page reaches
through its own "Add to my Discogs collection" button and form; nothing else
here writes. "Just add the Unit" saves the release in fbc as a Release with no
collection copy.

Everything that reaches Discogs goes through fbc.discogs, which refuses to
run in dev mode; search() also checks devmode itself, so the page can say
so plainly. Searches and release fetches are kept for a few minutes.

How a matrix is scored: both sides are put in capitals with everything but
letters and digits removed ("A-1 / ST-12345 " is "A1ST12345").
- Exact: it equals a matrix listed on the release.
- Partial: the longest run of characters the two share is at least 4 long and
  covers at least 60% of the shorter one. Partial scores between 30 and 89
  by how much of the shorter one is covered.
- No matrix listed: the release has none, so it can't be ruled in or out;
  look at its pictures. Scores 0.
- Mismatch: it lists matrices and none is close. Scores -30.
Exact scores 100. A matching country adds 30 and a matching colour 10.
Higher scores come first.
"""

import datetime
import json
import logging
import threading
import time
from difflib import SequenceMatcher

from markupsafe import Markup, escape

from fbc import devmode, discogs

logger = logging.getLogger()

CACHE_SECONDS = 300
MAX_CANDIDATES = 12
# More when a matrix is given: it's what picks the release, so it's worth the calls.
MAX_CANDIDATES_WITH_MATRIX = 20
DEFAULT_COUNTRY = "US"
# Discogs spells the country out in releases; the search filter takes either.
COUNTRY_NAMES = {"US": "US", "USA": "US", "UNITED STATES": "US", "UK": "UK"}

MIN_PARTIAL_RUN = 4
MIN_PARTIAL_COVER = 0.6
EXACT_SCORE = 100
COUNTRY_SCORE = 30
COLOR_SCORE = 10
# What the Type field offers: (label, the word Discogs lists in a release's
# format descriptions, or None for any vinyl). The first is the default.
TYPES = [("LP", "LP"), ('7" (45 RPM)', '7"'), ('12"', '12"'), ("Any vinyl", None)]
DEFAULT_TYPE = "LP"
# Reissue: blank is either, so a record that might be either isn't hidden.
REISSUE_CHOICES = [("", "Either"), ("yes", "Reissue"), ("no", "Not a reissue")]
MISMATCH_SCORE = -30

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
        return PARTIAL, 30 + int(cover * 60), text, span
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


def _call(fn, *args, **kwargs):
    """fn(*args, **kwargs), with Discogs's 429 raised as RateLimited."""
    try:
        return fn(*args, **kwargs)
    except discogs.ApiError as e:
        if e.rate_limited:
            raise RateLimited() from e
        raise


def _summary(release, result=None):
    """What the page shows about a release, from its record or a search hit."""
    result = result or {}
    formats = release.get("formats") or []
    labels = release.get("labels") or []
    # Every picture: a small one to show and the full-size one to pop up.
    images = [
        {
            "thumb": i.get("uri150") or i.get("uri"),
            "full": i.get("uri") or i.get("uri150"),
        }
        for i in release.get("images") or []
        if i.get("uri150") or i.get("uri")
    ]
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
        "thumb": result.get("thumb") or (images[0]["thumb"] if images else None),
        "images": images,
        "notes": (release.get("notes") or "").strip(),
        "identifiers": [
            {
                "type": i.get("type") or "",
                "value": i.get("value") or "",
                "description": i.get("description") or "",
            }
            for i in release.get("identifiers") or []
            if i.get("value")
        ],
        "matrices": listed_matrices(release),
        "url": release.get("uri")
        or "https://www.discogs.com/release/"
        + str(release.get("id") or result.get("id")),
    }


def _check(hits):
    """
    Fetch each hit's release record, for its matrix and pictures: (candidates,
    note, calls made). A hit that can't be fetched, or comes after Discogs's
    rate limit stops us, is kept unchecked.
    """
    candidates, note, calls = [], None, 0
    for hit in hits:
        if not hit.get("id"):
            continue
        unchecked = False
        try:
            calls += 1
            release = _release_record(hit["id"])
        except RateLimited:
            note = (
                "Discogs's rate limit stopped the matrix check, so some"
                " releases below are unchecked. Try again in a minute."
            )
            release, unchecked = {}, True
        except Exception as e:
            logger.warning("Couldn't fetch release %s: %s", hit.get("id"), e)
            release, unchecked = {}, True
        candidate = _summary(release, hit)
        candidate["unchecked"] = unchecked
        candidate["found_by_matrix"] = bool(hit.get("found_by_matrix"))
        candidates.append(candidate)
    return candidates, note, calls


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
    release_id="",
    master="",
    offset=0,
):
    """
    Look for releases on Discogs and rank them: {"ok": True, "candidates",
    "searched", "note"} or {"ok": False, "error", "dev_mode"}. "searched" is
    how many Discogs calls it took. Only vinyl is searched; vinyl_type is a
    label from TYPES, and reissue "yes" or "no" filters on Discogs's Reissue
    format description. color only ranks. "note" is set when Discogs's rate limit
    stopped the matrix check part way. A release_id (a number, [r123] or a
    release's address) overrides everything else: just that release. A master
    (a number, [m123] or its address) replaces artist, title, catalogue number
    and barcode: its versions are listed, filtered by country, type and
    reissue, and `offset` skips the ones an earlier call already checked.
    """
    if devmode.on():
        return _failure("Not available in dev mode.", dev_mode=True)
    if (release_id or "").strip():
        return _one_release(release_id, country, matrix, color)
    type_word = dict(TYPES).get(vinyl_type, "LP")
    if (master or "").strip():
        return _master_search(
            master, country, matrix, color, type_word, reissue, offset
        )
    params = {"type": "release", "per_page": 50}
    for key, value in (
        ("artist", artist),
        ("release_title", title),
        ("catno", catno),
        ("barcode", barcode),
    ):
        if (value or "").strip():
            params[key] = value.strip()
    matrix_text = (matrix or "").strip()
    has_fields = len(params) > 2
    if not has_fields and not matrix_text:
        return _failure("Give an artist, title, catalogue number, barcode or matrix.")
    # Both are narrowed again on the hits, which carry their formats.
    params["format"] = "Vinyl"
    # Searching with the country keeps other pressings from filling the page.
    # Matching countries are ranked first either way, so a wrong country filter
    # only costs the other countries' releases; leaving it blank searches all.
    if (country or "").strip():
        params["country"] = country.strip()

    def fetch():
        calls = 0

        def run(query):
            nonlocal calls
            calls += 1
            return _call(discogs.search, **query)

        try:
            hits = []
            if matrix_text:
                # Discogs's own text search may know the matrix, though its API
                # documents no field for it. Anything it finds goes first, so
                # the 20 releases checked are the likeliest.
                found_by_matrix = run(dict(params, q=matrix_text))
                hits += [dict(h, found_by_matrix=True) for h in found_by_matrix]
            if has_fields:
                seen = {h.get("id") for h in hits}
                hits += [h for h in run(params) if h.get("id") not in seen]
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure(
                "Discogs says too many requests; wait a minute and try again."
            )
        except discogs.ApiError as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs search failed")
            return _failure("Couldn't reach Discogs: " + str(e))
        hits = [h for h in hits if keep_hit(h, type_word, reissue)]
        limit = MAX_CANDIDATES_WITH_MATRIX if matrix_text else MAX_CANDIDATES
        candidates, note, used = _check(hits[:limit])
        calls += used
        return {
            "ok": True,
            "candidates": candidates,
            "searched": calls,
            "note": note,
            "total": len(hits),
        }

    key = ("search", tuple(sorted(params.items())), type_word, reissue, matrix_text)
    found = _cached(key, fetch)
    if not found["ok"]:
        # Failures aren't kept, so fixing the cause and retrying works at once.
        with _lock:
            _cache.pop(key, None)
        return found
    # Ranking is cheap, so the colour and country aren't in the key.
    return dict(found, candidates=rank(found["candidates"], country, matrix, color))


# A master's versions come 100 to a page; a page or two covers most masters'
# vinyl in one country, and this stops a huge master eating the rate limit.
MAX_VERSION_PAGES = 5
MASTER_BATCH = 20
# With a matrix, batches are checked one after another until one has an exact
# match, up to this many releases: under Discogs's 60 calls a minute, and short
# enough for a page to load.
MASTER_AUTO_MAX = 40


def master_id_from(text):
    """The master number in "123", "m123", "[m123]" or a master's address
    (".../master/123-Artist-Title"), or None."""
    import re

    text = (text or "").strip()
    m = re.fullmatch(r"\[?m?(\d+)\]?", text, re.I) or re.search(r"/master/(\d+)", text)
    return int(m.group(1)) if m else None


def find_masters(artist="", title=""):
    """
    Masters on Discogs for an artist and title, to fill in the Master field:
    {"ok": True, "masters": [{"id", "title", "year", "thumb", "url"}]} or
    {"ok": False, "error", "dev_mode"}. One Discogs call.
    """
    if devmode.on():
        return _failure("Not available in dev mode.", dev_mode=True)
    params = {"type": "master", "per_page": 15}
    if (artist or "").strip():
        params["artist"] = artist.strip()
    if (title or "").strip():
        params["release_title"] = title.strip()
    if len(params) == 2:
        return _failure("Give an artist or a title to find the master.")

    def fetch():
        try:
            hits = _call(discogs.search, **params)
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure("Discogs says too many requests; try in a minute.")
        except discogs.ApiError as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs master search failed")
            return _failure("Couldn't reach Discogs: " + str(e))
        return {
            "ok": True,
            "masters": [
                {
                    "id": h["id"],
                    "title": h.get("title"),
                    "year": h.get("year"),
                    "thumb": h.get("thumb"),
                    "url": "https://www.discogs.com/master/" + str(h["id"]),
                }
                for h in hits
                if h.get("id")
            ],
        }

    key = ("find-masters", tuple(sorted(params.items())))
    found = _cached(key, fetch)
    if not found["ok"]:
        with _lock:
            _cache.pop(key, None)
    return found


def _master_versions(master_id, country, type_word, reissue, need):
    """
    The master's versions that are the wanted vinyl, as search-hit-like
    dicts, fetching pages until there are `need` of them or none are left:
    (hits, calls made, whether pages were left unread at the page limit).
    """
    params = {"sort": "released", "format": "Vinyl"}
    if (country or "").strip():
        params["country"] = country.strip()
    hits, calls, page, pages = [], 0, 0, 1
    while page < pages and page < MAX_VERSION_PAGES and len(hits) < need:
        page += 1
        calls += 1
        body = _call(discogs.master_versions, master_id, page, **params)
        pages = ((body or {}).get("pagination") or {}).get("pages") or 1
        for v in (body or {}).get("versions") or []:
            formats = list(v.get("major_formats") or []) + [
                f.strip() for f in (v.get("format") or "").split(",") if f.strip()
            ]
            hit = {
                "id": v.get("id"),
                "title": v.get("title"),
                "country": v.get("country"),
                "year": (v.get("released") or "")[:4] or None,
                "format": formats,
                "label": [v["label"]] if v.get("label") else [],
                "catno": v.get("catno"),
                "thumb": v.get("thumb"),
            }
            if hit["id"] and keep_hit(hit, type_word, reissue):
                hits.append(hit)
    return hits, calls, page < pages and len(hits) < need


def _master_search(master, country, matrix, color, type_word, reissue, offset):
    """
    search() for a master: its versions, filtered, checked 20 at a time. With
    a matrix it carries on to the next 20, up to MASTER_AUTO_MAX, until a
    batch has an exact match.
    """
    master_id = master_id_from(master)
    if not master_id:
        return _failure(
            "That isn't a master id. Use the number, [m84990] or the master's"
            " address from Discogs."
        )
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0

    matrix_text = normalise(matrix)
    reach = MASTER_AUTO_MAX if matrix_text else MASTER_BATCH

    def fetch():
        try:
            hits, calls, truncated = _master_versions(
                master_id, country, type_word, reissue, offset + reach + 1
            )
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure("Discogs says too many requests; try in a minute.")
        except discogs.ApiError as e:
            if e.status_code == 404:
                return _failure("Discogs has no master %d." % master_id)
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs master versions failed")
            return _failure("Couldn't reach Discogs: " + str(e))
        candidates, note, used, at = [], None, 0, offset
        while at < len(hits) and at - offset < reach:
            batch = hits[at : min(at + MASTER_BATCH, offset + reach)]
            found_now, note, calls_now = _check(batch)
            candidates += found_now
            used += calls_now
            at += len(batch)
            exact = any(
                match_matrix(matrix, c.get("matrices"))[0] == EXACT for c in found_now
            )
            if note or exact or not matrix_text:
                break
        if truncated and not note:
            note = "Only the first %d versions were looked at." % (
                MAX_VERSION_PAGES * 100
            )
        return {
            "ok": True,
            "candidates": candidates,
            "searched": calls + used,
            "note": note,
            "total": len(hits),
            "master": master_id,
            "next_offset": at if len(hits) > at else None,
        }

    key = (
        "master",
        master_id,
        (country or "").strip(),
        type_word,
        reissue,
        offset,
        matrix_text,
    )
    found = _cached(key, fetch)
    if not found["ok"]:
        with _lock:
            _cache.pop(key, None)
        return found
    return dict(found, candidates=rank(found["candidates"], country, matrix, color))


def release_id_from(text):
    """The release number in "123", "r123", "[r123]" or a release's address
    (".../release/123-Artist-Title"), or None, including for a master's."""
    import re

    text = (text or "").strip()
    m = re.fullmatch(r"\[?r?(\d+)\]?", text, re.I) or re.search(r"/release/(\d+)", text)
    return int(m.group(1)) if m else None


def _one_release(text, country, matrix, color):
    """search() for a release id: that one release, ranked like any other."""
    rid = release_id_from(text)
    if not rid:
        return _failure(
            "That isn't a release id. Use the number, [r123456] or the"
            " release's address from Discogs (not a master's)."
        )
    try:
        record = _release_record(rid)
    except discogs.DiscogsAuthError as e:
        return _failure(str(e))
    except RateLimited:
        return _failure("Discogs says too many requests; wait a minute and try again.")
    except discogs.ApiError as e:
        if e.status_code == 404:
            return _failure("Discogs has no release " + str(rid) + ".")
        return _failure("Discogs said no: " + str(e))
    except Exception as e:
        logger.exception("Discogs release lookup failed")
        return _failure("Couldn't reach Discogs: " + str(e))
    return {
        "ok": True,
        "candidates": rank([_summary(record)], country, matrix, color),
        "searched": 1,
        "note": None,
        "total": 1,
    }


def _release_record(release_id):
    """One release's full record, kept for a few minutes."""
    return _cached(
        ("release", int(release_id)),
        lambda: _call(discogs.release, release_id),
    )


# --- Picking ---------------------------------------------------------------


def save_release(release_id):
    """
    Save a release found by search as a Release in fbc, without a copy in the
    Discogs collection (Sync Discogs adds the copy later if the user puts one
    in the collection). Returns (DiscogsRelease, None) or (None, why not).
    Reads from Discogs only.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsRelease

    if devmode.on():
        return None, devmode.message("Discogs")
    existing = db.session.get(DiscogsRelease, int(release_id))
    if existing:
        return existing, None
    try:
        record = _release_record(release_id)
    except discogs.DiscogsAuthError as e:
        return None, str(e)
    except RateLimited:
        return None, "Discogs says too many requests; wait a minute and try again."
    except discogs.ApiError as e:
        return None, "Discogs said no: " + str(e)
    except Exception as e:
        logger.exception("Couldn't fetch release %s", release_id)
        return None, "Couldn't reach Discogs: " + str(e)
    return save_record(record, _master_year(record)), None


def _master_year(record):
    """The master's year, or None (the release's year is then used)."""
    master_id = record.get("master_id")
    if not master_id:
        return None
    try:
        master = _cached(
            ("master", int(master_id)),
            lambda: _call(discogs.master, master_id),
        )
        return master.get("year") or None
    except Exception as e:
        logger.warning("Couldn't fetch master %s: %s", master_id, e)
        return None


def save_record(record, master_year=None):
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
        master_year=master_year or record.get("year"),
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


# Discogs spells countries out. These are the ones a shop sees most; any other
# is used as Discogs spells it.
COUNTRY_CODES = {
    "US": "US", "UK": "UK", "Canada": "CA", "Germany": "DE", "Japan": "JP",
    "France": "FR", "Netherlands": "NL", "Italy": "IT", "Australia": "AU",
    "Europe": "EU", "Sweden": "SE", "Spain": "ES", "Brazil": "BR",
    "Mexico": "MX", "Ireland": "IE", "Belgium": "BE", "Norway": "NO",
    "Denmark": "DK", "Finland": "FI", "New Zealand": "NZ", "Jamaica": "JM",
    "Argentina": "AR", "Russia": "RU", "Portugal": "PT", "Greece": "GR",
}  # fmt: skip


def pressing_for(release):
    """
    The Unit's Pressing from a release: "US 1970 Original" or "UK 1990
    Reissue". Reissue if its formats say Reissue or Repress, else Original.
    The country is left out if there isn't one, and the year too.
    """
    try:
        formats = json.loads(release.formats or "[]")
    except ValueError:
        formats = []
    words = {d for f in formats for d in (f.get("descriptions") or [])}
    kind = "Reissue" if words & {"Reissue", "Repress"} else "Original"
    country = release.country or ""
    parts = [COUNTRY_CODES.get(country, country), release.year, kind]
    return " ".join(str(p) for p in parts if p)


def unit_name(release):
    """ "Artist - Title (master year)": the master's year, else the release's."""
    year = release.master_year or release.year
    return "%s - %s (%s)" % (release.artist_rep(), release.title, year)


def unit_add_params(
    release, purchase_lot_id=None, matrix="", instance_id=None, grading="", notes=""
):
    """The Add Unit form's starting values for a release: like
    DiscogsRelease.unit_add, but with no collection instance, and the Purchase
    Lot and matrix the user gave."""
    from fbc.models.inventory import Unit

    master = release.discogs_master_key()
    params = {
        "name": unit_name(release),
        "pressing": pressing_for(release),
        "unit_type": '12" LP',
        "discogs_release": release.release_id,
        "discogs_master": master,
    }
    if instance_id:
        params["discogs_instance"] = instance_id
    if grading:
        params["grading"] = grading
    if (notes or "").strip():
        params["notes"] = notes.strip()
    if purchase_lot_id:
        params["purchase_lot"] = purchase_lot_id
    if (matrix or "").strip():
        params["matrix"] = matrix.strip()
    avg = Unit.average_sold_price(master)
    if avg is not None:
        params["retail_price"] = f"{avg:.2f}"
    return params


# --- Adding to the collection ----------------------------------------------

# The collection fields the form offers, in order. They're matched by name
# against the account's custom fields (Discogs numbers them per account).
COLLECTION_FIELDS = [
    "Media Condition",
    "Sleeve Condition",
    "Notes",
    "Dust Jacket Condition",
    "Extras Included",
]


def _grade(value):
    """ "Near Mint (NM or M-)" -> "NM or M-", as Sync Discogs reads it."""
    import re

    m = re.search(r".*\((.*)\)", value or "")
    return m.group(1) if m else ""


def grading_from(values):
    """The Unit's grading from the Media and Sleeve Condition chosen: "M-/VG+"."""
    media = _grade(values.get("Media Condition"))
    sleeve = _grade(values.get("Sleeve Condition"))
    return "/".join(g for g in (media, sleeve) if g)


def collection_form():
    """
    What the collect form offers, from the account: {"ok": True, "folders":
    [(id, name)], "fields": [{"id", "name", "type", "options"}], "missing":
    [names not set up on the account]} or {"ok": False, "error", "dev_mode"}.
    """
    if devmode.on():
        return _failure("Not available in dev mode.", dev_mode=True)

    def fetch():
        try:
            account = discogs.account()
            folders = account.collection_folders()
            fields = account.collection_fields()
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        except discogs.ApiError as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Couldn't read the collection's folders and fields")
            return _failure("Couldn't reach Discogs: " + str(e))
        by_name = {f["name"].lower(): f for f in fields}
        chosen = [by_name[n.lower()] for n in COLLECTION_FIELDS if n.lower() in by_name]
        return {
            "ok": True,
            # Folder 0 is "All", which can't be added to.
            "folders": [(f["id"], f["name"]) for f in folders if f["id"] != 0],
            "fields": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "type": f.get("type"),
                    "options": f.get("options") or [],
                }
                for f in chosen
            ],
            "missing": [n for n in COLLECTION_FIELDS if n.lower() not in by_name],
        }

    found = _cached(("collection-form",), fetch)
    if not found["ok"]:
        with _lock:
            _cache.pop(("collection-form",), None)
    return found


def collect(release_id, folder_id, values):
    """
    Add a copy of the release to the Discogs collection folder `folder_id`,
    set its custom fields from `values` ({field name: text}, blanks and
    anything a dropdown doesn't offer left out), and save it in fbc like a
    synced one. WRITES TO THE DISCOGS ACCOUNT. Returns (DiscogsRelease,
    instance_id, warnings, None) or (None, None, [], why not). If the copy
    is made but something after fails, the answer says so: the release is
    then in Discogs and Sync Discogs will bring it in.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsRelease
    from fbc.util import add_discogs_release

    if devmode.on():
        return None, None, [], devmode.message("Discogs")
    form = collection_form()
    if not form["ok"]:
        return None, None, [], form["error"]
    if int(folder_id) not in {f[0] for f in form["folders"]}:
        return None, None, [], "That isn't one of your Discogs folders."
    release_id = int(release_id)
    try:
        account = discogs.account()
        instance_id = account.add_to_collection(folder_id, release_id)
    except discogs.DiscogsAuthError as e:
        return None, None, [], str(e)
    except discogs.ApiError as e:
        if e.rate_limited:
            return None, None, [], "Discogs says too many requests; try in a minute."
        return None, None, [], "Discogs said no: " + str(e)
    except Exception as e:
        logger.exception("Couldn't add release %s to the collection", release_id)
        return None, None, [], "Couldn't reach Discogs: " + str(e)
    logger.info("Added release %s to Discogs as instance %s", release_id, instance_id)

    warnings = []
    for field in form["fields"]:
        value = (values.get(field["name"]) or "").strip()
        if not value:
            continue
        if field["type"] == "dropdown" and value not in field["options"]:
            warnings.append(field["name"] + ": Discogs has no choice " + repr(value))
            continue
        try:
            account.set_collection_field(
                folder_id, release_id, instance_id, field["id"], value
            )
        except Exception as e:
            logger.warning("Couldn't set %s: %s", field["name"], e)
            warnings.append(field["name"] + " wasn't set: " + str(e))
    try:
        item = next(
            i
            for i in account.collection_items(release_id)
            if int(i["instance_id"]) == int(instance_id)
        )
        add_discogs_release(item)
    except Exception as e:
        logger.exception("Added to Discogs but couldn't save in fbc")
        db.session.rollback()
        return (
            None,
            instance_id,
            warnings,
            "It was added to your Discogs collection (instance %s), but fbc"
            " couldn't save it (%s). Run Sync Discogs to bring it in."
            % (instance_id, e),
        )
    return db.session.get(DiscogsRelease, release_id), instance_id, warnings, None


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
