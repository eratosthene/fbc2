"""
Split a Unit's old one-run description into its Record, Sleeve and Dust Jacket
Notes and Extras, for `flask split-descriptions`.

The owner writes the sentences in a habit: sleeve, dust jacket, record, an
optional extra, then "Ultrasonically cleaned." So a sentence is placed by the
words in it. It is never guessed: a Unit whose sentences don't each fit one
place, or that has two sentences for one place, is left alone and listed so it
can be fixed by hand.
"""

import re

# The dust jacket comes first, since its words are taken out of a sentence
# before the rest are looked for.
PLACES = (
    (
        "dust_jacket_note",
        r"\b(dust\s*jackets?|dust\s*sleeves?|inner\s*sleeves?|inners?)\b",
    ),
    (
        "extras",
        r"\b(inserts?|posters?|booklets?|obi|includes?|included|comes\s+with"
        r"|bonus|stickers?|hype|download|postcards?|lyrics?)\b",
    ),
    ("sleeve_note", r"\b(sleeves?|covers?|jackets?|gatefold|shrink|spine|seams?)\b"),
    (
        "record_note",
        r"\b(records?|discs?|vinyl|lps?|plays?|played|surfaces?|grooves?|scratch\w*"
        r"|wax|sides?|platters?|pops?|crackles?|noise)\b",
    ),
)
CLEANED = re.compile(r"ultrasonic", re.IGNORECASE)
NOTE_FIELDS = ("record_note", "sleeve_note", "dust_jacket_note", "extras")


def sentences(text):
    """The sentences of `text`, each with its end mark."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def split(description):
    """
    Returns `(notes, problem)`. `notes` maps the note fields to a sentence, for
    those the description has; `problem` is None, or why it can't be split.
    """
    notes = {}
    for sentence in sentences(description or ""):
        if CLEANED.search(sentence):
            continue
        # "Dust jacket" holds the word "jacket", which isn't the sleeve.
        rest = re.sub(PLACES[0][1], " ", sentence, flags=re.I)
        places = [name for name, words in PLACES[1:] if re.search(words, rest, re.I)]
        if rest != sentence:
            places.insert(0, PLACES[0][0])
        if len(places) != 1:
            kind = "no place" if not places else "more than one place"
            return None, kind + " for: " + sentence
        if places[0] in notes:
            return None, "two sentences for " + places[0] + ": " + sentence
        notes[places[0]] = sentence
    if not notes:
        return None, "no sentences left after the cleaning line"
    return notes, None


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
