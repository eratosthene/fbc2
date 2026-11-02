"""
Combine Sales Receipts that stand for one shipment into one receipt.

Before Ship This handled several Units, shipments of several were recorded by
hand in two ways, both now one receipt each:

- An order of several Units split into a receipt per Unit. The receipts share
  the order, and eBay's earnings for it can't be split between them.
- Several orders from one buyer sent in one package, a receipt per order.
  The label was bought on one order and the shipping saving refunded on the
  others, so those have no label of their own.

receipts_to_combine finds both, and combine_receipts puts a group together.
"""

import logging

logger = logging.getLogger()

# How far apart a package's orders can be: a rider order (see is_rider) goes
# with the buyer's order that has a label nearest to it in that time.
PACKAGE_DAYS = 3


def _label_total(receipt):
    return round(
        sum(line.amount for line in receipt.lines if line.kind == "SHIPPING_LABEL"), 2
    )


def is_rider(receipt):
    """
    Whether `receipt` looks like an order that went in another order's
    package: eBay's lines show its sale but no label (or one bought and
    refunded).
    """
    return (
        any(line.kind == "SALE" for line in receipt.lines)
        and _label_total(receipt) >= 0
    )


def receipts_to_combine():
    """
    [(why, [receipts])] for every group of Sales Receipts that look like one
    shipment, oldest first: "same order" for receipts sharing an eBay order,
    "same package" for a buyer's rider orders (see is_rider) and the order
    with the label they went with.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt, sales_receipt_ebay_order

    groups = []
    grouped = set()

    # Receipts sharing an order, joined up through every order they share.
    links = db.session.query(
        sales_receipt_ebay_order.c.sales_receipt_id,
        sales_receipt_ebay_order.c.ebay_order_id,
    ).all()
    receipts_per_order = {}
    for receipt_id, order_id in links:
        receipts_per_order.setdefault(order_id, set()).add(receipt_id)
    for receipt_ids in receipts_per_order.values():
        if len(receipt_ids) < 2 or receipt_ids & grouped:
            continue
        group = set(receipt_ids)
        changed = True
        while changed:
            changed = False
            for others in receipts_per_order.values():
                if others & group and not others <= group:
                    group |= others
                    changed = True
        grouped |= group
        groups.append(("same order", sorted(group)))

    # Riders with the buyer's nearest order that has a label.
    receipts = (
        db.session.query(SalesReceipt)
        .filter(SalesReceipt.ebay_orders.any())
        .filter(SalesReceipt.lines.any())
        .all()
    )
    by_buyer = {}
    for receipt in receipts:
        if receipt.id in grouped or len(receipt.ebay_orders) > 1:
            continue
        buyer = receipt.ebay_orders[0].buyer
        if buyer:
            by_buyer.setdefault(buyer, []).append(receipt)
    packages = {}
    for buyer_receipts in by_buyer.values():
        labelled = [r for r in buyer_receipts if not is_rider(r)]
        for rider in (r for r in buyer_receipts if is_rider(r)):
            when = rider.ebay_orders[0].created_time
            near = [
                r
                for r in labelled
                if abs((r.ebay_orders[0].created_time - when).days) <= PACKAGE_DAYS
            ]
            if not near:
                continue
            with_label = min(
                near, key=lambda r: abs(r.ebay_orders[0].created_time - when)
            )
            packages.setdefault(with_label.id, [with_label.id]).append(rider.id)
    groups.extend(("same package", sorted(ids)) for ids in packages.values())

    everything = {r.id: r for r in db.session.query(SalesReceipt)}
    groups = [(why, [everything[i] for i in ids]) for why, ids in groups]
    groups.sort(key=lambda g: (min(r.date for r in g[1]), g[1][0].id))
    return groups


def combine_receipts(receipts):
    """
    Put `receipts` together into the oldest of them, delete the rest, and
    return it. It covers all their orders, Units and supplies used. Sold
    Price becomes the orders' prices added up, and Net Sold the receipts'.
    Their earnings lines are kept if every receipt had them; otherwise there
    are none, for Refresh earnings or backfill-earnings to fill in.

    Each Unit keeps its share of its old receipt's Sold Price as its own
    sold price, so the home page still splits the receipt between them as
    before (see unit_weights).
    """
    from fbc import db
    from fbc.models.inventory import Unit
    from fbc.models.sales import MANUAL, SalesReceiptLine
    from fbc.models.supplies import SupplyUse

    receipts = sorted(receipts, key=lambda r: (r.date, r.id))
    keep = receipts[0]
    orders = []
    for receipt in receipts:
        for order in receipt.ebay_orders:
            if order not in orders:
                orders.append(order)
    units = {
        receipt.id: db.session.query(Unit).filter_by(sales_receipt_id=receipt.id).all()
        for receipt in receipts
    }

    # Keep what each Unit sold for, which the separate receipts said. Every
    # share is worked out before any is set, since setting one changes how
    # its receipt splits between the rest.
    shares = {
        unit: round(receipt.unit_share(unit, receipt.sold_price or 0), 2)
        for receipt in receipts
        for unit in units[receipt.id]
    }
    for unit, share in shares.items():
        unit.sold_price = share

    lines = []
    if all(receipt.lines for receipt in receipts):
        seen = set()
        for receipt in receipts:
            for line in receipt.lines:
                if line.kind == MANUAL:
                    continue
                key = (line.transaction_id, line.kind, line.fee_type, line.label)
                if line.transaction_id and key in seen:
                    continue
                seen.add(key)
                lines.append(
                    {
                        c: getattr(line, c)
                        for c in (
                            "kind",
                            "fee_type",
                            "label",
                            "amount",
                            "ebay_order_id",
                            "transaction_id",
                            "transaction_date",
                        )
                    }
                )
    if all(line["transaction_date"] for line in lines):
        lines.sort(key=lambda line: line["transaction_date"])

    net_sold = round(sum(r.net_sold or 0 for r in receipts), 2)
    sold_price = round(sum(o.price or 0 for o in orders), 2)
    others = [r.id for r in receipts[1:]]
    if others:
        for unit in db.session.query(Unit).filter(Unit.sales_receipt_id.in_(others)):
            unit.sales_receipt_id = keep.id
        for use in db.session.query(SupplyUse).filter(
            SupplyUse.sales_receipt_id.in_(others)
        ):
            use.sales_receipt_id = keep.id
        for receipt in receipts[1:]:
            receipt.ebay_orders = []
            db.session.delete(receipt)
        db.session.flush()
    keep.ebay_orders = orders
    keep.sold_price = sold_price
    keep.net_sold = net_sold
    # set_lines builds new lines; the old ones go with delete-orphan.
    keep.lines = [SalesReceiptLine(**line) for line in lines]
    keep.balance_lines()
    db.session.flush()
    logger.info(
        "Combined Sales Receipts "
        + ", ".join(str(r.id) for r in receipts)
        + " into "
        + str(keep.id)
    )
    return keep


def describe(receipt):
    from fbc.models.sales import fmt_money

    return (
        str(receipt.id)
        + " ("
        + str(receipt.date)
        + ", "
        + ", ".join(receipt.order_ids())
        + ", "
        + fmt_money(receipt.sold_price or 0)
        + "/"
        + fmt_money(receipt.net_sold or 0)
        + ")"
    )


# The home page totals combine-receipts compares, and how they're shown.
COMPARED = [
    ("gross", "Sold For"),
    ("fees", "Fees"),
    ("net", "Net Sold"),
    ("feepc", "Fee %"),
    ("profit", "Profit"),
    ("roi", "ROI %"),
]


def _snapshot():
    from fbc.index import home_totals

    totals = home_totals()
    return {
        "totals": {key: totals["totals"][key] for key, _ in COMPARED}
        | {"total_net_profit": totals["total_net_profit"]},
        "lots": {
            lot.id: (lot.name, dict(totals["lot_totals"][lot.id]))
            for lot in totals["lots"]
        },
        "boxes": {box_id: dict(box) for box_id, box in totals["box_totals"].items()},
    }


def _fmt(key, value):
    if key in ("feepc", "roi"):
        return f"{value:.2f}%"
    return ("-" if value < 0 else "") + "$" + f"{abs(value):.2f}"


def compare(before, after, echo):
    """Echo how the home page's numbers changed between two _snapshots."""
    echo("Home page totals:")
    for key, label in COMPARED + [("total_net_profit", "Total Net Profit")]:
        b, a = before["totals"][key], after["totals"][key]
        echo(
            "  "
            + label
            + ": "
            + _fmt(key, b)
            + ("" if _fmt(key, a) == _fmt(key, b) else " -> " + _fmt(key, a))
        )
    for what, rows in (("Purchase Lots", "lots"), ("Storage Boxes", "boxes")):
        changed = []
        for key, row in after[rows].items():
            name, new = row if rows == "lots" else (row["name"], row)
            old = before[rows][key][1] if rows == "lots" else before[rows][key]
            diffs = [
                label + " " + _fmt(k, old[k]) + " -> " + _fmt(k, new[k])
                for k, label in (("gross", "Sold For"), ("net", "Net Sold"))
                if _fmt(k, new[k]) != _fmt(k, old[k])
            ]
            if diffs:
                changed.append("  " + name + ": " + ", ".join(diffs))
        echo(what + " whose numbers change: " + str(len(changed)))
        for line in changed:
            echo(line)


