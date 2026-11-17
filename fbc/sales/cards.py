"""
The home page's cards: each section of the page is a Card with a key, a title,
a template piece (fbc/templates/home/), a function that works out its figures,
and, if a sync fills it, which one. The To do banner above them isn't a card.

A card's figures are only worked out when the card is shown. Figures that
several cards use (the all-time numbers) are worked out once per page, through
Shared, so showing more cards never repeats a query.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

import sqlalchemy as sa

from fbc.ebay import insertions
from fbc.sales import other, tasks

NO_ORDER_NAME = "Other"


class Shared:
    """
    Figures worked out once for one page. `get(fn)` calls `fn(shared)` the first
    time and returns that result every time after, so cards that need the same
    figures share them. One Shared per page; it is not kept between requests.
    """

    def __init__(self):
        self._done = {}

    def get(self, fn):
        if fn not in self._done:
            self._done[fn] = fn(self)
        return self._done[fn]


@dataclass(frozen=True)
class Card:
    """
    `figures(shared)` returns the dict the card's template piece reads as `f`.
    `subtitle(figures)` is small text beside the title. `empty(figures)` is
    True when there is no data yet: the card then says so, and links to
    `sync` (a view endpoint, such as "MyIndexView.syncebaylistings") when
    running it would fill the card; `sync_label` and `sync_why` finish the
    sentence "Run <sync_label> <sync_why>." A card with `hide_when_empty`
    shows nothing at all instead.
    """

    key: str
    title: str
    template: str
    figures: Callable[[Shared], dict]
    subtitle: Optional[Callable[[dict], Any]] = None
    empty: Optional[Callable[[dict], bool]] = None
    sync: Optional[str] = None
    sync_label: str = ""
    sync_why: str = ""
    hide_when_empty: bool = False


@dataclass(frozen=True)
class ShownCard:
    """A card with its figures worked out, ready for the template."""

    card: Card
    figures: dict

    @property
    def is_empty(self):
        return bool(self.card.empty and self.card.empty(self.figures))

    @property
    def subtitle(self):
        return self.card.subtitle(self.figures) if self.card.subtitle else None

    @property
    def visible(self):
        return not (self.is_empty and self.card.hide_when_empty)


def marketplace_sales():
    """
    [{"name", "receipts", "gross", "net", "no_order"}] for eBay, Discogs and
    receipts with no order (no_order is True for that one), from the Sales
    Receipts, except Other Items' (see other.sales). Discogs's net is its total less
    its fee unless corrected by hand: its API has no payment processing fee.
    """
    from fbc import db
    from fbc.models import other as other_models
    from fbc.models.sales import SalesReceipt

    # eBay and Discogs are always listed, even at zero, so a marketplace
    # with no sales yet is still there. The row for receipts with no order
    # is only listed when there are some.
    rows = {name: [0, 0.0, 0.0] for name in ("eBay", "Discogs", "")}
    # Other Items' sales have a line of their own, so they aren't in these.
    for receipt in (
        db.session.query(SalesReceipt)
        .filter(SalesReceipt.id.notin_(other_models.receipt_ids()))
        .options(
            sa.orm.selectinload(SalesReceipt.ebay_orders),
            sa.orm.selectinload(SalesReceipt.discogs_orders),
        )
    ):
        row = rows[receipt.marketplace()]
        row[0] += 1
        row[1] += receipt.sold_price or 0
        row[2] += receipt.net_sold or 0
    return [
        {
            "name": name or NO_ORDER_NAME,
            "receipts": r,
            "gross": g,
            "net": n,
            "no_order": not name,
        }
        for name, (r, g, n) in rows.items()
        if r or name
    ]


# Figures that more than one card, or the banner, reads. Each takes the page's
# Shared and is reached through shared.get(fn).


def listing_summary(shared):
    return insertions.listing_summary()


def other_sales(shared):
    return other.sales()


def inventory(shared):
    """
    The Units and what they sold for, in total, by Purchase Lot and by Storage
    Box. The Units and receipts are loaded once, in fbc.sales.finance, and the
    per-lot figures come from there too, so the Lots report shows the same
    numbers.
    """
    from fbc import db
    from fbc.sales import finance
    from fbc.models.inventory import PurchaseLot, StorageBox

    inv = finance.Inventory()
    units = inv.units
    # sold=False, like filter_by(sold=False), leaves out NULL as well as True.
    stock_total = sum(1 for u in units if u.sold is False)
    unit_total = len(units)
    lots = db.session.query(PurchaseLot).all()
    boxes = db.session.query(StorageBox).all()
    box_ids = {b.id for b in boxes}
    box_totals = {}
    totals = {
        "units": len(units),
        "sold_units": 0,
        "instock": 0,
        "capital": 0,
        "gross": 0,
        "net": 0,
        "fees": 0,
        "feepc": 0,
        "profit": 0,
        "roi": 0,
    }
    for b in boxes:
        box_totals[b.id] = {}
        box_totals[b.id]["name"] = b.name
        box_totals[b.id]["units"] = 0
        box_totals[b.id]["sold_units"] = 0
        box_totals[b.id]["instock"] = 0
        box_totals[b.id]["capital"] = 0.0
        box_totals[b.id]["gross"] = 0.0
        box_totals[b.id]["net"] = 0.0
    box_totals[0] = {}
    box_totals[0]["name"] = "No Box Assigned"
    box_totals[0]["units"] = 0
    box_totals[0]["sold_units"] = 0
    box_totals[0]["instock"] = 0
    box_totals[0]["capital"] = 0.0
    box_totals[0]["gross"] = 0.0
    box_totals[0]["net"] = 0.0
    lot_totals = finance.lot_figures(lots, inv)
    for lot in lots:
        totals["capital"] += lot.price
    for u in units:
        # A unit whose box no longer exists counts as having no box.
        box = box_totals[u.storage_box_id if u.storage_box_id in box_ids else 0]
        box["units"] += 1
        if u.sold is True:
            box["sold_units"] += 1
            totals["sold_units"] += 1
        if u.sold is False:
            box["instock"] += 1
            totals["instock"] += 1
        # A unit with no lot (or whose lot no longer exists) adds no
        # capital, but its sales still count toward its box and the totals.
        lot_total = lot_totals.get(u.purchase_lot_id)
        sale = inv.sale(u)
        if sale:
            c_sold_price, c_net_sold = sale
            box["gross"] += c_sold_price
            box["net"] += c_net_sold
            totals["gross"] += c_sold_price
            totals["net"] += c_net_sold
        if lot_total:
            box["capital"] += lot_total["perunit"]
    for lot in lots:
        totals["fees"] += lot_totals[lot.id]["fees"]
        totals["profit"] += lot_totals[lot.id]["profit"]
    for b in boxes:
        box_totals[b.id]["fees"] = round(
            box_totals[b.id]["gross"] - box_totals[b.id]["net"], 2
        )
        if box_totals[b.id]["gross"] > 0:
            box_totals[b.id]["feepc"] = round(
                box_totals[b.id]["fees"] / box_totals[b.id]["gross"] * 100, 2
            )
        else:
            box_totals[b.id]["feepc"] = 0.0
        box_totals[b.id]["profit"] = round(
            box_totals[b.id]["net"] - box_totals[b.id]["capital"], 2
        )
        if box_totals[b.id]["capital"] > 0:
            box_totals[b.id]["roi"] = round(
                box_totals[b.id]["profit"] / box_totals[b.id]["capital"] * 100, 2
            )
        else:
            box_totals[b.id]["roi"] = 0.0
    box_totals[0]["fees"] = round(box_totals[0]["gross"] - box_totals[0]["net"], 2)
    if box_totals[0]["gross"] > 0:
        box_totals[0]["feepc"] = round(
            box_totals[0]["fees"] / box_totals[0]["gross"] * 100, 2
        )
    else:
        box_totals[0]["feepc"] = 0.0
    box_totals[0]["profit"] = round(box_totals[0]["net"] - box_totals[0]["capital"], 2)
    if box_totals[0]["capital"] > 0:
        box_totals[0]["roi"] = round(
            box_totals[0]["profit"] / box_totals[0]["capital"] * 100, 2
        )
    else:
        box_totals[0]["roi"] = 0.0
    if totals["gross"] > 0:
        totals["feepc"] = round(totals["fees"] / totals["gross"] * 100, 2)
    else:
        totals["feepc"] = 0.0
    if totals["capital"] > 0:
        totals["roi"] = round(totals["profit"] / totals["capital"] * 100, 2)
    return {
        "stock_total": stock_total,
        "unit_total": unit_total,
        "lots": lots,
        "lot_totals": lot_totals,
        "totals": totals,
        "box_totals": box_totals,
    }


def money(shared):
    """Supply cost and the other expenses, in total and by category."""
    from fbc import db
    from fbc.models.expenses import (
        category_label,
        expense_totals,
        purchase_order_expense_total,
    )
    from fbc.models.supplies import PurchaseOrder

    supply_total = sum(po.price for po in db.session.query(PurchaseOrder).all())
    # Expenses on a Purchase Order are part of its price, and count as
    # expenses, not supplies.
    supply_total -= purchase_order_expense_total()
    # Shipping supplies used on Other Items' sales are theirs, not the records'.
    supply_total -= shared.get(other_sales)["supplies"]
    expenses = [
        (category_label(category), amount)
        for category, amount in expense_totals().items()
    ]
    return {
        "supply_total": supply_total,
        "expenses": expenses,
        "expense_total": sum(amount for _, amount in expenses),
    }


def all_time(shared):
    """Net profit and breakeven per Unit: the Units' profit less supplies and expenses."""
    inv = shared.get(inventory)
    spend = shared.get(money)
    totals = inv["totals"]
    unit_total = inv["unit_total"]
    supply_total = spend["supply_total"]
    total_net_profit = totals["profit"] - supply_total - spend["expense_total"]
    if totals["net"] != 0:
        profit_margin = total_net_profit / totals["net"] * 100
    else:
        profit_margin = 0.0
    fee_factor = 1 - totals["feepc"] / 100
    if unit_total > 0:
        supply_per_unit = supply_total / unit_total
    else:
        supply_per_unit = 0.0
    if unit_total > 0 and fee_factor != 0:
        breakeven_per_unit = (
            totals["capital"] / unit_total / fee_factor + supply_per_unit
        )
    else:
        breakeven_per_unit = 0.0
    return {
        "total_net_profit": total_net_profit,
        "profit_margin": profit_margin,
        "supply_per_unit": supply_per_unit,
        "breakeven_per_unit": breakeven_per_unit,
    }


