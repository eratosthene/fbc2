"""
The Reports: money for a chosen period, not for all time. So far the period,
the profit and loss, the inventory, the Lots, the sales, the aging, the
supplies and the break-even figures; see issue #69 for the rest.

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
import math
import statistics

import sqlalchemy as sa

from fbc import db
from fbc.core import localtime
from fbc.models.expenses import Expense, category_label
from fbc.models.inventory import PurchaseLot
from fbc.models.other import OtherItem, receipt_ids
from fbc.models.sales import SalesReceipt, SalesReceiptLine
from fbc.models.supplies import (
    ADDING_UNIT,
    SHIPPING,
    PurchaseOrder,
    PurchaseOrderSupply,
    Supply,
    SupplyUse,
    fmt_stock,
)
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
    return localtime.local_naive(localtime.utcnow()).date()


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
    result["breakeven"] = breakeven(period)
    # Cash comes in from a position before the period, so the cumulative line
    # is the shop's whole run, not just the period's.
    before = (
        _figures(loaded, None, start - datetime.timedelta(days=1))["total"][
            "cash_profit"
        ]
        if start
        else 0.0
    )
    result["chart"] = _pnl_chart(result["columns"], before)
    return result


def _pnl_chart(columns, opening):
    """Chart.js data for the P&L: net sales and spending as bars, cash profit
    as a line, and the cumulative cash position, from `opening`."""
    spending = [round(f["lots"] + f["supplies"] + f["expenses"], 2) for _, f in columns]
    running, cumulative = opening, []
    for _, f in columns:
        running += f["cash_profit"]
        cumulative.append(round(running, 2))
    return {
        "money": {
            "labels": [label for label, _ in columns],
            "unit": "$",
            "series": [
                {
                    "label": "Net sold",
                    "kind": "bar",
                    "data": [f["net"] for _, f in columns],
                },
                {
                    "label": "Spending (Lots, supplies, Expenses)",
                    "kind": "bar",
                    "data": spending,
                },
                {
                    "label": "Cash profit",
                    "kind": "line",
                    "data": [f["cash_profit"] for _, f in columns],
                },
            ],
        },
        "cumulative": {
            "labels": [label for label, _ in columns],
            "unit": "$",
            "series": [
                {
                    "label": "Cumulative cash position",
                    "kind": "line",
                    "data": cumulative,
                },
                {"label": "Break-even", "kind": "baseline", "data": [0] * len(columns)},
            ],
        },
    }


# --- Inventory ---------------------------------------------------------------


def _stock_units():
    """
    ([(came in, went out, cost, retail, lot id)], flags): one entry per Unit that is
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
        units.append(
            (came_in, went_out, cost, u.retail_price or 0.0, u.purchase_lot_id)
        )
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
    columns = (
        [
            (label, _stock_figures(units, a, b))
            for label, a, b in buckets(first, last, period.group)
        ]
        if first and first <= last
        else []
    )
    return {
        "total": _stock_figures(units, period.start, period.end),
        "columns": columns,
        "flags": flags,
        "chart": _stock_chart(columns),
    }


def _stock_chart(columns):
    labels = [label for label, _ in columns]
    return {
        "value": {
            "labels": labels,
            "unit": "$",
            "series": [
                {
                    "label": "Stock at cost, end of period",
                    "kind": "line",
                    "data": [f["closing"]["cost"] for _, f in columns],
                }
            ],
        },
        "sold": {
            "labels": labels,
            "unit": "n",
            "series": [
                {
                    "label": "Units sold",
                    "kind": "bar",
                    "data": [f["sold"]["units"] for _, f in columns],
                }
            ],
        },
    }


# --- Lots --------------------------------------------------------------------


