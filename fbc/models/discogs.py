import logging
from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    Date,
    DateTime,
    Text,
    BigInteger,
    Boolean,
    Float,
    false,
)
from sqlalchemy.orm import relationship
import re
import json

logger = logging.getLogger()


class Artist(Model):
    __tablename__ = "artist"
    artist_id = Column(Integer, primary_key=True, unique=True)
    name = Column(String(100), nullable=False)
    sort_name = Column(String(100))

    def __unicode__(self):
        if self.sort_name:
            return self.sort_name
        else:
            return self.name

    def __repr__(self):
        if self.sort_name:
            return self.sort_name
        else:
            return self.name


class Genre(Model):
    __tablename__ = "genre"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Style(Model):
    __tablename__ = "style"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Folder(Model):
    __tablename__ = "folder"
    folder_id = Column(Integer, primary_key=True, unique=True)
    name = Column(String(100), nullable=False)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class DiscogsInstance(Model):
    __tablename__ = "discogs_instance"
    instance_id = Column(BigInteger, primary_key=True, unique=True)

    def __unicode__(self):
        return str(self.instance_id)

    def __repr__(self):
        return str(self.instance_id)


class DiscogsRelease(Model):
    __tablename__ = "discogs_release"

    from fbc import db

    assoc_artist_discogs_release = db.Table(
        "artist_discogs_release",
        Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("artist_id", Integer, ForeignKey("artist.artist_id")),
        Column("release_id", Integer, ForeignKey("discogs_release.release_id")),
        keep_existing=True,
    )
    assoc_genre_discogs_release = db.Table(
        "genre_discogs_release",
        Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("genre_id", Integer, ForeignKey("genre.id")),
        Column("release_id", Integer, ForeignKey("discogs_release.release_id")),
        keep_existing=True,
    )
    assoc_style_discogs_release = db.Table(
        "style_discogs_release",
        Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("style_id", Integer, ForeignKey("style.id")),
        Column("release_id", Integer, ForeignKey("discogs_release.release_id")),
        keep_existing=True,
    )
    assoc_discogs_instance_discogs_release = db.Table(
        "discogs_instance_discogs_release",
        Model.metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "instance_id",
            BigInteger,
            ForeignKey("discogs_instance.instance_id", ondelete="CASCADE"),
        ),
        Column("release_id", Integer, ForeignKey("discogs_release.release_id")),
        keep_existing=True,
    )

    release_id = Column(Integer, primary_key=True, unique=True)
    title = Column(String(200), nullable=False, index=True)
    artists = relationship(
        "Artist",
        secondary=assoc_artist_discogs_release,
        backref="discogs_release",
        lazy="selectin",
    )
    year = Column(Integer)
    genres = relationship(
        "Genre", secondary=assoc_genre_discogs_release, backref="discogs_release"
    )
    styles = relationship(
        "Style", secondary=assoc_style_discogs_release, backref="discogs_release"
    )
    master_id = Column(Integer)
    master_year = Column(Integer)
    formats = Column(Text)
    labels = Column(Text)
    country = Column(String(50))
    discogs_instances = relationship(
        "DiscogsInstance",
        secondary=assoc_discogs_instance_discogs_release,
        backref="discogs_release",
    )
    released = Column(String(20))
    notes = Column(Text)
    folder_id = Column(Integer, ForeignKey("folder.folder_id"))
    folder = relationship("Folder")
    date_added = Column(Date)

    def artist_rep(self):
        num = 0
        ret = ""
        for artist in self.artists:
            if num:
                ret = ret + " / "
            ret = ret + str(artist)
            num = num + 1
        return ret

    def __unicode__(self):
        return self.artist_rep() + " - " + self.title + " (" + str(self.year) + ")"

    def __repr__(self):
        return self.artist_rep() + " - " + self.title + " (" + str(self.year) + ")"

    def release_show(self, markup=True):
        s = (
            '<a href="https://www.discogs.com/release/'
            + str(self.release_id)
            + '">D:Release</a>'
        )
        if markup:
            return Markup(s)
        else:
            return s

    def master_show(self, markup=True):
        s = (
            '<a href="https://www.discogs.com/master/'
            + str(self.master_id)
            + '">D:Master</a>'
        )
        if markup:
            return Markup(s)
        else:
            return s

    def discogs_master_key(self):
        """
        The value stored in Unit.discogs_master for this release: the master
        id, or the release id prefixed with "r" when there is no master.
        """
        if self.master_id and self.master_id > 0:
            return str(self.master_id)
        return "r" + str(self.release_id)

    def filter_master(self, markup=True):
        s = (
            '<a href="'
            + url_for(
                "UnitModelView.list", _flt_3_discogs_master=self.discogs_master_key()
            )
            + '">Filter:Master</a>'
        )
        if markup:
            return Markup(s)
        else:
            return s

    def unit_list(self, markup=True):
        s = (
            '<a href="'
            + url_for("UnitModelView.list", _flt_0_discogs_release=str(self.release_id))
            + '">List Units</a>'
        )
        if markup:
            return Markup(s)
        else:
            return s

    def unit_add(self, instance_id, markup=True):
        grading = ""
        notes = json.loads(self.notes)
        for d in notes:
            if d["field_id"] == 1:
                m = re.search(r".*\((.*)\)", d["value"])
                if m:
                    grading = grading + m.group(1)
            elif d["field_id"] == 2:
                m = re.search(r".*\((.*)\)", d["value"])
                if m:
                    grading = grading + "/" + m.group(1)
        from fbc.models.inventory import Unit

        discogs_master = self.discogs_master_key()
        from fbc.identify import pressing_for, unit_name

        params = {
            "name": unit_name(self),
            "pressing": pressing_for(self),
            "unit_type": '12" LP',
            "grading": grading,
            "discogs_release": self.release_id,
            "discogs_instance": instance_id,
            "discogs_master": discogs_master,
        }
        avg_price = Unit.average_sold_price(discogs_master)
        if avg_price is not None:
            params["retail_price"] = f"{avg_price:.2f}"
        s = (
            '<a href="'
            + url_for("UnitModelView.add", **params)
            + '">Add Unit</a><br><span style="font-size:75%">('
            + str(instance_id)
            + ")</span>"
        )
        if markup:
            return Markup(s)
        else:
            return s

    def link_column(self):
        ret = self.release_show(False) + "<br>"
        ret += self.master_show(False) + "<br>"
        ret += self.filter_master(False) + "<br>"
        ret += self.unit_list(False) + "<br>"
        for discogs_instance in self.discogs_instances:
            ret += self.unit_add(discogs_instance.instance_id, False) + "<br>"
        return Markup(ret)

    def purchase_lot(self):
        from fbc.models.inventory import Unit
        from fbc import db

        ret = ""
        unit = db.session.query(Unit).filter_by(discogs_release=self).first()
        # A unit with no lot (or whose lot no longer exists) shows an empty cell.
        if unit and unit.purchase_lot:
            ret = unit.purchase_lot.name
        return Markup(ret)