def combine_all(apply=False, only=None, echo=print):
    """
    Combine every group receipts_to_combine finds, or only the receipts with
    ids in `only` as one group, and echo each and how the home page's
    numbers change. Only saves when `apply`. Returns how many groups.
    """
    from fbc import db
    from fbc.models.sales import SalesReceipt

    if only:
        receipts = (
            db.session.query(SalesReceipt).filter(SalesReceipt.id.in_(only)).all()
        )
        missing = set(only) - {r.id for r in receipts}
        if missing:
            echo("No Sales Receipt " + ", ".join(str(i) for i in sorted(missing)) + ".")
            return 0
        groups = [("chosen", receipts)]
    else:
        groups = receipts_to_combine()
    before = _snapshot()
    for why, receipts in groups:
        names = [describe(r) for r in receipts]
        kept = combine_receipts(receipts)
        echo(
            why.capitalize()
            + ": "
            + "; ".join(names)
            + " -> "
            + describe(kept)
            + (", " + str(len(kept.lines)) + " lines" if kept.lines else ", no lines")
        )
    db.session.flush()
    compare(before, _snapshot(), echo)
    if apply:
        db.session.commit()
    else:
        db.session.rollback()
        echo("Nothing was saved. Run it again with --apply to save these.")
    return len(groups)
