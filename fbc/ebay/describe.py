"""
Turning a Unit (and its Discogs release) into what eBay wants to show: the
item specifics, the 80-character title and the description. No network.

Item specifics are kept as plain text, one per line, in the form

    Name: value | value

so they can be read and edited in an ordinary textarea before publishing.
"""

import json
import re
from html import escape as _escape


from fbc.ebay import credits, vocab
from fbc.ebay.credits import clean_name


def escape(text):
    # Text only, never an attribute, so quotes (12") can stay as they are.
    return _escape(text, quote=False)


def grade_name(abbrev):
    if not abbrev:
        return None
    return vocab.GRADES.get(abbrev, abbrev)


def _format_descriptions(release):
    """Every Discogs format description and free-text note on the release."""
    if not release or not release.formats:
        return [], ""
    try:
        formats = json.loads(release.formats)
    except ValueError:
        return [], ""
    descriptions = []
    text = []
    for f in formats:
        descriptions.extend(f.get("descriptions") or [])
        if f.get("text"):
            text.append(f["text"])
    return descriptions, " ".join(text)


def _labels(release):
    if not release or not release.labels:
        return []
    try:
        return json.loads(release.labels)
    except ValueError:
        return []


def parse_pressing(pressing):
    """
    (Edition values, Country of Origin or None) from Unit.pressing, e.g.
    "US Original" -> (["First Edition", "First Pressing"], "United States").
    Country is the pressing's leading country code (US, UK, JP...) or
    leading capitalized word ("Germany Reissue"), without checking that the
    word really is a country.
    """
    pressing = (pressing or "").strip()
    editions = []
    lowered = pressing.lower()
    if "original" in lowered:
        editions += ["First Edition", "First Pressing"]
    if "reissue" in lowered:
        editions.append("Reissue")
    country = None
    for name in vocab.TWO_WORD_COUNTRIES:
        if lowered.startswith(name.lower()):
            country = name
            break
    if country is None:
        m = re.match(r"[A-Za-z]+", pressing)
        word = m.group(0) if m else ""
        if word in vocab.COUNTRIES:
            # A code ("US", "JP") or adjective ("Japanese"), as written.
            country = vocab.COUNTRIES[word]
        elif (
            len(word) > 2
            and word[0].isupper()
            and not word.isupper()
            and word not in vocab.NOT_COUNTRIES
        ):
            # Any other capitalized word is taken to be a country's name.
            country = word
    return editions, country


def parse_type(unit_type):
    """
    The eBay Type values in Unit.unit_type: '12" Double LP' -> ["LP"],
    '12" 2xLP' -> ["LP"], '12" Maxi-Single' -> ["Maxi-Single"].
    """
    by_name = {t.lower(): t for t in vocab.TYPES}
    found = []
    for token in re.split(r"[\s/,+&]+", unit_type or ""):
        token = re.sub(r"^\d*\s*[x×]", "", token).strip(" ()").lower()
        if token in by_name:
            found.append(by_name[token])
    return list(dict.fromkeys(found))


def record_size(unit):
    """
    `unit`'s record size, one of SIZES: from Discogs' format descriptions,
    else its Unit Type, as a listing's Record Size is, else 12".
    """
    descriptions, _ = _format_descriptions(unit.discogs_release)
    unit_type = unit.unit_type or ""
    for size in vocab.SIZES:
        if size in descriptions:
            return size
    for size in vocab.SIZES:
        if unit_type.startswith(size):
            return size
    return '12"'