class DiscogsOAuthToken(Model):
    """
    An OAuth access token and secret from Connect Discogs. Discogs's tokens
    don't expire; the newest row is the one used.
    """

    __tablename__ = "discogs_oauth_token"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(Text, nullable=False)
    secret = Column(Text, nullable=False)
    username = Column(String(100))
    created = Column(DateTime)

    def __repr__(self):
        return "DiscogsOAuthToken " + str(self.id)


class DiscogsListing(Model):
    """
    One item in the seller inventory on the Discogs Marketplace, as the
    Discogs listings sync last read it (fbc/syncjobs.py). The sync only reads:
    nothing here is ever sent back to Discogs.
    """

    __tablename__ = "discogs_listing"
    # Not an autoincrement: Discogs gives the ID. It's a BigInteger because
    # Discogs's IDs keep growing.
    listing_id = Column(BigInteger, primary_key=True, autoincrement=False)
    # Not a foreign key: a listing made by hand can be of a release that
    # isn't in the collection, so it isn't a DiscogsRelease.
    release_id = Column(Integer, index=True)
    release = relationship(
        "DiscogsRelease",
        primaryjoin="foreign(DiscogsListing.release_id) == DiscogsRelease.release_id",
        viewonly=True,
    )
    # Discogs's own description of the release, "Artist - Title".
    title = Column(String(255))
    # The Unit named by external_id ("fbc-<unit id>"), when there is one.
    unit_id = Column(Integer, ForeignKey("unit.id"), index=True)
    unit = relationship("Unit")
    price = Column(Float)
    currency = Column(String(3))
    condition = Column(String(30))
    sleeve_condition = Column(String(30))
    # Discogs's word: For Sale, Draft, Expired, Sold and so on. Only For Sale
    # is on the Marketplace; see is_for_sale.
    status = Column(String(20), index=True)
    # UTC, as Discogs sends it.
    posted = Column(DateTime)
    url = Column(String(255))
    # Shown on Discogs as the seller's private comments. fbc writes the
    # Unit's "fbc-<id>" there; a listing made by hand can have anything.
    external_id = Column(String(255))
    location = Column(String(255))
    # The listings sync sets these. last_seen is the UTC start of the last
    # sync that found the listing in the inventory. A listing that has left
    # the inventory (deleted on Discogs), or whose status is Sold or another one
    # it can't come back from, is marked ended, as eBay Listings are; see fbc/util.py mark_ended_discogs_listings.
    last_seen = Column(DateTime)
    ended = Column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    def __unicode__(self):
        return self.__repr__()

    def __repr__(self):
        return str(self.listing_id) + ": " + (self.title or "")

    def is_for_sale(self):
        """Live on the Marketplace now, as of the last sync."""
        return not self.ended and self.status == "For Sale"

    def fmt_price(self):
        if self.price is None:
            return ""
        return ("$" if self.currency in (None, "USD") else self.currency + " ") + (
            f"{self.price:.2f}"
        )

    def fmt_url(self):
        if not (self.url or "").startswith("https://"):
            return ""
        return Markup('<a href="{}" target="_blank" rel="noopener">Listing</a>').format(
            self.url
        )
