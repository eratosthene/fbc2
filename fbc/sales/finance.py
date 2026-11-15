"""
The money maths for Purchase Lots, shared by the home page and the
Purchase Lots list and Breakdown, and for the Reports to build on.

This is plain Python over the database: nothing here knows about views or
requests, so keep it that way. It loads the columns it needs once, and
totals in Python, so the number of queries doesn't grow with the stock.

A Sales Receipt's money is split between its Units (by `unit_weights`, or
evenly), and a Purchase Lot's price is split evenly between its Units.
Percentages are percent (13.04, not 0.1304).
"""

from collections import Counter

from fbc import db
from fbc.models.ebay import EbayListing, EbayOrderItem
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.sales import (
    SalesReceipt,
    sales_receipt_ebay_order,
    unit_share,
    unit_weights,
)


class Inventory:
    """Every Unit and Sales Receipt, loaded for working out shares."""

    def __init__(self):
        # Units stay in id order and are visited one at a time, so the
        # floating-point sums add up the same way every time.
        self.units = (
            db.session.query(
                Unit.id,
                Unit.ebay_listing_id,
                Unit.sold_price,
                Unit.retail_price,
                Unit.purchase_lot_id,
                Unit.storage_box_id,
                Unit.sales_receipt_id,
                Unit.sold,
            )
            .order_by(Unit.id)
            .all()
        )
        self.receipts = {
            r.id: r
            for r in db.session.query(
                SalesReceipt.id, SalesReceipt.sold_price, SalesReceipt.net_sold
            )
        }
        self.units_per_lot = Counter(u.purchase_lot_id for u in self.units)
        self.units_per_receipt = Counter(u.sales_receipt_id for u in self.units)
        self.weights = self._weights()

    def _weights(self):
        # A receipt of several Units is split between them by what each
        # sold for (see unit_weights), or evenly when that isn't known.
        receipt_units = {}
        for u in self.units:
            if self.units_per_receipt[u.sales_receipt_id] > 1 and u.sales_receipt_id:
                receipt_units.setdefault(u.sales_receipt_id, []).append(
                    (u.id, u.ebay_listing_id, u.sold_price, u.retail_price)
                )
        receipt_items = {}
        for receipt_id, listing_id, price in (
            db.session.query(
                sales_receipt_ebay_order.c.sales_receipt_id,
                EbayOrderItem.ebay_listing_id,
                EbayOrderItem.price,
            )
            .join(
                EbayOrderItem,
                EbayOrderItem.ebay_order_id == sales_receipt_ebay_order.c.ebay_order_id,
            )
            .order_by(EbayOrderItem.id)
        ):
            if listing_id and price is not None:
                receipt_items.setdefault(receipt_id, {}).setdefault(listing_id, price)
        listing_prices = dict(db.session.query(EbayListing.item_id, EbayListing.price))
        return {
            receipt_id: unit_weights(
                units, receipt_items.get(receipt_id, {}), listing_prices
            )
            for receipt_id, units in receipt_units.items()
        }

    def sale(self, u):
        """
        (sold for, net sold) for Unit row `u`: its share of its Sales
        Receipt. None when it has none.
        """
        receipt = self.receipts.get(u.sales_receipt_id)
        if not receipt:
            return None
        count = self.units_per_receipt[u.sales_receipt_id]
        w = self.weights.get(u.sales_receipt_id)
        return (
            unit_share(receipt.sold_price, u.id, w, count),
            unit_share(receipt.net_sold, u.id, w, count),
        )


def percent(part, whole):
    """`part` as a percent of `whole`, to 2 places; 0.0 when `whole` isn't above 0."""
    return round(part / whole * 100, 2) if whole > 0 else 0.0


def lot_figures(lots=None, inventory=None):
    """
    {lot id: figures} for `lots` (all of them when None). Figures:
    capital, units, sold_units, instock, perunit (capital per Unit, to the
    cent), gross (sold for), net, fees, feepc, profit, roi, breakeven (the
    price a Unit must sell for to cover the lot after fees, per Unit),
    retail_total, avg_sold_price, potential_profit, avg_profit_per_unit and
    likely_profit.
    """
    if lots is None:
        lots = db.session.query(PurchaseLot).all()
    inv = inventory or Inventory()
    figures = {}
    for lot in lots:
        count = inv.units_per_lot[lot.id]
        figures[lot.id] = {
            "capital": lot.price,
            "units": count,
            "sold_units": 0,
            "instock": 0,
            "perunit": round(lot.price / count, 2) if count else 0,
            "gross": 0,
            "net": 0,
            "retail_total": 0.0,
            "_gross_of_sold": 0.0,
            "_sold_with_sale": 0,
        }
    for u in inv.units:
        f = figures.get(u.purchase_lot_id)
        if f is None:
            continue
        if u.sold is True:
            f["sold_units"] += 1
        if u.sold is False:
            f["instock"] += 1
        if u.retail_price:
            f["retail_total"] += u.retail_price
        sale = inv.sale(u)
        if sale:
            f["gross"] += sale[0]
            f["net"] += sale[1]
            if u.sold:
                f["_gross_of_sold"] += sale[0]
                f["_sold_with_sale"] += 1
    for lot in lots:
        f = figures[lot.id]
        f["fees"] = round(f["gross"] - f["net"], 2)
        f["feepc"] = percent(f["fees"], f["gross"])
        f["profit"] = round(f["net"] - lot.price, 2)
        f["roi"] = percent(f["profit"], lot.price) if lot.price > 0 else 0.0
        fee_factor = 1 - f["feepc"] / 100
        if f["units"] and fee_factor > 0:
            f["breakeven"] = lot.price / f["units"] / fee_factor
        else:
            f["breakeven"] = 0
        # Average over the sold Units that have a receipt.
        sold_with_sale = f.pop("_sold_with_sale")
        gross_of_sold = f.pop("_gross_of_sold")
        f["avg_sold_price"] = gross_of_sold / sold_with_sale if sold_with_sale else 0
        f["potential_profit"] = (
            f["retail_total"] - f["retail_total"] * f["feepc"] / 100 - lot.price
        )
        f["avg_profit_per_unit"] = f["avg_sold_price"] - f["breakeven"]
        f["likely_profit"] = f["units"] * f["avg_profit_per_unit"]
    return figures
