"""
The Discogs credits fbc keeps for classical and opera records: who composed,
who conducted and which orchestra played. No network.

Discogs lists them inconsistently. Sometimes the composer, conductor and
orchestra are credits with roles ("Conductor", "Composed By", "Orchestra");
sometimes they are all main artists with no roles. So the rule is to trust
role credits, and to read a main artist without a role only when its name
says what it is (an orchestra). Anything else is left out rather than guessed.

DiscogsRelease.credits holds the few credits worth keeping, as JSON text: a
list of {"name": ..., "role": ...}. A main artist's role is "". The list is
empty for a release with no credits and NULL until the release is fetched.
"""

import json
import re

COMPOSER_ROLES = ("composed by", "written-by", "written by", "music by", "composer")
CONDUCTOR_ROLES = ("conductor",)
ORCHESTRA_ROLES = ("orchestra", "ensemble")

# Words that mark a name as an orchestra or ensemble, in the languages
# Discogs sees most.
ORCHESTRA_WORDS = (
    "orchestre",
    "orchester",
    "orquesta",
    "orchestra",
    "philharmonic",
    "philharmoniker",
    "philharmonia",
    "symphony",
    "symphonic",
    "sinfonie",
    "sinfonieorchester",
    "sinfonica",
    "ensemble",
    "kapelle",
)

# Names Discogs uses for "nobody in particular".
NOT_A_PERSON = ("various", "unknown artist", "unknown", "traditional", "anonymous")

CLASSICAL_GENRE = "Classical"
OPERA_STYLE = "Opera"


def role_names(role):
    """
    The lowercase roles in a Discogs role string: "Conductor, Chorus Master
    [Uncredited]" -> ["conductor", "chorus master"]. A note in square
    brackets is dropped; commas inside brackets don't split.
    """
    role = re.sub(r"\[[^\]]*\]", "", role or "")
    return [r.strip().lower() for r in role.split(",") if r.strip()]


def _kinds(role):
    names = role_names(role)
    kinds = set()
    if any(n in COMPOSER_ROLES for n in names):
        kinds.add("composer")
    if any(n in CONDUCTOR_ROLES for n in names):
        kinds.add("conductor")
    if any(n in ORCHESTRA_ROLES for n in names):
        kinds.add("orchestra")
    return kinds


def looks_like_orchestra(name):
    words = re.findall(r"[^\W\d_]+", (name or "").lower())
    return any(w in ORCHESTRA_WORDS for w in words)


def is_classical(release):
    """Whether Discogs files the release under Classical (or as an Opera)."""
    if not release:
        return False
    return any(g.name == CLASSICAL_GENRE for g in release.genres) or any(
        s.name == OPERA_STYLE for s in release.styles
    )


def extract(record):
    """
    The credits to keep from a Discogs release record (the JSON of
    GET /releases/{id}): every main artist, and the credits whose role is a
    composer, conductor or orchestra, including a composer credited on one
    track. Names are as Discogs has them, " (2)" and all.
    """
    found = []

    def add(name, role):
        entry = {"name": name, "role": role}
        if name and entry not in found:
            found.append(entry)

    for a in record.get("artists") or []:
        add(a.get("name"), a.get("role") or "")
    for a in record.get("extraartists") or []:
        if _kinds(a.get("role")):
            add(a.get("name"), a.get("role"))
    for track in record.get("tracklist") or []:
        for a in track.get("extraartists") or []:
            if "composer" in _kinds(a.get("role")):
                add(a.get("name"), a.get("role"))
    return found


def credits_json(record):
    return json.dumps(extract(record))


def load(release):
    """The release's stored credits as a list, [] if none or unreadable."""
    if not release or not release.credits:
        return []
    try:
        credits = json.loads(release.credits)
    except ValueError:
        return []
    return credits if isinstance(credits, list) else []


def people(release, clean_name):
    """
    {"composer": [...], "conductor": [...], "orchestra": [...]}: the cleaned
    names credited in each part, in credit order, without repeats. Role
    credits come first. A main artist with no role counts only as an
    orchestra, and only when its name says so.
    """
    credits = load(release)
    found = {"composer": [], "conductor": [], "orchestra": []}
    for c in credits:
        name = clean_name(c.get("name"))
        if not name or name.lower() in NOT_A_PERSON:
            continue
        for kind in _kinds(c.get("role")):
            found[kind].append(name)
    if not found["orchestra"]:
        for c in credits:
            name = clean_name(c.get("name"))
            if not c.get("role") and looks_like_orchestra(name):
                found["orchestra"].append(name)
    return {kind: list(dict.fromkeys(names)) for kind, names in found.items()}


# Surnames of more than one word, which the last-word rule would cut.
LONG_SURNAMES = ("Vaughan Williams", "Lloyd Webber")

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def surname(name):
    """
    The last name of a person, for a title: the last word, so lowercase
    particles fall away ("Herbert von Karajan" -> "Karajan"), after dropping
    "Jr." or "II" ("Johann Strauss II" -> "Strauss"). Hyphenated names
    ("Rimsky-Korsakov") are one word; LONG_SURNAMES are kept whole
    ("Ralph Vaughan Williams" -> "Vaughan Williams").
    """
    words = (name or "").split()
    while len(words) > 1 and words[-1].lower() in SUFFIXES:
        words.pop()
    if not words:
        return ""
    tail = " ".join(words).lower()
    for long in LONG_SURNAMES:
        if tail == long.lower() or tail.endswith(" " + long.lower()):
            return long
    return words[-1]