def lots_report(period):
    """
    {"rows", "total"}: the Purchase Lots dated in the Period, newest first,
    with the figures of the Purchase Lots list (all their sales so far, not
    just the period's), the cost of the Units still in stock and "to_break_even",
    the Units still to sell to cover the Lot's price (see breakeven()).
    """
    lots = [
        lot
        for lot in db.session.query(PurchaseLot).order_by(PurchaseLot.date.desc())
        if _within(lot.date, period.start, period.end)
    ]
    inv = finance.Inventory()
    figures = finance.lot_figures(lots, inv)
    avg, _source = _average_net_per_unit(period)
    rows = []
    for lot in lots:
        f = dict(figures[lot.id])
        f["lot"] = lot
        f["to_break_even"] = _units_to_cover(f["capital"] - f["net"], avg)
        f["stock_cost"] = (
            round(f["instock"] * f["capital"] / f["units"], 2) if f["units"] else 0.0
        )
        rows.append(f)
    total = {
        k: sum(r[k] for r in rows)
        for k in ("capital", "units", "sold_units", "instock", "gross", "net")
    }
    total["stock_cost"] = round(sum(r["stock_cost"] for r in rows), 2)
    total["to_break_even"] = _units_to_cover(total["capital"] - total["net"], avg)
    total["fees"] = round(total["gross"] - total["net"], 2)
    total["profit"] = round(total["net"] - total["capital"], 2)
    total["roi"] = finance.percent(total["profit"], total["capital"])
    return {"rows": rows, "total": total, "payback": _payback(rows, inv)}


PAYBACK_LOTS = 5


def _payback(rows, inv):
    """
    Chart.js data for how fast the PAYBACK_LOTS dearest Lots (by price) paid
    back: the net sold so far as a % of the Lot's price, at the end of each
    month from the first of their dates until now. A line starts in its Lot's
    month. 100% is where the Lot has paid for itself.
    """
    top = sorted(rows, key=lambda r: -r["capital"])[:PAYBACK_LOTS]
    top = [r for r in top if r["capital"] > 0]
    if not top:
        return None
    ids = {r["lot"].id for r in top}
    receipt_dates = dict(db.session.query(SalesReceipt.id, SalesReceipt.date))
    sales = {i: [] for i in ids}
    for u in inv.units:
        if u.sold and u.purchase_lot_id in ids and u.sales_receipt_id in receipt_dates:
            sale = inv.sale(u)
            if sale:
                sales[u.purchase_lot_id].append(
                    (receipt_dates[u.sales_receipt_id], sale[1] or 0.0)
                )
    first = _month_start(min(r["lot"].date for r in top))
    months = []
    while first <= today():
        months.append(first)
        first = _add_months(first, 1)
    series = []
    for r in top:
        lot = r["lot"]
        data = []
        for month in months:
            end = _month_end(month)
            if end < lot.date:
                data.append(None)
                continue
            net = sum(n for d, n in sales[lot.id] if d <= end)
            data.append(round(net / lot.price * 100, 1))
        series.append(
            {
                "label": f"{lot.name} ({lot.date.isoformat()})",
                "kind": "line",
                "data": data,
            }
        )
    series.append(
        {"label": "Paid back", "kind": "baseline", "data": [100] * len(months)}
    )
    return {
        "labels": [f"{m.year}-{m.month:02d}" for m in months],
        "unit": "%",
        "series": series,
    }


# --- Sales -------------------------------------------------------------------


def _sales_load():
    """
    ([receipts], [sold Units]) for record Sales Receipts (Other Items' are
    left out). A receipt is (date, marketplace, sold for, net, its Sale lines'
    total or None). A Unit is (its receipt's date, its share of sold for, its
    share of net, its cost, days from its Lot's date to the sale or None).
    """
    inv = finance.Inventory()
    others = {r for (r,) in db.session.execute(receipt_ids())}
    receipts = {
        r.id: r
        for r in db.session.query(SalesReceipt).options(
            sa.orm.selectinload(SalesReceipt.ebay_orders),
            sa.orm.selectinload(SalesReceipt.discogs_orders),
        )
        if r.id not in others
    }
    sale_lines = dict(
        db.session.query(
            SalesReceiptLine.sales_receipt_id, sa.func.sum(SalesReceiptLine.amount)
        )
        .filter(SalesReceiptLine.kind == "SALE")
        .group_by(SalesReceiptLine.sales_receipt_id)
    )
    rows = [
        (
            r.date,
            r.marketplace(),
            r.sold_price or 0.0,
            r.net_sold or 0.0,
            sale_lines.get(r.id),
        )
        for r in receipts.values()
    ]
    lots = {
        lot_id: (day, price)
        for lot_id, day, price in db.session.query(
            PurchaseLot.id, PurchaseLot.date, PurchaseLot.price
        )
    }
    units = []
    for u in inv.units:
        receipt = receipts.get(u.sales_receipt_id)
        if not u.sold or receipt is None:
            continue
        gross, net = inv.sale(u)
        lot = lots.get(u.purchase_lot_id)
        cost = lot[1] / inv.units_per_lot[u.purchase_lot_id] if lot else 0.0
        days = (receipt.date - lot[0]).days if lot and receipt.date >= lot[0] else None
        units.append((receipt.date, gross or 0.0, net or 0.0, cost, days))
    return rows, units


