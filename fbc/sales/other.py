"""
What Other Items sold, for the home page. They're kept out of everything
that counts records (see fbc/models/other.py): this is the only place their
money is added up.
"""


def sales():
    """
    {"sold", "gross", "net", "costed", "profit"} for the sold Other Items:
    how many, their Sales Receipts' Sold Price and Net Sold, and the profit of
    those with a cost. A receipt of several Items counts once, and has a profit
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
    result = {"sold": len(items), "gross": 0.0, "net": 0.0, "costed": 0, "profit": None}
    for receipt_id, group in by_receipt.items():
        receipt = receipts.get(receipt_id)
        if receipt is None:
            continue
        result["gross"] += receipt.sold_price or 0
        result["net"] += receipt.net_sold or 0
        if all(i.cost is not None for i in group):
            result["costed"] += len(group)
            result["profit"] = (result["profit"] or 0) + (
                (receipt.net_sold or 0) - sum(i.cost for i in group)
            )
    for key in ("gross", "net", "profit"):
        if result[key] is not None:
            result[key] = round(result[key], 2)
    return result