def decade(year):
    if not year:
        return None
    return str(year // 10 * 10) + "s"


def build_item_specifics(unit, defaults=None):
    """
    The item specifics for `unit`, as an ordered dict of name -> list of
    values. Anything fbc doesn't know is left out rather than guessed.
    """
    release = unit.discogs_release
    specifics = {}

    def put(name, *values):
        values = [v for v in values if v]
        if values and name not in specifics:
            specifics[name] = list(dict.fromkeys(values))

    if release:
        put("Artist", *[clean_name(a.name) for a in release.artists])
        put("Release Title", release.title)
        if credits.is_classical(release):
            by_part = credits.people(release)
            put("Composer", *by_part["composer"])
            put("Conductor", *by_part["conductor"])
            put("Performer Orchestra", *by_part["orchestra"])
        labels = _labels(release)
        put("Record Label", *[clean_name(label["name"]) for label in labels[:1]])
        catnos = [
            label["catno"]
            for label in labels[:1]
            if label.get("catno") and label["catno"].lower() != "none"
        ]
        put("Catalog Number", *catnos)
        put("MPN", *catnos)
        # A release with no year of its own takes its master's.
        year = release.year
        if not year and release.master_year and release.master_year > 0:
            year = release.master_year
        if year:
            put("Release Year", str(year))
        # Era (and the decade in Style) is when the music came out, so a
        # reissue keeps its original's: the master release's year when
        # Discogs has one.
        original_year = release.year
        if release.master_year and release.master_year > 0:
            original_year = release.master_year
        put("Era", decade(original_year))
        genres = []
        for g in release.genres:
            genres.extend(vocab.GENRES.get(g.name, [g.name]))
        put("Genre", *genres)
        put("Style", decade(original_year), *[s.name for s in release.styles])
    # Where the copy was pressed: Discogs' country for the release, else the
    # Pressing's leading country.
    editions, pressing_country = parse_pressing(unit.pressing)
    discogs_country = release.country if release else None
    put(
        "Country of Origin",
        vocab.COUNTRIES.get(discogs_country, discogs_country) or pressing_country,
    )

    descriptions, text = _format_descriptions(release)
    unit_type = unit.unit_type or ""
    put("Type", *record_types(unit))
    put("Record Size", *[s for s in vocab.SIZES if s in descriptions])
    put("Record Size", *[s for s in vocab.SIZES if unit_type.startswith(s)])
    for d in descriptions:
        m = re.match(r"(\d+)", d)
        if m and "RPM" in d:
            put("Speed", m.group(1) + " RPM")
    if any(t.endswith("LP") for t in specifics.get("Type", [])):
        put("Speed", "33 RPM")
    put("Number of Audio Channels", *[c for c in vocab.CHANNELS if c in descriptions])
    put("Color", *[c for c in vocab.COLORS if c.lower() in text.lower()])

    put("Vinyl Matrix Number", unit.matrix)
    put("Edition", *editions)
    put("Record Grading", grade_name(unit.record_grade))
    put("Sleeve Grading", grade_name(unit.sleeve_grade))
    put("Inlay Condition", grade_name(unit.dust_jacket_grade or unit.sleeve_grade))

    for name, values in (
        defaults if defaults is not None else vocab.DEFAULT_SPECIFICS
    ).items():
        put(name, *values)
    return specifics


def format_specifics(specifics):
    return "\n".join(
        name + ": " + " | ".join(values) for name, values in specifics.items()
    )


def parse_specifics(text):
    """The inverse of format_specifics. Blank and malformed lines are skipped."""
    specifics = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        name = name.strip()
        values = [v.strip() for v in values.split("|") if v.strip()]
        if name and values:
            specifics[name] = values
    return specifics


def classical_head(release):
    """
    "Strauss/Karajan - Also Sprach Zarathustra" for a classical or opera
    release: the composer's and the conductor's surnames. Either is left out
    when Discogs doesn't say, or says several (no way to pick one); with
    neither, the usual artists form. None if the release isn't classical.
    """
    if not credits.is_classical(release):
        return None
    by_part = credits.people(release)
    names = []
    for part in ("composer", "conductor"):
        if len(by_part[part]) == 1:
            names.append(credits.surname(by_part[part][0]))
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return None
    return "/".join(names) + " - " + release.title


def build_title(unit):
    """
    A title in the house style, e.g.
    "Spyro Gyra - Carnaval 1980 First Edition Vinyl LP Fusion Jazz-Funk VG+"
    for a "US Original" pressing. Artists are joined with "/". A reissue
    shows its master's year, then the word Reissue and the release's year:
    "Artist - Album 1960 Vinyl LP Reissue 1975 VG". A classical or opera
    record starts with the composer and conductor instead (classical_head).
    Over 80 characters, it drops the styles, then the release year after
    Reissue, then the edition, then cuts what is left.
    """
    release = unit.discogs_release
    # "First Edition" or "Reissue", as parsed for the Edition specific.
    edition = (parse_pressing(unit.pressing)[0] or [""])[0]
    reissue_year = ""
    if release:
        head = classical_head(release)
        if head is None:
            artist = "/".join(clean_name(a.name) for a in release.artists)
            head = (artist + " - " if artist else "") + release.title
        year = str(release.year) if release.year else ""
        master_year = release.master_year or 0
        if edition == "Reissue" and master_year > 0 and master_year != release.year:
            year = str(master_year)
            reissue_year = str(release.year) if release.year else ""
        styles = [s.name.replace(" ", "-") for s in release.styles]
    else:
        head = unit.name
        year = ""
        styles = []
    kind = (record_types(unit) or [""])[0]
    record = unit.record_grade

    def assemble(edition, reissue_year, styles):
        # "Reissue" goes after the type, the way people search for it; other
        # editions go in front of Vinyl.
        before = after = ""
        if edition == "Reissue":
            after = (edition + " " + reissue_year).strip()
        else:
            before = edition
        parts = [head, year, before, "Vinyl", kind, after] + styles + [record or ""]
        return " ".join(p for p in parts if p)

    while styles and len(assemble(edition, reissue_year, styles)) > vocab.TITLE_MAX:
        styles = styles[:-1]
    if len(assemble(edition, reissue_year, styles)) > vocab.TITLE_MAX:
        reissue_year = ""
    if len(assemble(edition, reissue_year, styles)) > vocab.TITLE_MAX:
        edition = ""
    return assemble(edition, reissue_year, styles)[: vocab.TITLE_MAX].strip()


def build_description(unit):
    """
    The eBay description, from the owner's template (fbc/ebay/template.py)
    filled in with the Unit's grades, notes and details. Only what fbc holds
    about the Unit goes in it; nothing from Discogs. A Unit with no notes or
    extras yet keeps its old Description, as it was, so listings made before
    the notes were filled in are unchanged.
    """
    from fbc.ebay import template

    if not any(
        [unit.record_note, unit.sleeve_note, unit.dust_jacket_note, unit.extras]
    ):
        if unit.description:
            return "<p>" + unit.description + "</p>"
    return template.render(template.current(), unit)


def _discs_in_text(text):
    """A disc count written in free text: "Double", "Triple", "2xLP", "2 x LP"."""
    text = (text or "").lower()
    if "triple" in text:
        return 3
    if "double" in text:
        return 2
    m = re.search(r"\b([2-9])\s*[x×]?\s*lp", text)
    if m:
        return int(m.group(1))
    return None


def disc_count(unit):
    """
    How many records the Unit is: the quantities of its Discogs Vinyl
    formats ("2 x Vinyl, LP"), else a count in its Unit Type ("Double",
    "Triple", "2xLP"), else 1.
    """
    release = unit.discogs_release
    if release and release.formats:
        try:
            formats = json.loads(release.formats)
        except ValueError:
            formats = []
        count = 0
        for f in formats:
            if f.get("name") == "Vinyl":
                try:
                    count += int(f.get("qty") or 1)
                except ValueError:
                    count += 1
        if count:
            return count
    return _discs_in_text(unit.unit_type) or 1


def record_types(unit):
    """
    The Type values for `unit`: from its Unit Type, else Discogs' format
    descriptions, with an LP of 2 or 3 discs (see disc_count) made a
    "Double LP" or "Triple LP".
    """
    descriptions, _ = _format_descriptions(unit.discogs_release)
    types = parse_type(unit.unit_type) or [t for t in vocab.TYPES if t in descriptions]
    multi = vocab.MULTI_DISC_TYPES.get(disc_count(unit))
    if multi and types in ([], ["LP"]):
        return [multi]
    return types