def _spread(days):
    """The median, quartiles, fastest and slowest of `days`; None for none."""
    if not days:
        return None
    days = sorted(days)
    if len(days) > 1:
        q1, _, q3 = statistics.quantiles(days, n=4, method="inclusive")
    else:
        q1 = q3 = days[0]
    return {
        "median": statistics.median(days),
        "q1": q1,
        "q3": q3,
        "fastest": days[0],
        "slowest": days[-1],
    }


def _sales_figures(rows, units, start, end):
    """The sales from start to end: counts, money, averages and days to sell."""
    receipts = [r for r in rows if _within(r[0], start, end)]
    sold = [u for u in units if _within(u[0], start, end)]
    gross = sum(r[2] for r in receipts)
    net = sum(r[3] for r in receipts)
    count = len(sold)
    days = [u[4] for u in sold if u[4] is not None]

    def per_unit(total):
        return round(total / count, 2) if count else 0.0

    return {
        "receipts": len(receipts),
        "units": count,
        "gross": round(gross, 2),
        "fees": round(gross - net, 2),
        "feepc": finance.percent(gross - net, gross),
        "net": round(net, 2),
        "avg_receipt": round(gross / len(receipts), 2) if receipts else 0.0,
        "avg_unit": per_unit(sum(u[1] for u in sold)),
        "avg_net_unit": per_unit(sum(u[2] for u in sold)),
        # Before supplies and Expenses: they are the shop's, not a Unit's.
        "avg_profit_unit": per_unit(sum(u[2] - u[3] for u in sold)),
        "days": _spread(days),
        "days_unknown": count - len(days),
    }


def _gross_by_year(rows):
    """
    [(year, {marketplace: {"gross", "sale_lines"}, "total": same})], newest
    first. "sale_lines" is the receipts' Sale lines (eBay's figure, which has
    the taxes it collects taken out), or the sold for where a receipt has none.
    """
    years = {}
    for day, marketplace, gross, _net, lines in rows:
        year = years.setdefault(
            day.year,
            {m: {"gross": 0.0, "sale_lines": 0.0} for m in [*MARKETPLACES, "total"]},
        )
        for key in (marketplace, "total"):
            year[key]["gross"] += gross
            year[key]["sale_lines"] += gross if lines is None else lines
    return [
        (
            y,
            {
                k: {m: round(v, 2) for m, v in figures.items()}
                for k, figures in years[y].items()
            },
        )
        for y in sorted(years, reverse=True)
    ]


def sales_report(period):
    """
    {"total", "columns", "years"}: _sales_figures() for the whole Period and
    for each column ([(label, figures)]), and the gross for every calendar
    year, whatever the Period (for checking eBay's 1099-K).
    """
    rows, units = _sales_load()
    dates = [r[0] for r in rows]
    first = period.start or (min(dates) if dates else None)
    last = period.end or max([today(), *dates])
    return {
        "total": _sales_figures(rows, units, period.start, period.end),
        "columns": (
            [
                (label, _sales_figures(rows, units, a, b))
                for label, a, b in buckets(first, last, period.group)
            ]
            if first and first <= last
            else []
        ),
        "years": _gross_by_year(rows),
    }


# --- Aging -------------------------------------------------------------------

