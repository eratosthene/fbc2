from flask_appbuilder import Model
from flask_appbuilder.models.decorators import renders
import datetime
import math

from sqlalchemy import Column, Integer, String, ForeignKey, Float, Date, DateTime
from sqlalchemy import inspect
from sqlalchemy.orm import column_property, object_session, relationship

# PurchaseOrder.expenses names Expense, which has to be mapped by the time
# the first view sets up its model.
import fbc.models.expenses  # noqa: F401

# Supply.used_for: where a supply is taken out of stock on its own. A
# SupplyUse's kind is one of these too, or ADJUSTMENT or RECONCILE.
SHIPPING = "shipping"
ADDING_UNIT = "unit"
ADJUSTMENT = "adjustment"
# Kept apart from ADJUSTMENT, which includes restocks, so a count's
# differences show how far the estimates drift.
RECONCILE = "reconcile"
# A Purchase Order brought some in (or one it brought was changed or removed).
RESTOCK = "restock"
USED_FOR_LABELS = {
    None: "Only by hand",
    SHIPPING: "Shipping an order",
    ADDING_UNIT: "Adding a Unit",
}
KIND_LABELS = {
    SHIPPING: "Shipping an order",
    ADDING_UNIT: "Adding a Unit",
    ADJUSTMENT: "Supply List edit",
    RECONCILE: "Reconcile Supplies",
    RESTOCK: "Purchase Order",
}


def uses_left(quantity, pack_size, pack_used):
    """
    How many uses `quantity` holds, `pack_size` each, less the `pack_used`
    gone from the one in use. None are when quantity is 0.
    """
    if not quantity or quantity < 0:
        return 0
    size = pack_size or 1
    return max(0, quantity * size - (pack_used or 0))


def fmt_stock(quantity, pack_size, pack_used):
    """quantity, and what's left of the one in use when it holds several."""
    size = pack_size or 1
    if size <= 1 or not quantity:
        return str(quantity)
    left = size - (pack_used or 0)
    return (
        str(quantity)
        + " ("
        + str(left)
        + " of "
        + str(size)
        + " left on the one in use)"
    )


class PurchaseOrder(Model):
    """
    One order: any number of supplies, and any number of Expenses for what
    else was on it. price is what the whole order cost; the Expenses' amounts
    come out of it, and the rest is what the supplies cost.
    """

    __tablename__ = "purchase_order"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    price = Column(Float, nullable=False)
    notes = Column(String(100))
    link = Column(String(255))
    # Deleting the order keeps its Expenses, which were still spent.
    expenses = relationship(
        "Expense", back_populates="purchase_order", order_by="Expense.id"
    )
    # What it brought, supply by supply. Deleting the order keeps the stock it
    # added: it did arrive, and the Supply Use record of it stays.
    items = relationship(
        "PurchaseOrderSupply",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderSupply.id",
    )
    supply = relationship(
        "Supply",
        secondary="purchase_order_supply",
        viewonly=True,
        back_populates="purchase_orders",
    )

    @renders("price")
    def purchase_price(self):
        return "$" + f"{self.price:.2f}"

    def expense_total(self):
        """What the Expenses on this order come to."""
        return sum(e.amount for e in self.expenses)

    def supply_cost(self):
        """What the supplies on this order cost: price, less its Expenses."""
        return self.price - self.expense_total()

    def fmt_supply_cost(self):
        return "$" + f"{self.supply_cost():.2f}"

    def fmt_supplies(self):
        # Just the names: a supply's own repr has its quantity now, not what
        # this order brought in.
        return ", ".join(sorted(s.name for s in self.supply))

    def __unicode__(self):
        return self.__repr__()

    def __repr__(self):
        # notes can be blank.
        return str(self.date) + ": " + (self.notes or "") + " $" + f"{self.price:.2f}"