# Each card's figures.


def _listings_figures(shared):
    listings = shared.get(listing_summary)
    # Waiting stock and the re-list suggestion mean nothing before the first
    # listings sync, so they aren't worked out then.
    if not listings["synced"]:
        return {"listings": listings}
    waiting = tasks.stock_waiting()
    return {
        "listings": listings,
        "waiting": waiting,
        "parked_suggestion": tasks.nothing_better_to_list(
            listings["room"], waiting["total"]
        ),
    }


def _stock_figures(shared):
    return {"stock_total": shared.get(inventory)["stock_total"]}


def _ytd_figures(shared):
    from fbc.sales import reports

    return {"ytd": reports.year_to_date()}


def _marketplace_figures(shared):
    return {"marketplace_sales": marketplace_sales()}


def _net_profit_figures(shared):
    all_ = shared.get(all_time)
    return {
        "total_net_profit": all_["total_net_profit"],
        "profit_margin": all_["profit_margin"],
    }


def _breakeven_figures(shared):
    all_ = shared.get(all_time)
    return {
        "supply_per_unit": all_["supply_per_unit"],
        "breakeven_per_unit": all_["breakeven_per_unit"],
    }


def _profit_details_figures(shared):
    inv = shared.get(inventory)
    return {key: inv[key] for key in ("lots", "lot_totals", "totals", "box_totals")}