AGE_BUCKETS = [
    ("0–3 months", 0, 3),
    ("3–6 months", 3, 6),
    ("6–12 months", 6, 12),
    ("A year or more", 12, None),
]
OLD_STOCK_LOTS = 10
# "Old" stock, for the list of Lots holding it: bought this many months ago
# (the default; the page lets you change it, up to MAX_OLD_AFTER_MONTHS).
OLD_AFTER_MONTHS = 6
MAX_OLD_AFTER_MONTHS = 120


def old_after_months(text):
    """The months from ?old=, or the default when it isn't a whole number in range."""
    try:
        months = int(text)
    except (TypeError, ValueError):
        return OLD_AFTER_MONTHS
    return months if 1 <= months <= MAX_OLD_AFTER_MONTHS else OLD_AFTER_MONTHS


def _months_between(first, last):
    """Whole months from `first` to `last`."""
    months = (last.year - first.year) * 12 + last.month - first.month
    return months - 1 if last.day < first.day else months


def aging(period, old_after=OLD_AFTER_MONTHS):
    """
    {"as_of", "buckets", "no_lot", "total", "old_lots"}: the Units in stock at
    the end of the Period (today, if it ends later or not at all), by months
    since their Purchase Lot's date. Each of "buckets" ([(label, figures)]),
    "no_lot" and "total" has {"units", "cost", "retail"}. "old_lots" is the
    OLD_STOCK_LOTS Purchase Lots bought OLD_AFTER_MONTHS or more before then
    that have the most cost in stock, as [{"lot", "units", "cost"}];
    `old_after` changes the OLD_AFTER_MONTHS.
    """
    units, _flags = _stock_units()
    as_of = min(period.end, today()) if period.end else today()
    held = _held_before(units, as_of + datetime.timedelta(days=1))
    groups = [[] for _ in AGE_BUCKETS]
    no_lot = []
    old = {}
    for u in held:
        if u[0] is None:
            no_lot.append(u)
            continue
        months = _months_between(u[0], as_of)
        for group, (_label, low, high) in zip(groups, AGE_BUCKETS):
            if months >= low and (high is None or months < high):
                group.append(u)
        if months >= old_after:
            old.setdefault(u[4], []).append(u)
    names = {
        lot_id: lot
        for lot_id, lot in ((lot.id, lot) for lot in db.session.query(PurchaseLot))
        if lot_id in old
    }
    old_lots = sorted(
        (
            {"lot": names[lot_id], **_sum(lot_units)}
            for lot_id, lot_units in old.items()
        ),
        key=lambda f: (-f["cost"], -f["units"], f["lot"].date),
    )[:OLD_STOCK_LOTS]
    return {
        "as_of": as_of,
        "buckets": [(a[0], _sum(g)) for a, g in zip(AGE_BUCKETS, groups)],
        "no_lot": _sum(no_lot),
        "total": _sum(held),
        "old_lots": old_lots,
    }


# --- Supplies ----------------------------------------------------------------


def _supplies_load():
    """
    The supply money and uses, once: (bought, other_used, uses, ordered,
    supplies, units_sold).
    - bought: [(Purchase Order date, price less its Expenses)];
    - other_used: [(date, cost)] of the supplies used on Other Items' sales;
    - uses: [(date, supply id, count)] of record sales and Units added (Other
      Items' are in other_used), in local time; restocks and Supply List edits
      are not uses;
    - ordered: [(Purchase Order date, supply id, quantity)];
    - supplies: {id: Supply};
    - units_sold: [date] of every sold Unit, by its Sales Receipt.
    """
    others = {r for (r,) in db.session.execute(receipt_ids())}
    receipt_dates = dict(db.session.query(SalesReceipt.id, SalesReceipt.date))
    po_expenses = dict(
        db.session.query(Expense.purchase_order_id, sa.func.sum(Expense.amount))
        .filter(Expense.purchase_order_id.isnot(None))
        .group_by(Expense.purchase_order_id)
    )
    orders = list(
        db.session.query(PurchaseOrder.id, PurchaseOrder.date, PurchaseOrder.price)
    )
    bought = [(day, price - po_expenses.get(i, 0.0)) for i, day, price in orders]
    order_dates = {i: day for i, day, _price in orders}
    other_used = [
        (receipt_dates[r], c)
        for r, c in other.supply_costs().items()
        if r in receipt_dates
    ]
    uses = [
        (localtime.local_naive(when).date(), supply_id, count)
        for when, supply_id, count, receipt_id in db.session.query(
            SupplyUse.date,
            SupplyUse.supply_id,
            SupplyUse.count,
            SupplyUse.sales_receipt_id,
        ).filter(SupplyUse.kind.in_([SHIPPING, ADDING_UNIT]))
        if receipt_id not in others
    ]
    ordered = [
        (order_dates[order_id], supply_id, quantity)
        for order_id, supply_id, quantity in db.session.query(
            PurchaseOrderSupply.purchase_order_id,
            PurchaseOrderSupply.supply_id,
            PurchaseOrderSupply.quantity,
        )
        if order_id in order_dates
    ]
    supplies = {s.id: s for s in db.session.query(Supply)}
    units_sold = [u[0] for u in _sales_load()[1]]
    return bought, other_used, uses, ordered, supplies, units_sold


