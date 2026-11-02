from flask_appbuilder import Model
from markupsafe import Markup
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import relationship

# SalesReceiptLine.kind is eBay's transactionType (SALE, SHIPPING_LABEL,
# REFUND, NON_SALE_CHARGE...) for a whole transaction, or one of these.
FEE = "FEE"  # one of eBay's fees, taken out of a sale
MANUAL = "MANUAL"  # what's left when Net Sold differs from eBay's lines
MANUAL_LABEL = "Manual adjustment"

# What reports total each line as. Anything else is "other".
CATEGORY_LABELS = {
    "sales": "Sales",
    "ebay_fees": "eBay fees",
    "ad_fees": "Ad fees",
    "labels": "Shipping labels",
    "refunds": "Refunds",
    "other_fees": "Other fees",
    "adjustments": "Manual adjustments",
    "other": "Other",
}
KIND_CATEGORIES = {
    "SALE": "sales",
    FEE: "ebay_fees",
    "SHIPPING_LABEL": "labels",
    "REFUND": "refunds",
    "NON_SALE_CHARGE": "other_fees",
    MANUAL: "adjustments",
}


# eBay's feeTypes for ads, and for the fees it takes for selling an order.
# eBay sometimes bills a selling fee as a NON_SALE_CHARGE of its own rather
# than taking it out of the sale, and it's still an eBay fee then.
AD_FEE_TYPES = {"AD_FEE", "PREMIUM_AD_FEES"}
SELLING_FEE_TYPES = {
    "FINAL_VALUE_FEE",
    "FINAL_VALUE_FEE_FIXED_PER_ORDER",
    "FINAL_VALUE_SHIPPING_FEE",
    "BELOW_STANDARD_FEE",
    "BELOW_STANDARD_SHIPPING_FEE",
    "HIGH_ITEM_NOT_AS_DESCRIBED_FEE",
    "HIGH_ITEM_NOT_AS_DESCRIBED_SHIPPING_FEE",
    "INTERNATIONAL_FEE",
    "REGULATORY_OPERATING_FEE",
    "PAYMENT_PROCESSING_FEE",
}


def line_category(kind, fee_type=None):
    """Which of CATEGORY_LABELS a line of `kind` and `fee_type` counts in."""
    if fee_type in AD_FEE_TYPES:
        return "ad_fees"
    if kind == "NON_SALE_CHARGE" and fee_type in SELLING_FEE_TYPES:
        return "ebay_fees"
    return KIND_CATEGORIES.get(kind, "other")


def item_prices(orders):
    """{listing id: what one Unit on it sold for} from `orders`' items."""
    prices = {}
    for order in orders:
        for item in order.items:
            if item.ebay_listing_id and item.price is not None:
                prices.setdefault(item.ebay_listing_id, item.price)
    return prices


def unit_weights(units, item_prices, listing_prices):
    """
    How a Sales Receipt's prices are split between its `units` ([(unit id,
    listing id, Unit.sold_price)]): {unit id: weight}, each Unit's sold
    price, or else its order's item price (`item_prices`, {listing id:
    price}), or else its listing's asking price (`listing_prices`). A Unit
    with none of them counts as the average of the others. None to split them evenly: for a single Unit, or when no
    Unit has a price, as for orders synced before fbc kept every item.
    """
    if len(units) <= 1:
        return None
    prices = {
        unit_id: (
            sold_price
            if sold_price is not None
            else item_prices.get(listing_id, listing_prices.get(listing_id))
        )
        for unit_id, listing_id, sold_price in units
    }
    known = [p for p in prices.values() if p is not None]
    if not known or sum(known) <= 0:
        return None
    average = sum(known) / len(known)
    return {
        unit_id: price if price is not None else average
        for unit_id, price in prices.items()
    }


def unit_share(amount, unit_id, weights, count):
    """`unit_id`'s part of `amount`, split between `count` Units by `weights`."""
    if weights is None:
        return amount / count
    return amount * weights[unit_id] / sum(weights.values())


def fmt_money(amount):
    """$4.63, or -$4.63 below 0."""
    return ("-" if amount < 0 else "") + "$" + f"{abs(amount):.2f}"