def _other_items_figures(shared):
    return {"other_sales": shared.get(other_sales)}


def _money_figures(shared):
    return shared.get(money)


CARDS = [
    Card(
        "listings",
        "Listings",
        "home/listings.html",
        _listings_figures,
        empty=lambda f: not f["listings"]["synced"],
        sync="MyIndexView.syncebaylistings",
        sync_label="the eBay listings sync",
        sync_why=(
            "to see how many listings are active and how many free insertions"
            " this month has used"
        ),
    ),
    Card("stock", "Stock", "home/stock.html", _stock_figures),
    Card(
        "ytd",
        "Year to date",
        "home/ytd.html",
        _ytd_figures,
        subtitle=lambda f: f["ytd"]["start"].year,
    ),
    Card(
        "marketplace_sales",
        "All time: Sales by marketplace",
        "home/marketplace_sales.html",
        _marketplace_figures,
    ),
    Card(
        "net_profit",
        "All time: Net profit",
        "home/net_profit.html",
        _net_profit_figures,
    ),
    Card(
        "breakeven",
        "All time: Breakeven per unit",
        "home/breakeven.html",
        _breakeven_figures,
    ),
    Card(
        "profit_details",
        "All time: Profit Details",
        "home/profit_details.html",
        _profit_details_figures,
    ),
    Card(
        "other_items",
        "Other items",
        "home/other_items.html",
        _other_items_figures,
        # Until an Other Item has sold, the page has never shown this block.
        empty=lambda f: not f["other_sales"]["sold"],
        sync="MyIndexView.syncebayorders",
        sync_label="the eBay orders sync",
        sync_why="to see what Other Items have sold",
        hide_when_empty=True,
    ),
    Card("money", "Money", "home/money.html", _money_figures),
]


def figures_for(cards, shared=None):
    """The cards, each with its figures worked out, in the order given."""
    shared = shared or Shared()
    return [ShownCard(card, card.figures(shared)) for card in cards]
