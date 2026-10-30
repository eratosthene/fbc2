import sys

sys.path.insert(0, "..")
from fbc.models.inventory import Unit, PurchaseLot, StorageBox
from fbc.models.sales import SalesReceipt
from fbc.models.discogs import DiscogsRelease
from fbc import app, db
from pymongo import MongoClient
import logging

# db.session needs an app context.
app.app_context().push()

logging.getLogger("pymongo.command").setLevel(logging.INFO)
logging.getLogger("pymongo.connection").setLevel(logging.INFO)
logging.getLogger("pymongo.serverSelection").setLevel(logging.INFO)
logging.getLogger("pymongo.topology").setLevel(logging.INFO)

conn = MongoClient("mongodb://192.168.1.10:27017")
mdb = conn.fbc
for doc in mdb.unit.find():
    if not db.session.query(Unit).filter_by(old_id=str(doc["_id"])).first():
        print(doc)
        purchase_lot = (
            db.session.query(PurchaseLot)
            .filter_by(old_id=str(doc["purchase_lot"]))
            .first()
        )
        storage_box = (
            db.session.query(StorageBox)
            .filter_by(old_id=str(doc["storage_box"]))
            .first()
        )
        sales_receipt_id = None
        if "sales_receipt" in doc:
            sales_receipt = (
                db.session.query(SalesReceipt)
                .filter_by(old_id=str(doc["sales_receipt"]))
                .first()
            )
            sales_receipt_id = sales_receipt.id
        discogs_release_id = None
        discogs_instance_id = None
        if "discogs_release" in doc:
            mdr = mdb.discogs_release
            mdiscogs_release = mdr.find_one({"_id": doc["discogs_release"]})
            # print(str(doc['discogs_release']))
            if not mdiscogs_release:
                raise Exception("No discogs_release object!")
            # print(str(mdiscogs_release['release_id']))
            discogs_release = (
                db.session.query(DiscogsRelease)
                .filter_by(release_id=str(mdiscogs_release["release_id"]))
                .first()
            )
            discogs_release_id = discogs_release.release_id
            discogs_instance_id = str(discogs_release.discogs_instances[0])
        ebay_listing_id = None
        if "ebay_listing" in doc:
            ebay_listing_id = str(doc["ebay_listing"])
        inst = Unit(
            old_id=str(doc["_id"]),
            name=doc["name"],
            unit_type=doc["unit_type"],
            description=doc["description"],
            discogs_release_id=discogs_release_id,
            discogs_instance_id=discogs_instance_id,
            ebay_listing_id=ebay_listing_id,
            purchase_lot_id=purchase_lot.id,
            storage_box_id=storage_box.id,
            grading=doc["grading"],
            pressing=doc["pressing"],
            matrix=doc["matrix"],
            notes=doc["notes"],
            retail_price=doc["retail_price"],
            sold=doc["sold"],
            sales_receipt_id=sales_receipt_id,
        )
        db.session.add(inst)
        db.session.commit()
        db.session.refresh(inst)
        print(inst)
