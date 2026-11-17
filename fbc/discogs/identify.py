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
  so fbc.discogs.client posts itself). The copy's condition, notes and other custom fields
  are then set one at a time, by POST to .../instances/{instance}/fields/{field}.
  Field ids are the account's own, so they're looked up by name.

fbc writes to the Discogs account only from collect(), which the page reaches
through its own "Add to my Discogs collection" button and form; nothing else
here writes. "Just add the Unit" saves the release in fbc as a Release with no
collection copy.

Everything that reaches Discogs goes through fbc.discogs.client, which refuses to
run in dev mode; search() also checks devmode itself, so the page can say
so plainly. Searches and release fetches are kept for a few minutes.

Saving the chosen release is in saving.py, adding it to the collection in
collect.py, and the dev mode samples in samples.py. Names a release gives a Unit
are in naming.py.

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

import logging
import threading
import time
from difflib import SequenceMatcher
from markupsafe import Markup, escape

from fbc.discogs import client as discogs_client
from fbc.core import devmode

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


# The most releases one record can have marked "not it". It keeps the page's
# address a sane length.
MAX_NOT_IT = 100


def parse_not_it(text):
    """
    The release ids marked "not it", from the page's `not_it` value: numbers
    separated by commas (or spaces), in the order given, without repeats.
    Anything that isn't plain digits is ignored, and only the first
    MAX_NOT_IT count.
    """
    import re

    ids = []
    for word in re.split(r"[,\s]+", text or ""):
        if re.fullmatch(r"[0-9]{1,12}", word) and int(word) not in ids:
            ids.append(int(word))
            if len(ids) == MAX_NOT_IT:
                break
    return ids


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


def _failure(message, **extra):
    return dict({"ok": False, "error": message}, **extra)


def _call(fn, *args, **kwargs):
    """fn(*args, **kwargs), with Discogs's 429 raised as RateLimited."""
    try:
        return fn(*args, **kwargs)
    except discogs_client.ApiError as e:
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
    not_it=(),
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
    Releases in `not_it` (ids the owner ruled out) are left out before ranking,
    and before any is checked, so the ones shown are all candidates and the
    offsets count only what is left. "hidden" is how many it left out.
    """
    if devmode.on():
        return _failure("Not available in dev mode.", dev_mode=True)
    excluded = frozenset(not_it or ())
    if (release_id or "").strip():
        return _one_release(release_id, country, matrix, color, excluded)
    type_word = dict(TYPES).get(vinyl_type, "LP")
    if (master or "").strip():
        return _master_search(
            master, country, matrix, color, type_word, reissue, offset, excluded
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
            return _call(discogs_client.search, **query)

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
        except discogs_client.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure(
                "Discogs says too many requests; wait a minute and try again."
            )
        except discogs_client.ApiError as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs search failed")
            return _failure("Couldn't reach Discogs: " + str(e))
        hits = [h for h in hits if keep_hit(h, type_word, reissue)]
        hidden = len([h for h in hits if h.get("id") in excluded])
        hits = [h for h in hits if h.get("id") not in excluded]
        limit = MAX_CANDIDATES_WITH_MATRIX if matrix_text else MAX_CANDIDATES
        candidates, note, used = _check(hits[:limit])
        calls += used
        return {
            "ok": True,
            "candidates": candidates,
            "searched": calls,
            "note": note,
            "total": len(hits),
            "hidden": hidden,
        }

    key = (
        "search",
        tuple(sorted(params.items())),
        type_word,
        reissue,
        matrix_text,
        tuple(sorted(excluded)),
    )
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
            hits = _call(discogs_client.search, **params)
        except discogs_client.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure("Discogs says too many requests; try in a minute.")
        except discogs_client.ApiError as e:
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


def _master_versions(master_id, country, type_word, reissue, need, excluded=()):
    """
    The master's versions that are the wanted vinyl and not in `excluded`, as
    search-hit-like dicts, fetching pages until there are `need` of them or
    none are left: (hits, calls made, whether pages were left unread at the
    page limit, how many were left out as excluded). Excluded ones don't count
    toward `need`, so a page isn't short because of them.
    """
    params = {"sort": "released", "format": "Vinyl"}
    if (country or "").strip():
        params["country"] = country.strip()
    hits, calls, page, pages, hidden = [], 0, 0, 1, 0
    while page < pages and page < MAX_VERSION_PAGES and len(hits) < need:
        page += 1
        calls += 1
        body = _call(discogs_client.master_versions, master_id, page, **params)
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
                if hit["id"] in excluded:
                    hidden += 1
                else:
                    hits.append(hit)
    return hits, calls, page < pages and len(hits) < need, hidden


def _master_search(
    master, country, matrix, color, type_word, reissue, offset, excluded=frozenset()
):
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
            hits, calls, truncated, hidden = _master_versions(
                master_id, country, type_word, reissue, offset + reach + 1, excluded
            )
        except discogs_client.DiscogsAuthError as e:
            return _failure(str(e))
        except RateLimited:
            return _failure("Discogs says too many requests; try in a minute.")
        except discogs_client.ApiError as e:
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
            "hidden": hidden,
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
        tuple(sorted(excluded)),
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


def _one_release(text, country, matrix, color, excluded=frozenset()):
    """search() for a release id: that one release, ranked like any other."""
    rid = release_id_from(text)
    if not rid:
        return _failure(
            "That isn't a release id. Use the number, [r123456] or the"
            " release's address from Discogs (not a master's)."
        )
    if rid in excluded:
        return {
            "ok": True,
            "candidates": [],
            "searched": 0,
            "note": None,
            "total": 0,
            "hidden": 1,
        }
    try:
        record = _release_record(rid)
    except discogs_client.DiscogsAuthError as e:
        return _failure(str(e))
    except RateLimited:
        return _failure("Discogs says too many requests; wait a minute and try again.")
    except discogs_client.ApiError as e:
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
        "hidden": 0,
    }


def _release_record(release_id):
    """One release's full record, kept for a few minutes."""
    return _cached(
        ("release", int(release_id)),
        lambda: _call(discogs_client.release, release_id),
    )
