from flask_appbuilder import Model
from flask_appbuilder.models.decorators import renders
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Date
from sqlalchemy.orm import relationship


class PurchaseOrder(Model):
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
    purchase_orders = relationship(
        "PurchaseOrder", secondary=assoc_purchase_order_supply, backref="supply"
    )

    def __unicode__(self):
        return self.name + ": " + str(self.quantity)

    def __repr__(self):
        return self.name + ": " + str(self.quantity)
