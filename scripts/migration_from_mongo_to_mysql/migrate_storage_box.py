import sys

sys.path.insert(0, "..")
from fbc.models.inventory import StorageBox
from fbc import db
from pymongo import MongoClient

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn["fbc"]
for doc in mdb["storage_box"].find():
    sb = StorageBox(old_id=str(doc["_id"]), name=doc["name"])
    db.session.add(sb)
    db.session.commit()
