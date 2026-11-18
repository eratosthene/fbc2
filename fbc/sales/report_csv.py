"""
The Reports as CSV (see fbc/sales/reports.py): each report's table for the
period, as rows for a spreadsheet, plus every record in the period for an
accountant. Numbers are plain, with no $ or thousands commas.
"""

import csv
import io

from fbc import db
from fbc.models.expenses import Expense, category_label
from fbc.models.inventory import PurchaseLot
from fbc.models.other import OtherItem, receipt_ids
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import PurchaseOrder
from fbc.sales import reports


def _text(value):
    """A text cell that can't be taken for a formula when a spreadsheet opens it."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, str):
        return _text(value)
    return value


def render(rows):
    """CSV text for a list of rows."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_cell(c) for c in row])
    return out.getvalue()


def _wide(total, columns, lines):
    """Rows of `lines` ([(label, key)]) against the total and each column."""
    figures = [total] + [f for _, f in columns]
    rows = [["", "Total"] + [label for label, _ in columns]]
    for label, key in lines:
        rows.append([label] + [f[key] for f in figures])
    return rows


def profit_and_loss(period):
    d = reports.profit_and_loss(period)
    rows = _wide(
        d["total"],
        d["columns"],
        [
            ("Sales Receipts", "receipts"),
            ("Sold for", "gross"),
            ("Fees", "fees"),
            ("Fee %", "feepc"),
            ("Net sold", "net"),
            ("Cost of the Units sold", "cogs"),
            ("Lots bought", "lots"),
            ("Supplies bought", "supplies"),
            ("Expenses", "expenses"),
            ("Profit, cost of goods", "cogs_profit"),
            ("Profit, cash", "cash_profit"),
        ],
    )
    return "profit-and-loss", rows


def inventory(period):
    d = reports.inventory(period)
    figures = [d["total"]] + [f for _, f in d["columns"]]
    rows = [["", "Total"] + [label for label, _ in d["columns"]]]
    for name, key in (
        ("Opening stock", "opening"),
        ("Bought", "bought"),
        ("Sold", "sold"),
        ("Closing stock", "closing"),
    ):
        for what in ("units", "cost", "retail"):
            rows.append([f"{name}, {what}"] + [f[key][what] for f in figures])
    return "inventory", rows


def lots(period):
    d = reports.lots_report(period)
    rows = [
        [
            "Date",
            "Lot",
            "Price",
            "Units",
            "Per Unit",
            "Sold",
            "In stock",
            "Stock at cost",
            "Sold for",
            "Net sold",
            "Profit",
            "ROI %",
            "Forecast profit (if all sells)",
            "Forecast used shop rate alone",
            "12-month forecast",
            "Units to break even",
        ]
    ]
    for r in d["rows"]:
        rows.append(
            [
                r["lot"].date.isoformat(),
                r["lot"].name,
                r["capital"],
                r["units"],
                r["perunit"],
                r["sold_units"],
                r["instock"],
                r["stock_cost"],
                r["gross"],
                r["net"],
                r["profit"],
                r["roi"],
                r["forecast"],
                "yes" if r["shop_rate_only"] else "",
                r["forecast12"],
                r["to_break_even"],
            ]
        )
    return "lots", rows


def sales(period):
    d = reports.sales_report(period)
    figures = [d["total"]] + [f for _, f in d["columns"]]
    rows = _wide(
        d["total"],
        d["columns"],
        [
            ("Sales Receipts", "receipts"),
            ("Units sold", "units"),
            ("Sold for", "gross"),
            ("Fees", "fees"),
            ("Fee %", "feepc"),
            ("Net sold", "net"),
            ("Average per Sales Receipt", "avg_receipt"),
            ("Average per Unit", "avg_unit"),
            ("Average net per Unit", "avg_net_unit"),
            ("Average profit per Unit", "avg_profit_unit"),
        ],
    )
    for label, key in (
        ("Days to sell, median", "median"),
        ("Days to sell, first quartile", "q1"),
        ("Days to sell, third quartile", "q3"),
        ("Days to sell, fastest", "fastest"),
        ("Days to sell, slowest", "slowest"),
    ):
        rows.append([label] + [f["days"][key] if f["days"] else None for f in figures])
    return "sales", rows


