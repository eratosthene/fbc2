"""
Remove every fake record the dev-scripts added from a dev database, along
with what testing made from them: Units added from fake releases, Sales
Receipts for fake orders (Ship This or Add SR) and drafts for fake Units.
Supplies Ship This or Add Unit took out of stock aren't put back.

Run from the repo root, on this machine or in docker/compose.dev.yml's app:
  pipenv run python dev-scripts/clear_fake.py
  docker compose -f docker/compose.dev.yml exec app python dev-scripts/clear_fake.py
"""

import argparse

import _fake
from _fake import (
    FAKE_ID_START,
    FAKE_INSTANCE_START,
    FAKE_LISTING_PREFIX,
    FAKE_NOTE,
    FAKE_ORDER_PREFIX,
)
from sqlalchemy import or_


def clear():
    db = _fake.db
    from fbc.models.discogs import Artist, DiscogsInstance, DiscogsRelease, Folder
    from fbc.models.ebay import EbayDraft, EbayListing, EbayOrder
    from fbc.models.inventory import Unit
    from fbc.models.sales import SalesReceipt

    fake_listing = EbayListing.item_id.like(FAKE_LISTING_PREFIX + "%")
    fake_order = EbayOrder.order_id.like(FAKE_ORDER_PREFIX + "%")
    units = (
        db.session.query(Unit)
        .filter(
            or_(
                Unit.notes == FAKE_NOTE,
                Unit.discogs_release_id >= FAKE_ID_START,
                Unit.discogs_instance_id >= FAKE_INSTANCE_START,
                Unit.ebay_listing_id.like(FAKE_LISTING_PREFIX + "%"),
            )
        )
        .all()
    )
    unit_ids = [u.id for u in units]
    receipt_ids = [u.sales_receipt_id for u in units if u.sales_receipt_id]
    groups = [
        (
            "drafts",
            db.session.query(EbayDraft).filter(
                or_(
                    EbayDraft.unit_id.in_(unit_ids),
                    EbayDraft.ebay_listing_id.like(FAKE_LISTING_PREFIX + "%"),
                )
            ),
        ),
        ("Units", units),
        (
            "Sales Receipts",
            db.session.query(SalesReceipt).filter(
                or_(
                    SalesReceipt.id.in_(receipt_ids),
                    SalesReceipt.ebay_orders.any(
                        EbayOrder.order_id.like(FAKE_ORDER_PREFIX + "%")
                    ),
                )
            ),
        ),
        ("orders", db.session.query(EbayOrder).filter(fake_order)),
        ("listings", db.session.query(EbayListing).filter(fake_listing)),
        (
            "releases",
            db.session.query(DiscogsRelease).filter(
                DiscogsRelease.release_id >= FAKE_ID_START
            ),
        ),
        (
            "instances",
            db.session.query(DiscogsInstance).filter(
                DiscogsInstance.instance_id >= FAKE_INSTANCE_START
            ),
        ),
        (
            "artists",
            db.session.query(Artist).filter(Artist.artist_id >= FAKE_ID_START),
        ),
        (
            "folders",
            db.session.query(Folder).filter(Folder.folder_id >= FAKE_ID_START),
        ),
    ]
    # In this order, so nothing is removed while something still points at it.
    for name, records in groups:
        records = list(records)
        for record in records:
            db.session.delete(record)
        db.session.flush()
        print("Removed " + str(len(records)) + " fake " + name)
    db.session.commit()


def main():
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    _fake.setup()
    clear()


if __name__ == "__main__":
    main()