class Supply(Model):
    __tablename__ = "supply"

    from fbc import db

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # active_history keeps what quantity and pack_used were when one is
    # changed, even after a commit, for record_adjustment.
    quantity = column_property(
        Column(Integer, nullable=False, default=0), active_history=True
    )
    # SHIPPING: offered on Ship This, ADDING_UNIT: offered on Add Unit, None:
    # only ever changed by hand.
    used_for = Column(String(10))
    # Only Units of this record size ('12"', '10"', '7"') use it when
    # shipping; None for any size.
    record_size = Column(String(5))
    # Shipping takes one for every per_units Units (rounded up), or one per
    # shipment when it's 0; None leaves it at none for you to fill in.
    per_units = Column(Integer)
    # How many uses one of quantity holds (220 labels on a roll), and how
    # many of those are gone from the one in use. quantity still counts that
    # one until it's used up.
    pack_size = Column(Integer, nullable=False, default=1, server_default="1")
    pack_used = column_property(
        Column(Integer, nullable=False, default=0, server_default="0"),
        active_history=True,
    )
    # The home page asks for a reorder once quantity is down to this; 0
    # never does.
    min_quantity = Column(Integer, nullable=False, default=0, server_default="0")
    # What one use costs, set by hand, so reports can cost a sale's supplies.
    # A Purchase Order has one price for everything on it, so this can't be
    # worked out from them. None when not known.
    cost_per_use = Column(Float)
    purchase_orders = relationship(
        "PurchaseOrder",
        secondary="purchase_order_supply",
        viewonly=True,
        back_populates="supply",
    )
    order_items = relationship(
        "PurchaseOrderSupply",
        back_populates="supply",
        cascade="all, delete-orphan",
    )
    # Deleting a supply deletes its history with it.
    uses = relationship(
        "SupplyUse", back_populates="supply", cascade="all, delete-orphan"
    )

    def __unicode__(self):
        return self.name + ": " + str(self.quantity)

    def __repr__(self):
        return self.name + ": " + str(self.quantity)

    def fmt_quantity(self):
        return fmt_stock(self.quantity, self.pack_size, self.pack_used)

    def fmt_used_for(self):
        return USED_FOR_LABELS.get(self.used_for, self.used_for)

    def fmt_record_size(self):
        return self.record_size or "Any"

    def fmt_cost_per_use(self):
        if self.cost_per_use is None:
            return ""
        return "$" + f"{self.cost_per_use:.4f}".rstrip("0").rstrip(".")

    def available(self):
        """
        How many uses are left in stock: all of quantity's, less those gone
        from the one in use. None are when quantity is 0.
        """
        return uses_left(self.quantity, self.pack_size, self.pack_used)

    def is_low(self):
        """Whether it's time to reorder: quantity is down to min_quantity."""
        return bool(self.min_quantity) and self.quantity <= self.min_quantity

    def shipping_count(self, sizes):
        """
        How many uses shipping Units of record `sizes` together normally
        takes: see per_units and record_size.
        """
        if self.per_units is None:
            return 0
        units = len([s for s in sizes if not self.record_size or s == self.record_size])
        if not units:
            return 0
        if self.per_units == 0:
            return 1
        return math.ceil(units / self.per_units)

    def use(self, count, kind, receipt=None, unit=None):
        """
        Take `count` uses out of stock: one of quantity for each, or, when
        one holds several (pack_size), one each time the one in use runs
        out. Check available() first: quantity never goes below 0, and once
        it's 0 none is in use. Records it as a SupplyUse of `kind`, for
        `receipt` or `unit` if given, and returns that.
        """
        size = self.pack_size or 1
        used = (self.pack_used or 0) + count
        self.quantity = max(0, (self.quantity or 0) - used // size)
        self.pack_used = used % size if self.quantity else 0
        return self._record(count, kind, receipt=receipt, unit=unit)

    def restock(self, count):
        """
        Add `count` to quantity, which is how many a Purchase Order brought,
        or fewer than 0 to take back some it didn't. Quantity stops at 0.
        Records it as a SupplyUse and returns that, or None when stock didn't
        change. The one in use (pack_used) stays as it is.
        """
        before = self.quantity or 0
        self.quantity = max(0, before + count)
        if self.quantity == before:
            return None
        return self._record(before - self.quantity, RESTOCK)

    def stock_from_count(self, on_hand, left=None):
        """
        (quantity, pack_used) for `on_hand` counted, counting the one in use,
        with `left` uses left on that one when it holds several (pack_size).
        Raises ValueError, with what to fix, for anything the Supply form
        wouldn't take.
        """
        if on_hand < 0:
            raise ValueError("On hand can't be below 0.")
        size = self.pack_size or 1
        if size <= 1:
            return on_hand, 0
        if not on_hand:
            if left:
                raise ValueError("Leave uses left blank when none are on hand.")
            return 0, 0
        if left is None:
            raise ValueError("Enter about how many uses are left on the one in use.")
        if not 1 <= left <= size:
            raise ValueError(
                "Uses left on the one in use must be from 1 to "
                + str(size)
                + ". One that's used up isn't on hand."
            )
        return on_hand, size - left

    def record_adjustment(self, kind=ADJUSTMENT):
        """
        Record a change made by hand to quantity or pack_used, since it was
        loaded, as a SupplyUse of `kind`: the uses it took out of stock, or
        fewer than 0 for a restock. Both sides count at the pack_size it has now,
        so changing only that records nothing. Returns the SupplyUse, or None
        when stock didn't change.
        """
        attrs = inspect(self).attrs

        def before(name):
            deleted = attrs[name].history.deleted
            return deleted[0] if deleted else getattr(self, name)

        old = uses_left(before("quantity"), self.pack_size, before("pack_used"))
        count = old - self.available()
        if not count:
            return None
        return self._record(count, kind)

    def _record(self, count, kind, receipt=None, unit=None):
        use = SupplyUse(
            supply=self, count=count, kind=kind, sales_receipt=receipt, unit=unit
        )
        # Assigning supply doesn't add it to the session on its own.
        session = object_session(self)
        if session is not None:
            session.add(use)
        return use

    @staticmethod
    def used_for_list(used_for):
        """Every supply that's used_for `used_for`, by name."""
        from fbc import db

        return (
            db.session.query(Supply)
            .filter_by(used_for=used_for)
            .order_by(Supply.name)
            .all()
        )

    @staticmethod
    def low_list():
        """Every supply that's due a reorder (see is_low), by name."""
        from fbc import db

        return (
            db.session.query(Supply)
            .filter(Supply.min_quantity > 0)
            .filter(Supply.quantity <= Supply.min_quantity)
            .order_by(Supply.name)
            .all()
        )


class PurchaseOrderSupply(Model):
    """
    How many of a supply one Purchase Order brought. This was only a link
    between the two, so what's there from before has a quantity of 0: the
    Supply List already counted it.
    """

    __tablename__ = "purchase_order_supply"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Deleting either deletes this through the ORM's cascade.
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id"), nullable=False)
    purchase_order = relationship("PurchaseOrder", back_populates="items")
    supply_id = Column(Integer, ForeignKey("supply.id"), nullable=False)
    supply = relationship("Supply", back_populates="order_items")
    # Added to the supply's quantity, so packs, like a roll of labels. Kept
    # so editing or removing this can take back the same amount.
    quantity = column_property(
        Column(Integer, nullable=False, default=0, server_default="0"),
        active_history=True,
    )

    def previous_quantity(self):
        """What quantity was when it was loaded, before an edit."""
        deleted = inspect(self).attrs.quantity.history.deleted
        return deleted[0] if deleted else self.quantity

    def __repr__(self):
        name = self.supply.name if self.supply else "?"
        return str(self.quantity) + " " + name


class SupplyUse(Model):
    """
    One time a supply's stock changed: by Ship This, Add Unit, an edit on
    the Supply List or Reconcile Supplies. Recorded since this table was
    added; nothing before.
    """

    __tablename__ = "supply_use"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, default=datetime.datetime.now)
    supply_id = Column(
        Integer, ForeignKey("supply.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supply = relationship("Supply", back_populates="uses")
    # Uses, as Supply.use takes them. An ADJUSTMENT or RECONCILE that added
    # stock is below 0.
    count = Column(Integer, nullable=False)
    kind = Column(String(10), nullable=False)
    sales_receipt_id = Column(
        Integer, ForeignKey("sales_receipt.id", ondelete="SET NULL"), index=True
    )
    sales_receipt = relationship("SalesReceipt")
    # A Unit goes when its Discogs instance does, which shouldn't take the
    # record of the supplies it used with it.
    unit_id = Column(Integer, ForeignKey("unit.id", ondelete="SET NULL"), index=True)
    unit = relationship("Unit")

    def fmt_kind(self):
        return KIND_LABELS.get(self.kind, self.kind)

    def fmt_supply(self):
        # Just the name: the supply's own repr has its quantity now, not
        # what it was then.
        return self.supply.name if self.supply else ""

    def __repr__(self):
        name = self.supply.name if self.supply else "?"
        return str(self.date) + ": " + str(self.count) + " " + name
