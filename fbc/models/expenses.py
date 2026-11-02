from flask import current_app
from flask_appbuilder import Model
from flask_appbuilder.models.decorators import renders

from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, func
from sqlalchemy.orm import relationship

# Expense.category: the key is stored, the label shown. EXPENSE_CATEGORIES in
# config.py replaces this list. The labels follow Schedule C's lines, so a
# year's totals map onto the tax form.
DEFAULT_CATEGORIES = {
    "advertising": "Advertising (line 8)",
    "car": "Car and truck (line 9)",
    "fees": "Commissions and fees (line 10)",
    "equipment": "Equipment (line 13)",
    "insurance": "Insurance (line 15)",
    "office": "Office expense (line 18)",
    "rent": "Rent or lease (line 20)",
    "supplies": "Supplies (line 22)",
    "travel": "Travel (line 24a)",
    "utilities": "Utilities (line 25)",
    "other": "Other (line 27a)",
}


def categories():
    """{key: label} for every expense category, in the order to show them."""
    return current_app.config.get("EXPENSE_CATEGORIES") or DEFAULT_CATEGORIES


def category_label(key):
    """`key`'s label, or the key itself once config.py no longer lists it."""
    return categories().get(key, key)


def expense_totals(start=None, end=None):
    """
    {category: total amount} of the Expenses dated from `start` to `end`
    (both included, either None for no limit), in categories() order, then
    any categories config.py no longer lists. Only categories with an
    Expense are in it.
    """
    from fbc import db

    query = db.session.query(Expense.category, Expense.amount)
    if start is not None:
        query = query.filter(Expense.date >= start)
    if end is not None:
        query = query.filter(Expense.date <= end)
    totals = {}
    for category, amount in query.order_by(Expense.date, Expense.id):
        totals[category] = totals.get(category, 0) + amount
    order = list(categories())
    return dict(
        sorted(
            totals.items(),
            key=lambda item: (
                order.index(item[0]) if item[0] in order else len(order),
                item[0],
            ),
        )
    )


def purchase_order_expense_total():
    """
    What the Expenses on Purchase Orders come to. They're part of those
    orders' prices, so supply costs leave them out.
    """
    from fbc import db

    total = (
        db.session.query(func.sum(Expense.amount))
        .filter(Expense.purchase_order_id.isnot(None))
        .scalar()
    )
    return total or 0


class Expense(Model):
    """
    A business cost that isn't a Purchase Lot (records) or packing supplies,
    like an eBay Store subscription, equipment or mileage. It can be on a
    Purchase Order, as part of its price. Equipment is recorded when it's
    bought; depreciating it is left to the accountant.
    """

    __tablename__ = "expense"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    category = Column(String(20), nullable=False)
    amount = Column(Float, nullable=False)
    vendor = Column(String(100))
    note = Column(String(255))
    # A receipt or invoice, like PurchaseOrder.link.
    link = Column(String(255))
    # For mileage: amount is miles × mileage_rate. The rate is kept with each
    # entry, since the IRS changes it every year.
    miles = Column(Float)
    mileage_rate = Column(Float)
    # Bought on a Purchase Order along with other things. amount is then this
    # expense's part of the order's price.
    purchase_order_id = Column(
        Integer,
        ForeignKey("purchase_order.id", ondelete="SET NULL"),
        index=True,
    )
    purchase_order = relationship("PurchaseOrder", back_populates="expenses")

    def work_out_mileage(self, rate=None):
        """
        With miles set, set amount to miles × mileage_rate, filling in
        mileage_rate with `rate` when it's blank. Without, clear the rate.
        """
        if self.miles is None:
            self.mileage_rate = None
            return
        if self.mileage_rate is None:
            self.mileage_rate = rate
        self.amount = round(self.miles * self.mileage_rate, 2)

    def fmt_category(self):
        return category_label(self.category)

    @renders("amount")
    def fmt_amount(self):
        return "$" + f"{self.amount:,.2f}"

    def fmt_mileage_rate(self):
        if self.mileage_rate is None:
            return ""
        # IRS rates go to a tenth of a cent, like $0.725.
        return "$" + f"{self.mileage_rate:.3f}"

    def __repr__(self):
        return (
            str(self.date)
            + ": "
            + category_label(self.category)
            + " $"
            + f"{self.amount:.2f}"
        )
