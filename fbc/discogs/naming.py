"""Names a Unit gets from a Discogs release: its pressing text, its name and the
parameters Add Unit is opened with. Functions of a release that import nothing from
fbc when the module loads, so models can import them."""

import json


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
    release,
    purchase_lot_id=None,
    matrix="",
    instance_id=None,
    grades=None,
    notes="",
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
    # The grades to start with: record_grade, sleeve_grade, dust_jacket_grade.
    params.update({name: grade for name, grade in (grades or {}).items() if grade})
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
