import sys
sys.path.insert(0, '..')
from fbc.models.sales import SalesReceipt
from fbc.models.ebay import EbayOrder
from fbc import db
from pymongo import MongoClient
import datetime

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn['fbc']
for doc in mdb['sales_receipt'].find():
    if not db.session.query(SalesReceipt).filter_by(old_id=str(doc['_id'])).first():
        if 'ebay_order' in doc:
            ebay_order = db.session.query(EbayOrder).filter_by(order_id=str(doc['ebay_order'])).first()
            inst = SalesReceipt(
                old_id = str(doc['_id']),
                date = datetime.datetime.strptime(doc['date'], '%Y-%m-%d').date(),
                ebay_order_id = ebay_order.order_id,
                sold_price = doc['sold_price'],
                net_sold = doc['net_sold'],
            )
            db.session.add(inst)
            db.session.commit()
            db.session.refresh(inst)
            print(inst)
