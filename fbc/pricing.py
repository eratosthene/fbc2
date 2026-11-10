"""
Price research for the Add Unit page: what Discogs and eBay say a record is
worth right now, fetched only when the owner asks for it.

What each site really gives (checked against the APIs' docs and the
python3-discogs-client source; nothing here is a sold price from them):

- Discogs: Release.price_suggestions (a suggested price per condition, from
  the seller's own settings), marketplace_stats (the lowest asking price and
  the number for sale) and community (how many have and want it). Its API has
  no sold-price history; that is only on the website, which fbc doesn't scrape.
- eBay: sold prices are only in the Marketplace Insights API, which eBay
  limits to approved partners, and the Finding API's findCompletedItems was
  shut down in February 2025. The Browse API shows active listings, so those
  are asking prices. eBay's own sold-items search is offered as a link.

Everything that reaches Discogs or eBay goes through discogs.client() and
listing.oauth_token(), which refuse to run in dev mode; research() also checks
devmode itself, so the page can say so plainly. Answers are kept for a few
minutes so reloading the page doesn't repeat the calls.
"""

import logging
import statistics
import threading
import time
from urllib.parse import quote_plus

from fbc import devmode

logger = logging.getLogger()

CACHE_SECONDS = 600
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
# eBay's Records category, so a search for an album isn't swamped by posters.
EBAY_RECORDS_CATEGORY = "176985"
EBAY_RESULT_LIMIT = 50
EBAY_SOLD_SEARCH_URL = "https://www.ebay.com/sch/" + EBAY_RECORDS_CATEGORY + "/i.html"

# Best to worst, as python3-discogs-client names them and Discogs spells them.
DISCOGS_CONDITIONS = [
    ("mint", "Mint (M)"),
    ("near_mint", "Near Mint (NM or M-)"),
    ("very_good_plus", "Very Good Plus (VG+)"),
    ("very_good", "Very Good (VG)"),
    ("good_plus", "Good Plus (G+)"),
    ("good", "Good (G)"),
    ("fair", "Fair (F)"),
    ("poor", "Poor (P)"),
]

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
    # Failures aren't kept, so fixing the cause and retrying works at once.
    if value.get("ok"):
        with _lock:
            _cache[key] = (now + CACHE_SECONDS, value)
    return value


def _price(obj):
    """(amount, currency) from a Discogs Price, or None."""
    try:
        if obj is not None and obj.value is not None:
            return round(float(obj.value), 2), obj.currency or "USD"
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _failure(message):
    return {"ok": False, "error": message}


def discogs_research(release_id):
    """
    Discogs asking-price data for one release: {"ok": True, "suggestions":
    [(condition, price, currency)], "suggestions_note", "lowest", "num_for_sale",
    "have", "want", "rating", "genres", "styles", "master_genres",
    "master_styles"} or {"ok": False, "error"}. Genres and styles are Discogs's
    own, for the release and for its master if it has one, to help choose a
    Storage Box.
    """
    from fbc import discogs
    from discogs_client.exceptions import DiscogsAPIError, HTTPError

    if devmode.on():
        return _failure("Not available in Dev Mode.")
    if not release_id:
        return _failure("This Unit has no Discogs release to look up.")

    def fetch():
        try:
            release = discogs.client().release(int(release_id))
            out = {
                "ok": True,
                "suggestions": [],
                "suggestions_note": None,
                "lowest": None,
                "num_for_sale": None,
                "have": None,
                "want": None,
                "rating": None,
                "genres": [],
                "styles": [],
                "master_genres": [],
                "master_styles": [],
            }
            try:
                suggestions = release.price_suggestions
                for attribute, label in DISCOGS_CONDITIONS:
                    price = _price(getattr(suggestions, attribute, None))
                    if price:
                        out["suggestions"].append((label, *price))
                if not out["suggestions"]:
                    out["suggestions_note"] = (
                        "Discogs gave no price suggestions for this release."
                    )
            except (DiscogsAPIError, HTTPError) as e:
                # Needs seller settings on the Discogs account; the rest still works.
                out["suggestions_note"] = (
                    "Discogs wouldn't give price suggestions (" + str(e) + "). They"
                    " need seller settings filled in on the Discogs account."
                )
            out["genres"] = [str(g) for g in getattr(release, "genres", None) or []]
            out["styles"] = [str(g) for g in getattr(release, "styles", None) or []]
            try:
                master = getattr(release, "master", None)
                if master is not None:
                    out["master_genres"] = [str(g) for g in master.genres or []]
                    out["master_styles"] = [str(g) for g in master.styles or []]
            except (DiscogsAPIError, HTTPError):
                # The master is extra; the release's own answer still shows.
                pass
            stats = release.marketplace_stats
            if stats is not None:
                out["lowest"] = _price(stats.lowest_price)
                out["num_for_sale"] = stats.num_for_sale
            community = release.community
            if community is not None:
                out["have"] = community.have
                out["want"] = community.want
                rating = getattr(community, "rating", None)
                if rating is not None and rating.count:
                    out["rating"] = (rating.average, rating.count)
            return out
        except discogs.DiscogsAuthError as e:
            return _failure(str(e))
        except (DiscogsAPIError, HTTPError) as e:
            return _failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Discogs price research failed")
            return _failure("Couldn't reach Discogs: " + str(e))

    return _cached(("discogs", int(release_id)), fetch)


