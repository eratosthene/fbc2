import logging
from flask_appbuilder import Model
from markupsafe import Markup
from flask import url_for
from sqlalchemy import Column, Integer, String, ForeignKey, Date, Text
from sqlalchemy.orm import relationship
import re
import json

logger = logging.getLogger()


class Artist(Model):
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
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Style(Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class Folder(Model):
    folder_id = Column(Integer, primary_key=True, unique=True)
    name = Column(String(100), nullable=False)

    def __unicode__(self):
        return self.name

    def __repr__(self):
        return self.name


class DiscogsInstance(Model):
    instance_id = Column(Integer, primary_key=True, unique=True)

    def __unicode__(self):
        return str(self.instance_id)

    def __repr__(self):
        return str(self.instance_id)


class DiscogsRelease(Model):
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
        Column("instance_id", Integer, ForeignKey("discogs_instance.instance_id")),
        Column("release_id", Integer, ForeignKey("discogs_release.release_id")),
        keep_existing=True,
    )

    release_id = Column(Integer, primary_key=True, unique=True)
    title = Column(String(200), nullable=False, index=True)
    artists = relationship(
        "Artist", secondary=assoc_artist_discogs_release, backref="discogs_release"
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
                m = re.search(".*\((.*)\)", d["value"])
                if m:
                    grading = grading + m.group(1)
            elif d["field_id"] == 2:
                m = re.search(".*\((.*)\)", d["value"])
                if m:
                    grading = grading + "/" + m.group(1)
        s = (
            '<a href="'
            + url_for(
                "UnitModelView.add",
                name=str(self),
                unit_type='12" LP',
                grading=grading,
                discogs_release=self.release_id,
                discogs_instance=instance_id,
            )
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
        ret += self.unit_list(False) + "<br>"
        for discogs_instance in self.discogs_instances:
            ret += self.unit_add(discogs_instance.instance_id, False) + "<br>"
        return Markup(ret)

    def purchase_lot(self):
        from fbc.models.inventory import Unit
        from fbc import db

        ret = ""
        unit = db.session.query(Unit).filter_by(discogs_release=self).first()
        if unit:
            ret = unit.purchase_lot.name
        return Markup(ret)
