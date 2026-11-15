"""
What Other Items sold, for the home page. They're kept out of everything
that counts records (see fbc/models/other.py): this is the only place their
money is added up.
"""


def supply_costs():
    """
    {Sales Receipt id: cost} of the supplies used shipping Other Items' sales,
    from each Supply Use and the supply's Cost Per Use. A supply with no Cost
    Per Use is left out, so its cost stays in the shop-wide supply cost.
    """
    from sqlalchemy import func

    from fbc import db
    from fbc.models.other import receipt_ids
    from fbc.models.supplies import Supply, SupplyUse

    rows = (
        db.session.query(
            SupplyUse.sales_receipt_id, func.sum(SupplyUse.count * Supply.cost_per_use)
        )
        .join(Supply, SupplyUse.supply_id == Supply.id)
        .filter(
            SupplyUse.sales_receipt_id.in_(receipt_ids()),
            Supply.cost_per_use.isnot(None),
        )
        .group_by(SupplyUse.sales_receipt_id)
    )
    return {receipt_id: cost or 0.0 for receipt_id, cost in rows}


def sales():
    """
    {"sold", "gross", "net", "supplies", "costed", "profit"} for the sold Other
    Items: how many, their Sales Receipts' Sold Price and Net Sold, the cost of
    the shipping supplies they used (see supply_costs), and the profit of those
    with a cost, after those supplies. A receipt of several Items counts once, and has a profit
    only when every Item on it has a cost, so an Item with no cost is never
    taken for a free one. `profit` is None when no Item has a cost.
    """
    from fbc import db
    from fbc.models.other import OtherItem
    from fbc.models.sales import SalesReceipt

    items = db.session.query(OtherItem).filter(OtherItem.sold.is_(True)).all()
    by_receipt = {}
    for item in items:
        if item.sales_receipt_id:
            by_receipt.setdefault(item.sales_receipt_id, []).append(item)
    receipts = {
        r.id: r
        for r in db.session.query(SalesReceipt).filter(
            SalesReceipt.id.in_(list(by_receipt))
        )
    }
    supplies = supply_costs()
    result = {
        "sold": len(items),
        "gross": 0.0,
        "net": 0.0,
        "supplies": round(sum(supplies.values()), 2),
        "costed": 0,
        "profit": None,
    }
    for receipt_id, group in by_receipt.items():
        receipt = receipts.get(receipt_id)
        if receipt is None:
            continue
        result["gross"] += receipt.sold_price or 0
        result["net"] += receipt.net_sold or 0
        if all(i.cost is not None for i in group):
            result["costed"] += len(group)
            result["profit"] = (result["profit"] or 0) + (
                (receipt.net_sold or 0)
                - sum(i.cost for i in group)
                - supplies.get(receipt_id, 0.0)
            )
    for key in ("gross", "net", "profit"):
        if result[key] is not None:
            result[key] = round(result[key], 2)
    return result