def aging(period, old_after=reports.OLD_AFTER_MONTHS):
    d = reports.aging(period, old_after)
    rows = [["Age of stock", "Units", "Cost", "Retail"]]
    for label, f in d["buckets"]:
        rows.append(
            [
                f"{label} (as of {d['as_of'].isoformat()})",
                f["units"],
                f["cost"],
                f["retail"],
            ]
        )
    f = d["no_lot"]
    rows.append(["No Purchase Lot", f["units"], f["cost"], f["retail"]])
    f = d["total"]
    rows.append(["Total", f["units"], f["cost"], f["retail"]])
    return "aging", rows


def supplies(period):
    d = reports.supplies_report(period)
    rows = _wide(
        d["total"],
        d["columns"],
        [
            ("Bought", "bought"),
            ("Less used on Other Items", "other_used"),
            ("Supplies in Profit & Loss", "net"),
            ("Units sold", "units_sold"),
            ("Supplies per Unit sold", "per_unit"),
            ("Supplies used", "used"),
            ("Cost of those used", "used_cost"),
            ("Used with no Cost Per Use", "uncosted"),
            ("Cost used per Unit sold", "used_per_unit"),
        ],
    )
    return "supplies", rows


def records(period):
    """
    One row per record dated in the period: Sales Receipts (Other Items'
    marked), Purchase Lots, supply orders (their price less their Expenses)
    and Expenses. Amount is what was paid, or Sold For; Net is a receipt's.
    """
    rows = [["Type", "Date", "Description", "Amount", "Net"]]
    found = []
    others = {r for (r,) in db.session.execute(receipt_ids())}
    item_names = {}
    for name, receipt_id in db.session.query(
        OtherItem.name, OtherItem.sales_receipt_id
    ):
        if receipt_id is not None:
            item_names.setdefault(receipt_id, []).append(name)
    for r in db.session.query(SalesReceipt):
        if reports._within(r.date, period.start, period.end):
            if r.id in others:
                kind, what = "Other Item sale", ", ".join(item_names.get(r.id, []))
            else:
                kind, what = "Sales Receipt", r.marketplace() or "Other"
            found.append(
                (
                    r.date,
                    0,
                    [
                        kind,
                        r.date.isoformat(),
                        what,
                        r.sold_price or 0.0,
                        r.net_sold or 0.0,
                    ],
                )
            )
    for lot in db.session.query(PurchaseLot):
        if reports._within(lot.date, period.start, period.end):
            found.append(
                (
                    lot.date,
                    1,
                    ["Purchase Lot", lot.date.isoformat(), lot.name, lot.price, None],
                )
            )
    expense_totals = {}
    for e in db.session.query(Expense):
        if e.purchase_order_id:
            expense_totals[e.purchase_order_id] = (
                expense_totals.get(e.purchase_order_id, 0.0) + e.amount
            )
        if reports._within(e.date, period.start, period.end):
            what = ": ".join(
                x for x in (category_label(e.category), e.vendor, e.note) if x
            )
            found.append(
                (e.date, 3, ["Expense", e.date.isoformat(), what, e.amount, None])
            )
    for po in db.session.query(PurchaseOrder):
        if reports._within(po.date, period.start, period.end):
            found.append(
                (
                    po.date,
                    2,
                    [
                        "Supplies",
                        po.date.isoformat(),
                        po.notes or "",
                        po.price - expense_totals.get(po.id, 0.0),
                        None,
                    ],
                )
            )
    rows.extend(row for _d, _k, row in sorted(found, key=lambda t: (t[0], t[1])))
    return "records", rows
