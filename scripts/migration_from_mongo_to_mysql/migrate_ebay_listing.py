import sys

sys.path.insert(0, "..")
from fbc.models.ebay import EbayListing
from fbc import db
from pymongo import MongoClient

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn["fbc"]
for doc in mdb["e_bay_listing"].find():
    if not db.session.query(EbayListing).filter_by(item_id=doc["_id"]).first():
        inst = EbayListing(
            item_id=doc["_id"], title=doc["title"], price=doc["price"], url=doc["url"]
        )
        db.session.add(inst)
        db.session.commit()
        db.session.refresh(inst)
        print(inst)
