from flask_appbuilder import Model
from sqlalchemy import Column, DateTime, String

from fbc.core import localtime

# Don't import this module at the top of one that fbc/__init__.py imports before
# Flask-AppBuilder starts: AppBuilder makes the tables of every model loaded by
# then, which breaks the migration that creates sync_record (the Migrations
# workflow catches it). Import it inside the function that needs it.
#
# The sync_record name of the eBay listings sync.
LISTINGS_SYNC = "ebay-listings"


class SyncRecord(Model):
    """When a sync last finished successfully, by name ("ebay-listings")."""

    __tablename__ = "sync_record"
    LISTINGS_SYNC = LISTINGS_SYNC
    name = Column(String(40), primary_key=True)
    # UTC, like the times eBay sends.
    finished = Column(DateTime, nullable=False)

    @staticmethod
    def record_finished(name, when=None):
        """Remember that the sync called `name` just finished successfully."""
        from fbc import db

        when = when or localtime.utcnow()
        record = db.session.get(SyncRecord, name)
        if record is None:
            db.session.add(SyncRecord(name=name, finished=when))
        else:
            record.finished = when
        db.session.commit()

    @staticmethod
    def last_finished(name):
        """The UTC time the sync called `name` last finished, or None."""
        from fbc import db

        record = db.session.get(SyncRecord, name)
        return record.finished if record else None
