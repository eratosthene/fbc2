import sys

sys.path.insert(0, "..")
from fbc.models.inventory import StorageBox
from fbc import app, db
from pymongo import MongoClient

# db.session needs an app context.
app.app_context().push()

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn["fbc"]
for doc in mdb["storage_box"].find():
    sb = StorageBox(old_id=str(doc["_id"]), name=doc["name"])
    db.session.add(sb)
    db.session.commit()