# The eBay orders a Sales Receipt covers: one, or several shipped together.
# An order should be on one receipt, but orders of several Units were split
# into a receipt per Unit by hand before Ship This handled them, and
# combine_receipts puts those back together.
sales_receipt_ebay_order = Table(
    "sales_receipt_ebay_order",
    Model.metadata,
    Column(
        "sales_receipt_id",
        Integer,
        ForeignKey("sales_receipt.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "ebay_order_id",
        String(20),
        ForeignKey("ebay_order.order_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class SalesReceipt(Model):
    __tablename__ = "sales_receipt"
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    date = Column(Date, nullable=False)
    ebay_orders = relationship(
        "EbayOrder",
        secondary=sales_receipt_ebay_order,
        back_populates="sales_receipts",
        order_by="EbayOrder.created_time",
    )
    sold_price = Column(Float)
    net_sold = Column(Float)
    # How eBay's earnings add up to net_sold. Receipts from before these
    # were kept, or with no eBay lines at all, have none.
    lines = relationship(
        "SalesReceiptLine",
        back_populates="sales_receipt",
        cascade="all, delete-orphan",
        order_by="SalesReceiptLine.id",
    )

    def order_ids(self):
        return [order.order_id for order in self.ebay_orders]

    def order_receipts(self):
        """
        How many Sales Receipts, this one included, are linked to any of its
        eBay orders. More than one when an order of several Units was split
        into a receipt per Unit by hand. eBay's earnings are for the whole
        order and can't be split between them, so those receipts get no
        lines until they're combined (see shipping.combine_receipts).
        """
        from fbc import db

        if not self.ebay_orders:
            return 0
        return (
            db.session.query(sales_receipt_ebay_order.c.sales_receipt_id)
            .filter(sales_receipt_ebay_order.c.ebay_order_id.in_(self.order_ids()))
            .distinct()
            .count()
        )

    def unit_share(self, unit, amount):
        """`unit`'s part of `amount`, one of this receipt's prices."""
        from fbc import db
        from fbc.models.inventory import Unit

        units = db.session.query(Unit).filter_by(sales_receipt_id=self.id).all()
        if len(units) <= 1:
            return amount
        weights = unit_weights(
            [(u.id, u.ebay_listing_id, u.sold_price) for u in units],
            item_prices(self.ebay_orders),
            {u.ebay_listing_id: u.ebay_listing.price for u in units if u.ebay_listing},
        )
        return unit_share(amount, unit.id, weights, len(units))

    def lines_total(self):
        return round(sum(line.amount for line in self.lines), 2)

    def set_lines(self, lines):
        """
        Replace the lines with `lines` (dicts of SalesReceiptLine's columns,
        as shipping.order_earnings gives them), then balance them.
        """
        self.lines = [SalesReceiptLine(**line) for line in lines]
        self.balance_lines()

    def balance_lines(self):
        """
        Keep the lines adding up to Net Sold: drop any manual adjustment, and
        add one for whatever difference is left. A receipt with no other
        lines is left with none, as its fees aren't broken down at all.
        Returns the adjustment, or None.
        """
        self.lines = [line for line in self.lines if line.kind != MANUAL]
        if not self.lines or self.net_sold is None:
            return None
        difference = round(self.net_sold - self.lines_total(), 2)
        if not difference:
            return None
        adjustment = SalesReceiptLine(
            kind=MANUAL, label=MANUAL_LABEL, amount=difference
        )
        self.lines.append(adjustment)
        return adjustment

    def __unicode__(self):
        return self.__repr__()

    def __repr__(self):
        ret = str(self.date)
        if self.ebay_orders:
            first = self.ebay_orders[0]
            ret = ret + " [" + first.order_id + "|" + (first.title or "")[:45]
            if len(self.ebay_orders) > 1:
                ret = ret + " +" + str(len(self.ebay_orders) - 1) + " more"
            ret = ret + "] "
        ret = ret + " $" + f"{self.sold_price:.2f}" + "/$" + f"{self.net_sold:.2f}"
        return ret

    def fmt_sold_price(self):
        return "$" + f"{self.sold_price:.2f}"

    def fmt_net_sold(self):
        return "$" + f"{self.net_sold:.2f}"

    def fmt_ebay_order(self, markup=True):
        ret = "<br>".join(
            '<a href="https://www.ebay.com/mesh/ord/details?orderid='
            + order.order_id
            + '">eBay:Order&nbsp;$'
            + f"{order.price:.2f}"
            + "</a>"
            for order in self.ebay_orders
        )
        if markup:
            return Markup(ret)
        else:
            return ret

    def link_column(self, markup=True):
        ret = self.fmt_ebay_order(False)
        if markup:
            return Markup(ret)
        else:
            return ret


class SalesReceiptLine(Model):
    """
    One part of a Sales Receipt's earnings: an eBay transaction on its order
    (the sale, a shipping label, a refund...), one of the fees eBay took out
    of the sale, or a manual adjustment. Credits are above 0. A receipt's
    lines add up to its Net Sold.
    """

    __tablename__ = "sales_receipt_line"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sales_receipt_id = Column(
        Integer,
        ForeignKey("sales_receipt.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sales_receipt = relationship("SalesReceipt", back_populates="lines")
    kind = Column(String(20), nullable=False)
    # eBay's feeType, for a fee in the sale or a charge of its own.
    fee_type = Column(String(50))
    label = Column(String(100), nullable=False)
    amount = Column(Float, nullable=False)
    # Not a foreign key: once a receipt covers several orders (#53), a line
    # can come from an order fbc hasn't synced.
    ebay_order_id = Column(String(20))
    # eBay doesn't say how long these get; a dispute hold's release is
    # DISPUTE_HOLD_REVERSE-<id>-<id>, 42 characters.
    transaction_id = Column(String(100))
    transaction_date = Column(DateTime)

    def category(self):
        return line_category(self.kind, self.fee_type)

    def fmt_amount(self):
        return fmt_money(self.amount)

    def __repr__(self):
        return self.label + " " + self.fmt_amount()


def earnings_totals(start=None, end=None):
    """
    Sales Receipts' earnings totalled for the receipts dated from `start` to
    `end` (dates, both included; None for no limit), for reports. Returns a
    dict with:
      receipts         how many
      gross, net       their Sold Price and Net Sold
      one key per CATEGORY_LABELS, the lines' total; fees and charges are
                       below 0
      not_broken_down  gross less net for the receipts with no lines, whose
                       fees, labels and refunds can't be told apart
    The categories and not_broken_down add up to net, apart from receipts
    with lines whose sale isn't Sold Price (shipping charged, or taxes).
    """
    from sqlalchemy import func

    from fbc import db

    def in_period(query):
        if start is not None:
            query = query.filter(SalesReceipt.date >= start)
        if end is not None:
            query = query.filter(SalesReceipt.date <= end)
        return query

    totals = {"receipts": 0, "gross": 0.0, "net": 0.0, "not_broken_down": 0.0}
    totals.update({category: 0.0 for category in CATEGORY_LABELS})
    broken_down = {
        receipt_id
        for (receipt_id,) in in_period(
            db.session.query(SalesReceiptLine.sales_receipt_id)
            .join(SalesReceipt)
            .distinct()
        )
    }
    for receipt_id, gross, net in in_period(
        db.session.query(
            SalesReceipt.id, SalesReceipt.sold_price, SalesReceipt.net_sold
        )
    ):
        totals["receipts"] += 1
        totals["gross"] += gross or 0
        totals["net"] += net or 0
        if receipt_id not in broken_down:
            totals["not_broken_down"] += (gross or 0) - (net or 0)
    for kind, fee_type, amount in in_period(
        db.session.query(
            SalesReceiptLine.kind,
            SalesReceiptLine.fee_type,
            func.sum(SalesReceiptLine.amount),
        )
        .join(SalesReceipt)
        .group_by(SalesReceiptLine.kind, SalesReceiptLine.fee_type)
    ):
        totals[line_category(kind, fee_type)] += amount or 0
    return {
        key: round(value, 2) if isinstance(value, float) else value
        for key, value in totals.items()
    }
