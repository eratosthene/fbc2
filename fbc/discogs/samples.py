"""Sample candidates for Identify Record in dev mode, where nothing may reach
Discogs."""

from fbc.core import devmode
from fbc.discogs import identify


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
        return identify._failure("The sample data is only for Dev Mode.")
    return {
        "ok": True,
        "candidates": identify.rank(SAMPLE_CANDIDATES, country, matrix, color),
        "searched": 0,
        "note": "Sample data, not from Discogs.",
        "total": len(SAMPLE_CANDIDATES),
        "sample": True,
    }
