import sys
sys.path.insert(0, '..')
from fbc.models.supplies import PurchaseOrder
from fbc import db
from pymongo import MongoClient
import datetime

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn['fbc']
for doc in mdb['purchase_order'].find():
    sb = PurchaseOrder(
        old_id=str(doc['_id']),
        date = datetime.datetime.strptime(doc['date'],"%Y-%m-%d").date(),
        price = doc['price'],
        notes = doc['notes'],
        link = doc['link'],
    )
    db.session.add(sb)
    db.session.commit()
