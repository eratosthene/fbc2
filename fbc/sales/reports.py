"""
The Reports: money for a chosen period, not for all time. So far the period,
the profit and loss, the inventory and the Lots; see issue #69 for the rest.

Plain Python over the database, like fbc.sales.finance, which it builds on:
everything is loaded once and totalled in Python, so the number of queries
doesn't grow with the stock. Archived records count, as on the home page.

Two ways to work out profit, side by side:
- Cash: net sales, less Purchase Lots bought, supplies bought and Expenses,
  each on its own date. Did the shop make or spend money in the period?
- Cost of goods: net sales, less the cost of the Units sold in the period
  (their share of their Lot's price, so adding a Unit to a Lot later changes
  the past), supplies and Expenses. Were the things sold, sold at a profit?
  This leads, as the owner chose in #69.

Other Items' sales are kept out of every record figure, as on the home page
(see fbc/models/other.py). They are worked out beside, on their own.
The tax year is the calendar year.
"""

import calendar
import datetime

import sqlalchemy as sa

from fbc import db
from fbc.core import localtime
from fbc.models.expenses import Expense, category_label
from fbc.models.inventory import PurchaseLot
from fbc.models.other import OtherItem
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import PurchaseOrder
from fbc.sales import finance, other

PRESETS = [
    ("month", "This month"),
    ("lastmonth", "Last month"),
    ("quarter", "Quarter to date"),
    ("ytd", "Year to date"),
    ("lastyear", "Last year"),
    ("12months", "Last 12 months"),
    ("all", "All time"),
]
DEFAULT_PRESET = "ytd"
GROUPS = [("month", "Month"), ("quarter", "Quarter")]
# The marketplaces a record Sales Receipt sells on, in the order to show them;
# "" is a receipt with no order, shown as Other (the home page's name too).
MARKETPLACES = ["eBay", "Discogs", ""]


def today():
    return localtime.local_naive(
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ).date()


def _month_start(day):
    return day.replace(day=1)


