"""
Split a Unit's old one-run description into its Record, Sleeve and Dust Jacket
Notes and Extras, for `flask split-descriptions`.

The owner writes it in a habit: the sleeve ("Cover looks good..."), the dust
jacket ("Original paper dust jacket..."), the record ("Disc is in great
shape..."), an optional extra, then "Ultrasonically cleaned." Each part starts
with a sentence that names it, and may go on in more sentences that don't
("Back shows some ringwear."). So a sentence that names its part starts that
part, and one that doesn't goes on with the part before it. Words further into
a sentence ("Records logo", "split") aren't read, since they say little about
which part it is.

It is never guessed past that: a Unit whose first sentence names no part is
left alone and listed, with its text, so it can be fixed by hand.
"""

import re
from html import unescape

# How a sentence starts a part. The dust jacket is found anywhere in the
# sentence, since its name is specific; the others by the sentence's first
# words, since "Cover" or "Disc" later on is usually about something else.
DUST_JACKET = re.compile(
    r"\b(dust\s*jackets?|dust\s*sleeves?|inner\s*sleeves?|inners?)\b", re.I
)
LEADS = (
    (
        "extras",
        r"(includes?|included|comes\s+with|bonus|also\s+(includes?|comes)|with\s+the"
        r"|has\s+an?\s+(insert|poster|booklet)|insert|poster|booklet|obi)",
    ),
    (
        "sleeve_note",
        r"(cover|front\s+cover|sleeve|jacket|gatefold|front|album\s+cover)",
    ),
    (
        "record_note",
        r"(disc|discs|record|records|vinyl|lp|wax|platter|label|plays?|sides?)",
    ),
)
CLEANED = re.compile(r"ultrasonic", re.IGNORECASE)
NOTE_FIELDS = ("record_note", "sleeve_note", "dust_jacket_note", "extras")


def sentences(text):
    """The sentences of `text`, each with its end mark."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _part(sentence):
    """The part this sentence starts, or None if it doesn't name one."""
    if DUST_JACKET.search(sentence):
        return "dust_jacket_note"
    for name, words in LEADS:
        if re.match(r"\W*(the\s+|a\s+|an\s+|both\s+)?" + words + r"\b", sentence, re.I):
            return name
    return None


def split(description):
    """
    Returns `(notes, problem)`. `notes` maps the note fields to their sentences
    (joined with a space when a part runs to several); `problem` is None, or why
    it can't be split.
    """
    parts = {}
    current = None
    # Descriptions made on eBay's old form may hold "&quot;" and the like.
    for sentence in sentences(unescape(description or "")):
        if CLEANED.search(sentence):
            continue
        current = _part(sentence) or current
        if current is None:
            return None, "doesn't say which part it is: " + sentence
        parts.setdefault(current, []).append(sentence)
    if not parts:
        return None, "no sentences left after the cleaning line"
    return {name: " ".join(texts) for name, texts in parts.items()}, None


def split_all(apply=False, echo=print):
    """
    Fill the notes of every Unit that has a description and no notes yet. Without
    `apply` it only says what it would do. Safe to run again: a Unit with any
    note is skipped. Returns counts.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    counts = {"split": 0, "problems": 0, "skipped": 0}
    units = (
        db.session.query(Unit)
        .filter(Unit.description.isnot(None), Unit.description != "")
        .order_by(Unit.id)
        .all()
    )
    for unit in units:
        if any(getattr(unit, f) for f in NOTE_FIELDS):
            counts["skipped"] += 1
            continue
        notes, problem = split(unit.description)
        if problem:
            counts["problems"] += 1
            echo("#" + str(unit.id) + " " + unit.name + ": " + problem)
            echo("    Description: " + unit.description)
            continue
        counts["split"] += 1
        if apply:
            for field, text in notes.items():
                setattr(unit, field, text)
    if apply:
        db.session.commit()
    return counts
