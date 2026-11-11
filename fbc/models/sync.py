from flask_appbuilder import Model
from sqlalchemy import Column, DateTime, String


class SyncRecord(Model):
    """When a sync last finished successfully, by name ("ebay-listings")."""

    __tablename__ = "sync_record"
    name = Column(String(40), primary_key=True)
    # UTC, like the times eBay sends.
    finished = Column(DateTime, nullable=False)