def _supply_figures(load, start, end):
    """
    The supplies for start to end: "bought" (what the shop spent, less the
    Expenses on its orders), "other_used" (the cost of those used on Other
    Items, which come off it, as in Profit & Loss), "net" (Profit & Loss's
    supplies), the Units sold, "per_unit" (net over the Units sold), and
    the uses of record sales and Units added: "used" (count), "used_cost"
    (where the Supply has a Cost Per Use), "uncosted" (uses of a Supply
    that has none) and "used_per_unit".
    """
    bought, other_used, uses, _ordered, supplies, units_sold = load
    spent = sum(p for d, p in bought if _within(d, start, end))
    off = sum(c for d, c in other_used if _within(d, start, end))
    used = cost = uncosted = 0
    for day, supply_id, count in uses:
        if not _within(day, start, end):
            continue
        used += count
        supply = supplies.get(supply_id)
        if supply is not None and supply.cost_per_use is not None:
            cost += count * supply.cost_per_use
        else:
            uncosted += count
    units = sum(1 for d in units_sold if _within(d, start, end))
    return {
        "bought": round(spent, 2),
        "other_used": round(off, 2),
        "net": round(spent - off, 2),
        "units_sold": units,
        "per_unit": round((spent - off) / units, 2) if units else None,
        "used": used,
        "used_cost": round(cost, 2),
        "uncosted": uncosted,
        "used_per_unit": round(cost / units, 2) if units else None,
    }


def supplies_report(period):
    """
    {"total", "columns", "per_supply", "breakeven"}: _supply_figures() for the
    Period and for each column ([(label, figures)]); one row per Supply,
    [{"supply", "ordered", "used", "cost_per_use", "used_cost", "in_stock"}],
    for what was ordered and used in the Period; and breakeven().
    """
    load = _supplies_load()
    bought, other_used, uses, ordered, supplies, units_sold = load
    dates = [d for d, _ in bought] + [d for d, _ in other_used] + units_sold
    first = period.start or (min(dates) if dates else None)
    last = period.end or max([today(), *dates])
    rows = []
    for supply in sorted(supplies.values(), key=lambda s: s.name.lower()):
        order_qty = sum(
            q
            for d, i, q in ordered
            if i == supply.id and _within(d, period.start, period.end)
        )
        used = sum(
            c
            for d, i, c in uses
            if i == supply.id and _within(d, period.start, period.end)
        )
        if not (order_qty or used):
            continue
        rows.append(
            {
                "supply": supply,
                "ordered": order_qty,
                "used": used,
                "cost_per_use": supply.cost_per_use,
                "used_cost": (
                    round(used * supply.cost_per_use, 2)
                    if supply.cost_per_use is not None
                    else None
                ),
                "in_stock": fmt_stock(
                    supply.quantity, supply.pack_size, supply.pack_used
                ),
            }
        )
    return {
        "total": _supply_figures(load, period.start, period.end),
        "columns": (
            [
                (label, _supply_figures(load, a, b))
                for label, a, b in buckets(first, last, period.group)
            ]
            if first and first <= last
            else []
        ),
        "per_supply": rows,
        "breakeven": breakeven(period),
    }


