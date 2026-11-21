"""
The eBay description template, and how it is filled in from a Unit.

A template is HTML with a few marks in braces:

    {record_note}                 the value, escaped
    {if sleeve_note}...{end}      what is between is kept only if it has a value
    {if a or b}...{end}           kept if either has a value

Line breaks and the spaces at the start of a line are dropped, so a template can
be laid out to read well; use tags (<br>, <p>, <li>) for the breaks buyers see.
A mistake in a template is found when it is saved, never at listing time.
No network and no code runs: it is only these marks.
"""

import re

from fbc.ebay.describe import escape, grade_name

# What a template can say, and the Unit field behind each. Grades are shown by
# their full name ("Very Good Plus (VG+)").
FIELDS = {
    "record_grade": "The record's grade, in full",
    "record_note": "The Record Note",
    "sleeve_grade": "The sleeve's grade, in full",
    "sleeve_note": "The Sleeve Note",
    "dust_jacket_grade": "The dust jacket's grade, in full",
    "dust_jacket_note": "The Dust Jacket Note",
    "extras": "The Extras",
    "pressing": "The Pressing",
    "unit_type": "The Unit Type",
    "matrix": "The Matrix",
}
GRADES = {"record_grade", "sleeve_grade", "dust_jacket_grade"}

# The line every description ends with. List on Discogs ends its Comments with
# the same words, so they live here once. (A template the owner has edited
# keeps whatever closing line they gave it; Discogs's stays this.)
CLOSING = "All records are ultrasonically cleaned."

DEFAULT = (
    """\
{if sleeve_grade or sleeve_note or dust_jacket_grade or dust_jacket_note or record_grade or record_note}
<h3>Condition</h3>
<ul>
{if sleeve_grade or sleeve_note}
<li><b>Sleeve:</b> {if sleeve_grade}{sleeve_grade}.{end}{if sleeve_note} {sleeve_note}{end}</li>
{end}
{if dust_jacket_grade or dust_jacket_note}
<li><b>Dust Jacket:</b> {if dust_jacket_grade}{dust_jacket_grade}.{end}{if dust_jacket_note} {dust_jacket_note}{end}</li>
{end}
{if record_grade or record_note}
<li><b>Record:</b> {if record_grade}{record_grade}.{end}{if record_note} {record_note}{end}</li>
{end}
</ul>
{end}
{if extras}
<p>{extras}</p>
{end}
{if pressing or unit_type or matrix}
<h3>The record</h3>
<ul>
{if pressing}<li><b>Pressing:</b> {pressing}</li>{end}
{if unit_type}<li><b>Format:</b> {unit_type}</li>{end}
{if matrix}<li><b>Matrix:</b> {matrix}</li>{end}
</ul>
{end}
<p>"""
    + CLOSING
    + """</p>
"""
)

NAME = "ebay"
MARK = re.compile(r"\{([^{}]*)\}")


class TemplateError(ValueError):
    """What is wrong with a template, in words for its owner."""


def _lines(body):
    return "".join(line.strip() for line in body.splitlines())


def parse(body):
    """
    The template as a list of text, ("value", name) and ("if", names, [parts]).
    Raises TemplateError.
    """
    root = []
    stack = [("root", None, root)]
    position = 0
    text = _lines(body)
    for m in MARK.finditer(text):
        stack[-1][2].append(text[position : m.start()])
        position = m.end()
        mark = m.group(1).strip()
        if mark == "end":
            if len(stack) == 1:
                raise TemplateError("There is an {end} with no {if} before it.")
            stack.pop()
        elif mark.startswith("if ") or mark == "if":
            names = [n.strip() for n in mark[2:].split(" or ")]
            for n in names:
                if n not in FIELDS:
                    raise TemplateError(_unknown(n, "{" + mark + "}"))
            parts = []
            stack[-1][2].append(("if", names, parts))
            stack.append(("if", names, parts))
        elif mark in FIELDS:
            stack[-1][2].append(("value", mark))
        else:
            raise TemplateError(_unknown(mark, "{" + mark + "}"))
    stack[-1][2].append(text[position:])
    if len(stack) > 1:
        raise TemplateError("An {if} is never closed with {end}.")
    if "{" in "".join(p for p in root if isinstance(p, str)) or "}" in "".join(
        p for p in root if isinstance(p, str)
    ):
        raise TemplateError("There is a stray { or } that isn't a mark.")
    return root


def _unknown(name, mark):
    return (
        mark
        + " is not something a template knows: "
        + (name or "(nothing)")
        + ". It can use "
        + ", ".join("{" + f + "}" for f in FIELDS)
        + "."
    )


def validate(body):
    """Raise TemplateError if `body` can't be used."""
    if not (body or "").strip():
        raise TemplateError("The template is empty.")
    parse(body)


def values(unit):
    """Each field's text for `unit`, escaped; blank when it has none."""
    result = {}
    for field in FIELDS:
        value = getattr(unit, field, None) or ""
        if field in GRADES:
            value = grade_name(value) or ""
        result[field] = escape(value)
    return result


def _render(parts, fields):
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif part[0] == "value":
            out.append(fields[part[1]])
        elif any(fields[n] for n in part[1]):
            out.append(_render(part[2], fields))
    return "".join(out)


def render(body, unit):
    """`body` filled in from `unit`. Raises TemplateError if it is invalid."""
    return _render(parse(body), values(unit))


def saved():
    """The owner's saved template, or None when they haven't edited it."""
    from fbc import db
    from fbc.models.templates import DescriptionTemplate

    row = db.session.get(DescriptionTemplate, NAME)
    return row.body if row else None


def current():
    """The template new eBay Drafts use."""
    return saved() or DEFAULT
