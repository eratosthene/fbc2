import sys
sys.path.insert(0, '..')
from fbc.models.supplies import Supply, PurchaseOrder
from fbc import db
from pymongo import MongoClient
import datetime

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn['fbc']
for doc in mdb['supply'].find():
    old_id = str(doc['_id'])
    name = doc['name']
    quantity = doc['quantity']
    pos = []
    for mpo in doc['purchase_order']:
        po = db.session.query(PurchaseOrder).filter_by(old_id=str(mpo)).first()
        pos.append(po)
    inst = Supply(
        old_id=old_id,
        name=name,
        quantity=quantity,
        purchase_orders=pos
    )
    db.session.add(inst)
    db.session.commit()
