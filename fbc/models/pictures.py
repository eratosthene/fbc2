"""A Unit's pictures. The files are on disk; see fbc/pictures/store.py."""

from flask_appbuilder import Model
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String


class UnitPicture(Model):
    """
    One picture of a Unit, kept for every marketplace to use. `file` is the
    working copy (what eBay gets and the Discogs page shows) and
    `original_file` is the upload as it came, both names inside the Unit's
    folder. A picture's files are never changed in place, so a rotation makes
    new names. There's no relationship to the Unit: the Unit's id is enough,
    and a relationship to a model that may not be loaded yet would break the
    mappers of every view imported before it.
    """

    __tablename__ = "unit_picture"
    id = Column(Integer, primary_key=True, autoincrement=True)
    unit_id = Column(
        Integer,
        ForeignKey("unit.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 1 is the main picture; the others follow in order.
    position = Column(Integer, nullable=False)
    file = Column(String(60), nullable=False)
    original_file = Column(String(60), nullable=False)
    # The name it was uploaded with, for people to recognise it by.
    upload_name = Column(String(255))
    # Where eBay's picture hosting has this picture, a cache of the last
    # upload. Blank when it hasn't been sent, or changed since.
    ebay_url = Column(String(500))
    ebay_uploaded_at = Column(DateTime)

    def __repr__(self):
        return "Unit " + str(self.unit_id) + " picture " + str(self.position)
