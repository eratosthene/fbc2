import sys

sys.path.insert(0, "..")
from fbc.models.ebay import EbayOrder
from fbc import db
from pymongo import MongoClient
import datetime

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn["fbc"]
for doc in mdb["e_bay_order"].find():
    if not db.session.query(EbayOrder).filter_by(order_id=doc["_id"]).first():
        d = datetime.datetime.strptime(doc["date"], "%Y-%m-%d")
        if "ebay_listing" in doc:
            inst = EbayOrder(
                order_id=doc["_id"],
                buyer=doc["buyer"],
                price=doc["price"],
                title=doc["title"],
                created_time=d,
                ebay_listing_id=doc["ebay_listing"],
            )
        else:
            inst = EbayOrder(
                order_id=doc["_id"],
                buyer=doc["buyer"],
                price=doc["price"],
                title=doc["title"],
                created_time=d,
            )
        db.session.add(inst)
        db.session.commit()
        db.session.refresh(inst)
        print(inst)

#     order_id = Column(String(20), primary_key=True, unique=True)
#    created_time = Column(DateTime)
#    buyer = Column(String(50))
#    price = Column(Float)
#    title = Column(String(100))
#    ebay_listing_id = Column(String(20), ForeignKey('ebay_listing.item_id'))
