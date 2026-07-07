from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.supplies import PurchaseOrder, Supply


class SupplyModelView(CustomModelView):
    datamodel = SQLAInterface(Supply)
    list_columns = ["name", "quantity", "purchase_orders"]


class PurchaseOrderModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseOrder)
    list_columns = ["date", "purchase_price", "notes", "link"]
    label_columns = {"purchase_price": "Price"}