def ebay_sold_search_url(query):
    """eBay's own sold-items search, to open in a browser (signed in to eBay)."""
    return (
        EBAY_SOLD_SEARCH_URL
        + "?_nkw="
        + quote_plus(query)
        + "&LH_Sold=1&LH_Complete=1&_sop=13"
    )


def _summarise(prices):
    return {
        "count": len(prices),
        "low": min(prices),
        "median": round(statistics.median(prices), 2),
        "high": max(prices),
    }


def ebay_research(query):
    """
    Active eBay listings that look like `query`, grouped by condition:
    {"ok": True, "total", "groups": [{"condition", ...summary}], "currency_note"}
    or {"ok": False, "error"}. These are asking prices, not sold prices.
    """
    import requests

    from fbc import listing

    if devmode.on():
        return _failure("Not available in Dev Mode.")
    query = (query or "").strip()
    if not query:
        return _failure("Nothing to search eBay for.")

    def fetch():
        try:
            token = listing.oauth_token(listing.TRADING_SCOPE)
            r = requests.get(
                BROWSE_URL,
                headers={
                    "Authorization": "Bearer " + token,
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                },
                params={
                    "q": query,
                    "category_ids": EBAY_RECORDS_CATEGORY,
                    "filter": "buyingOptions:{FIXED_PRICE},priceCurrency:USD",
                    "limit": EBAY_RESULT_LIMIT,
                },
                timeout=30,
            )
            if r.status_code != 200:
                return _failure("eBay said no: " + "; ".join(listing._rest_errors(r)))
            items = r.json().get("itemSummaries") or []
        except listing.OAuthError as e:
            return _failure(str(e))
        except requests.RequestException as e:
            return _failure("Couldn't reach eBay: " + str(e))
        except ValueError:
            return _failure("eBay's answer wasn't readable.")
        by_condition = {}
        for item in items:
            price = item.get("price") or {}
            try:
                amount = float(price["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if price.get("currency") != "USD":
                continue
            by_condition.setdefault(item.get("condition") or "Not stated", []).append(
                amount
            )
        groups = [
            dict(condition=condition, **_summarise(prices))
            for condition, prices in sorted(by_condition.items())
        ]
        return {"ok": True, "total": sum(g["count"] for g in groups), "groups": groups}

    return _cached(("ebay", query.lower()), fetch)


def research(release_id, master, name):
    """Everything the Add Unit price block shows, as one dict for the template."""
    from fbc.models.inventory import Unit

    own = Unit.average_sold_price(master) if master else None
    return {
        "dev_mode": devmode.on(),
        # A plain link, so it works in dev mode too: nothing here reaches Discogs.
        "discogs_url": "https://www.discogs.com/release/" + str(release_id)
        if release_id
        else None,
        "own_average": own,
        "discogs": discogs_research(release_id),
        "ebay": ebay_research(name),
        "ebay_sold_url": ebay_sold_search_url(name or ""),
    }