# --- Break-even --------------------------------------------------------------


def _average_net_per_unit(period):
    """(average net per Unit sold, where it came from): the Period's, or all
    time's when the Period sold nothing; (0.0, "") when nothing ever sold."""
    rows, units = _sales_load()
    for label, start, end in (
        ("the period", period.start, period.end),
        ("all time", None, None),
    ):
        avg = _sales_figures(rows, units, start, end)["avg_net_unit"]
        if avg > 0:
            return avg, label
    return 0.0, ""


def _units_to_cover(amount, average):
    """Whole Units at `average` net each to cover `amount`: 0 when covered,
    None when there is no average to go on."""
    if amount <= 0:
        return 0
    if average <= 0:
        return None
    return math.ceil(amount / average)


def _break_even(cost_per_unit, feepc, supply_per_unit):
    """
    The least a Unit can sell for to cover its cost and the supplies, after
    fees: the home page's sum. None when fees take everything.
    """
    factor = 1 - feepc / 100
    if factor <= 0:
        return None
    return round(cost_per_unit / factor + supply_per_unit, 2)


def breakeven(period):
    """
    {"period", "all", "still_to_sell"}. "period" and "all" (all time) are
    {"units", "cost_per_unit", "feepc", "supply_per_unit", "breakeven"}: the
    break-even per Unit, which is also the least an average Unit can sell
    for. All time is the home page's: every Lot's price over every Unit,
    plus every supply over every Unit. The Period's is the cost of the Units
    sold in it over those Units, plus the Period's supplies over them, with
    the Period's fee %. "breakeven" is None when there are no Units to go on.

    "still_to_sell" is {"outstanding", "average", "average_of", "units"}: what
    all time has spent (Lots, supplies and Expenses) beyond its net sales,
    the average net per Unit sold in the Period (or all time, "average_of"),
    and the Units still to sell at that average to cover it.
    """
    loaded = _load()
    load = _supplies_load()
    inv = finance.Inventory()
    everything = _figures(loaded, None, None)["total"]
    here = _figures(loaded, period.start, period.end)["total"]
    all_units = len(inv.units)
    supply_all = _supply_figures(load, None, None)
    supply_here = _supply_figures(load, period.start, period.end)

    def block(units, cost, feepc, supplies):
        per_cost = cost / units if units else 0.0
        per_supply = supplies / units if units else 0.0
        return {
            "units": units,
            "cost_per_unit": round(per_cost, 2),
            "feepc": feepc,
            "supply_per_unit": round(per_supply, 2),
            "breakeven": _break_even(per_cost, feepc, per_supply) if units else None,
        }

    average, average_of = _average_net_per_unit(period)
    outstanding = round(
        everything["lots"]
        + everything["supplies"]
        + everything["expenses"]
        - everything["net"],
        2,
    )
    return {
        "period": block(
            supply_here["units_sold"],
            here["cogs"],
            here["feepc"],
            here["supplies"],
        ),
        "all": block(
            all_units, everything["lots"], everything["feepc"], supply_all["net"]
        ),
        "still_to_sell": {
            "outstanding": max(outstanding, 0.0),
            "average": average,
            "average_of": average_of,
            "units": _units_to_cover(outstanding, average),
        },
    }


# --- Home page ---------------------------------------------------------------


def year_to_date(now=None):
    """
    {"start", "end", "net", "profit", "margin", "units_sold", "receipts"}: this
    calendar year so far, for the home page. Profit is the cost-of-goods one
    from Profit & Loss, and so is the margin (of net sold).
    """
    period = Period({"period": "ytd"}, now)
    total = _figures(_load(), period.start, period.end)["total"]
    rows, units = _sales_load()
    return {
        "start": period.start,
        "end": period.end,
        "net": total["net"],
        "profit": total["cogs_profit"],
        "margin": total["margin"],
        "receipts": total["receipts"],
        "units_sold": _sales_figures(rows, units, period.start, period.end)["units"],
    }