def _add_months(day, months):
    """The first of the month `months` after `day`'s month."""
    index = day.year * 12 + day.month - 1 + months
    return datetime.date(index // 12, index % 12 + 1, 1)


def _month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def preset_range(name, now=None):
    """(start, end) for a preset, both included; None for no limit."""
    now = now or today()
    if name == "month":
        return _month_start(now), _month_end(now)
    if name == "lastmonth":
        first = _add_months(now, -1)
        return first, _month_end(first)
    if name == "quarter":
        return datetime.date(now.year, (now.month - 1) // 3 * 3 + 1, 1), now
    if name == "ytd":
        return datetime.date(now.year, 1, 1), now
    if name == "lastyear":
        return datetime.date(now.year - 1, 1, 1), datetime.date(now.year - 1, 12, 31)
    if name == "12months":
        # From the same day a year ago, plus one.
        first = _add_months(now, -12).replace(day=1)
        return max(first, now - datetime.timedelta(days=364)), now
    if name == "all":
        return None, None
    raise ValueError(name)


def _parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


class Period:
    """
    The period asked for in the URL: ?period=ytd, or ?from=2026-01-01&to=
    2026-06-30 (either may be left out), and &group=month or quarter. Anything
    unreadable falls back to the default, with `notice` saying so.
    """

    def __init__(self, args, now=None):
        self.notice = ""
        self.group = args.get("group") if args.get("group") in dict(GROUPS) else "month"
        start, end = _parse_date(args.get("from")), _parse_date(args.get("to"))
        if args.get("from") or args.get("to"):
            self.preset = ""
            if (args.get("from") and not start) or (args.get("to") and not end):
                self.notice = "A date wasn't understood; use YYYY-MM-DD."
            elif start and end and start > end:
                self.notice = "The From date is after the To date."
            self.start, self.end = start, end
            if self.notice:
                self.preset = DEFAULT_PRESET
                self.start, self.end = preset_range(self.preset, now)
        else:
            self.preset = args.get("period")
            if self.preset not in dict(PRESETS):
                self.preset = DEFAULT_PRESET
            self.start, self.end = preset_range(self.preset, now)

    def label(self):
        if self.preset:
            return dict(PRESETS)[self.preset]
        return (
            (self.start.isoformat() if self.start else "the beginning")
            + " to "
            + (self.end.isoformat() if self.end else "today")
        )

    def query(self, **extra):
        """The URL's query string for this period, with `extra` changed."""
        params = {"group": self.group}
        if self.preset:
            params["period"] = self.preset
        else:
            if self.start:
                params["from"] = self.start.isoformat()
            if self.end:
                params["to"] = self.end.isoformat()
        params.update(extra)
        return params


def buckets(start, end, group):
    """[(label, first day, last day)] covering start to end by month or quarter."""
    step = 3 if group == "quarter" else 1
    first = _month_start(start)
    if step == 3:
        first = first.replace(month=(first.month - 1) // 3 * 3 + 1)
    result = []
    while first <= end:
        last = _month_end(_add_months(first, step - 1))
        if step == 3:
            label = f"{first.year} Q{(first.month - 1) // 3 + 1}"
        else:
            label = f"{first.year}-{first.month:02d}"
        result.append((label, max(first, start), min(last, end)))
        first = _add_months(first, step)
    return result


def _blank():
    return {
        "receipts": 0,
        "gross": 0.0,
        "net": 0.0,
        "cogs": 0.0,
        "lots": 0.0,
        "supplies": 0.0,
        "expenses": 0.0,
    }


def _load():
    """
    Every dated money event, once: ([sales], [lot purchases], [supply
    purchases], [expenses], [Other Items' sales]), each a list of tuples
    starting with the date.
    """
    inv = finance.Inventory()
    other_receipts = {
        r for (r,) in db.session.query(OtherItem.sales_receipt_id) if r is not None
    }
    # Each Unit's cost: its Lot's price over the Lot's Units.
    lot_price = dict(db.session.query(PurchaseLot.id, PurchaseLot.price))
    cost_of_receipt = {}
    for u in inv.units:
        if u.sold and u.sales_receipt_id and u.purchase_lot_id in lot_price:
            cost = lot_price[u.purchase_lot_id] / inv.units_per_lot[u.purchase_lot_id]
            cost_of_receipt[u.sales_receipt_id] = (
                cost_of_receipt.get(u.sales_receipt_id, 0.0) + cost
            )

    sales = []
    other_sales = []
    other_supplies = other.supply_costs()
    receipts = (
        db.session.query(SalesReceipt)
        .options(
            sa.orm.selectinload(SalesReceipt.ebay_orders),
            sa.orm.selectinload(SalesReceipt.discogs_orders),
        )
        .all()
    )
    other_costs = {}
    for item in db.session.query(OtherItem).filter(OtherItem.sold.is_(True)):
        if item.sales_receipt_id:
            other_costs.setdefault(item.sales_receipt_id, []).append(item.cost)
    for r in receipts:
        gross, net = r.sold_price or 0.0, r.net_sold or 0.0
        if r.id in other_receipts:
            items = other_costs.get(r.id)
            # A receipt of Other Items has a profit only when every Item has a
            # cost, so an Item with no cost is never taken for a free one.
            costed = bool(items) and all(c is not None for c in items)
            other_sales.append(
                (
                    r.date,
                    len(items or []),
                    gross,
                    net,
                    sum(items) if costed else 0.0,
                    costed,
                    other_supplies.get(r.id, 0.0),
                )
            )
        else:
            sales.append(
                (r.date, r.marketplace(), gross, net, cost_of_receipt.get(r.id, 0.0))
            )

    receipt_dates = {r.id: r.date for r in receipts}
    lots = [
        (lot_date, price)
        for lot_date, price in db.session.query(PurchaseLot.date, PurchaseLot.price)
    ]
    po_expenses = {}
    expenses = []
    for e in db.session.query(Expense):
        expenses.append((e.date, e.category, e.amount))
        if e.purchase_order_id:
            po_expenses[e.purchase_order_id] = (
                po_expenses.get(e.purchase_order_id, 0.0) + e.amount
            )
    # Packing supplies: a Purchase Order's price less its Expenses (they're
    # counted as Expenses), by the order's date. The supplies used on Other
    # Items' sales are theirs, and come out by their receipt's date.
    supplies = [
        (po.date, po.price - po_expenses.get(po.id, 0.0))
        for po in db.session.query(PurchaseOrder)
    ]
    for receipt_id, cost in other_supplies.items():
        if receipt_id in receipt_dates:
            supplies.append((receipt_dates[receipt_id], -cost))
    return sales, lots, supplies, expenses, other_sales


def _within(day, start, end):
    return (start is None or day >= start) and (end is None or day <= end)


def _figures(loaded, start, end):
    """The money in start to end, from `_load()`: {"total", "marketplaces",
    "expenses", "other"}."""
    sales, lots, supplies, expenses, other_sales = loaded
    marketplaces = {name: _blank() for name in MARKETPLACES}
    total = _blank()
    for day, marketplace, gross, net, cost in sales:
        if not _within(day, start, end):
            continue
        for row in (marketplaces[marketplace], total):
            row["receipts"] += 1
            row["gross"] += gross
            row["net"] += net
            row["cogs"] += cost
    total["lots"] = sum(p for d, p in lots if _within(d, start, end))
    total["supplies"] = sum(p for d, p in supplies if _within(d, start, end))
    by_category = {}
    for day, category, amount in expenses:
        if _within(day, start, end):
            by_category[category] = by_category.get(category, 0.0) + amount
    total["expenses"] = sum(by_category.values())
    result = {"total": total, "marketplaces": marketplaces, "expenses": by_category}
    o = {"items": 0, "receipts": 0, "gross": 0.0, "net": 0.0, "supplies": 0.0}
    cost, costed_receipts, costed_net, costed_supplies = 0.0, 0, 0.0, 0.0
    for day, items, gross, net, item_cost, costed, supply in other_sales:
        if not _within(day, start, end):
            continue
        o["items"] += items
        o["receipts"] += 1
        o["gross"] += gross
        o["net"] += net
        o["supplies"] += supply
        if costed:
            costed_receipts += 1
            cost += item_cost
            costed_net += net
            costed_supplies += supply
    o["profit"] = costed_net - cost - costed_supplies if costed_receipts else None
    result["other"] = o
    for row in [total, *marketplaces.values()]:
        _finish(row)
    return result


def _finish(row):
    """Fees and both profits, from a row's sums."""
    row["fees"] = round(row["gross"] - row["net"], 2)
    row["feepc"] = finance.percent(row["fees"], row["gross"])
    # Supplies and Expenses are the shop's, not a marketplace's: they are only
    # in the Total, so a marketplace's profit leaves them out.
    row["cogs_profit"] = round(
        row["net"] - row["cogs"] - row["supplies"] - row["expenses"], 2
    )
    row["cash_profit"] = round(
        row["net"] - row["lots"] - row["supplies"] - row["expenses"], 2
    )
    row["margin"] = finance.percent(row["cogs_profit"], row["net"])


def profit_and_loss(period):
    """
    {"total", "marketplaces", "expenses", "other", "columns"} for a Period.
    The first four are _figures() for the whole period (expenses is
    {label: amount}); columns is [(label, figures)] by its group. An
    all-time period with no records has no columns.
    """
    loaded = _load()
    start, end = period.start, period.end
    dates = (
        [s[0] for s in loaded[0]]
        + [lot[0] for lot in loaded[1]]
        + [s[0] for s in loaded[2]]
        + [e[0] for e in loaded[3]]
        + [o[0] for o in loaded[4]]
    )
    first = start or (min(dates) if dates else None)
    last = end or max([today(), *dates])
    result = _figures(loaded, start, end)
    result["expenses"] = {
        category_label(k): v
        for k, v in sorted(result["expenses"].items(), key=lambda kv: kv[0])
    }
    result["columns"] = (
        [
            (label, _figures(loaded, a, b)["total"])
            for label, a, b in buckets(first, last, period.group)
        ]
        if first and first <= last
        else []
    )
    return result


# --- Inventory ---------------------------------------------------------------


def _stock_units():
    """
    ([(came in, went out, cost, retail)], flags): one entry per Unit that is
    or was in stock. A Unit comes in on its Lot's date and goes out on its
    Sales Receipt's date; a Unit with no Lot has no date to come in on, so it
    is in stock from the beginning, at no cost. Cost is the Unit's share of its
    Lot's price. `flags` counts the Units that don't fit, which are left out.
    """
    inv = finance.Inventory()
    lots = {
        lot_id: (day, price)
        for lot_id, day, price in db.session.query(
            PurchaseLot.id, PurchaseLot.date, PurchaseLot.price
        )
    }
    receipt_dates = dict(db.session.query(SalesReceipt.id, SalesReceipt.date))
    flags = {"unset": 0, "no_receipt": 0, "no_lot": 0, "sold_first": 0}
    units = []
    for u in inv.units:
        if u.sold is None:
            flags["unset"] += 1
            continue
        lot = lots.get(u.purchase_lot_id)
        came_in = lot[0] if lot else None
        cost = lot[1] / inv.units_per_lot[u.purchase_lot_id] if lot else 0.0
        went_out = None
        if u.sold:
            went_out = receipt_dates.get(u.sales_receipt_id)
            if went_out is None:
                flags["no_receipt"] += 1
                continue
        if not lot:
            flags["no_lot"] += 1
        if came_in and went_out and went_out < came_in:
            flags["sold_first"] += 1
        units.append((came_in, went_out, cost, u.retail_price or 0.0))
    return units, flags


def _sum(units):
    return {
        "units": len(units),
        "cost": round(sum(u[2] for u in units), 2),
        "retail": round(sum(u[3] for u in units), 2),
    }


def _held_before(units, day):
    """The Units in stock when `day` began; None for before everything."""
    return [
        u
        for u in units
        if (u[0] is None or (day is not None and u[0] < day))
        and not (u[1] is not None and day is not None and u[1] < day)
    ]


def _stock_figures(units, start, end):
    """Opening, bought, sold and closing stock for start to end."""
    after = end + datetime.timedelta(days=1) if end else datetime.date.max
    bought = [u for u in units if u[0] and _within(u[0], start, end)]
    sold = [u for u in units if u[1] and _within(u[1], start, end)]
    return {
        "opening": _sum(_held_before(units, start)),
        "bought": _sum(bought),
        "sold": _sum(sold),
        "closing": _sum(_held_before(units, after)),
    }


def inventory(period):
    """
    {"total", "columns", "flags"}: stock figures for the whole Period, and for
    each of its columns ([(label, figures)]). Each figure is {"units", "cost",
    "retail"}. Cost of goods sold is "sold"'s cost, as in profit_and_loss().
    """
    units, flags = _stock_units()
    dates = [d for u in units for d in u[:2] if d]
    first = period.start or (min(dates) if dates else None)
    last = period.end or max([today(), *dates])
    return {
        "total": _stock_figures(units, period.start, period.end),
        "columns": (
            [
                (label, _stock_figures(units, a, b))
                for label, a, b in buckets(first, last, period.group)
            ]
            if first and first <= last
            else []
        ),
        "flags": flags,
    }


# --- Lots --------------------------------------------------------------------


def lots_report(period):
    """
    {"rows", "total"}: the Purchase Lots dated in the Period, newest first,
    with the figures of the Purchase Lots list (all their sales so far, not
    just the period's) and the cost of the Units still in stock.
    """
    lots = [
        lot
        for lot in db.session.query(PurchaseLot).order_by(PurchaseLot.date.desc())
        if _within(lot.date, period.start, period.end)
    ]
    figures = finance.lot_figures(lots)
    rows = []
    for lot in lots:
        f = dict(figures[lot.id])
        f["lot"] = lot
        f["stock_cost"] = (
            round(f["instock"] * f["capital"] / f["units"], 2) if f["units"] else 0.0
        )
        rows.append(f)
    total = {
        k: sum(r[k] for r in rows)
        for k in ("capital", "units", "sold_units", "instock", "gross", "net")
    }
    total["stock_cost"] = round(sum(r["stock_cost"] for r in rows), 2)
    total["fees"] = round(total["gross"] - total["net"], 2)
    total["profit"] = round(total["net"] - total["capital"], 2)
    total["roi"] = finance.percent(total["profit"], total["capital"])
    return {"rows": rows, "total": total}
