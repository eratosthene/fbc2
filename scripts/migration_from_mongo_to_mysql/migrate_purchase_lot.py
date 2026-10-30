import sys

sys.path.insert(0, "..")
from fbc.models.inventory import PurchaseLot
from fbc import db
from pymongo import MongoClient
import datetime

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn["fbc"]
for doc in mdb["purchase_lot"].find():
    d = datetime.datetime.strptime(doc["date"], "%Y-%m-%d").date()
    sb = PurchaseLot(
        old_id=str(doc["_id"]),
        name=doc["name"],
        date=d,
        price=doc["price"],
        notes=doc["notes"],
    )
    db.session.add(sb)
    db.session.commit()
