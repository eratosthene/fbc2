from flask_appbuilder import Model
from flask_appbuilder.models.decorators import renders
import math

from sqlalchemy import Column, Integer, String, ForeignKey, Float, Date
from sqlalchemy.orm import relationship

# Supply.used_for: where a supply is taken out of stock on its own.
SHIPPING = "shipping"
ADDING_UNIT = "unit"


class PurchaseOrder(Model):
    __tablename__ = "purchase_order"
    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    date = Column(Date, nullable=False)
    price = Column(Float, nullable=False)
    notes = Column(String(100))
    link = Column(String(255))

    @renders("price")
    def purchase_price(self):
        return "$" + f"{self.price:.2f}"

    def __unicode__(self):
        return str(self.date) + ": " + self.notes + " $" + f"{self.price:.2f}"

    def __repr__(self):
        return str(self.date) + ": " + self.notes + " $" + f"{self.price:.2f}"


class Supply(Model):
    __tablename__ = "supply"

    from fbc import db

    assoc_purchase_order_supply = db.Table(
        "purchase_order_supply",
        Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("purchase_order_id", Integer, ForeignKey("purchase_order.id")),
        Column("supply_id", Integer, ForeignKey("supply.id")),
        keep_existing=True,
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    old_id = Column(String(24))
    name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
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
    pack_used = Column(Integer, nullable=False, default=0, server_default="0")
    # The home page asks for a reorder once quantity is down to this; 0
    # never does.
    min_quantity = Column(Integer, nullable=False, default=0, server_default="0")
    purchase_orders = relationship(
        "PurchaseOrder", secondary=assoc_purchase_order_supply, backref="supply"
    )

    def __unicode__(self):
        return self.name + ": " + str(self.quantity)

    def __repr__(self):
        return self.name + ": " + str(self.quantity)

    def fmt_quantity(self):
        """quantity, and what's left of the one in use when it holds several."""
        size = self.pack_size or 1
        if size <= 1:
            return str(self.quantity)
        left = size - (self.pack_used or 0)
        return (
            str(self.quantity)
            + " ("
            + str(left)
            + " of "
            + str(size)
            + " left on the one in use)"
        )

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

    def use(self, count):
        """
        Take `count` uses out of stock: one of quantity for each, or, when
        one holds several (pack_size), one each time the one in use runs
        out. quantity never goes below 0.
        """
        size = self.pack_size or 1
        used = (self.pack_used or 0) + count
        self.quantity = max(0, (self.quantity or 0) - used // size)
        self.pack_used = used % size

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
