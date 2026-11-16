"""
A Unit's pictures on disk. The folder is the PICTURES_DIR setting, with one
folder inside it per Unit (`unit-123/`). Each picture is two files, the
upload as it came and a working copy (fbc/pictures/images.py), named by a
random token, never by what the person called the file: a name is only for
people to read, and the Discogs page makes real ones (`unit-123-1.jpg`) when
it serves a picture.

Nothing is changed in place. Rotating writes new files and removes the old
ones, so a copy of the folder taken while the app runs is never half of a
picture, and needn't be taken in step with the database.

Functions here add and remove rows but leave the commit to the caller.
"""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from fbc.models.pictures import UnitPicture
from fbc.pictures import images

logger = logging.getLogger()


class NotConfigured(Exception):
    """PICTURES_DIR isn't set, so there's nowhere to keep pictures."""


def folder():
    """The pictures folder as a Path, or None if PICTURES_DIR isn't set."""
    setting = current_app.config.get("PICTURES_DIR")
    return Path(setting) if setting else None


def _unit_folder(unit_id, create=False):
    base = folder()
    if base is None:
        raise NotConfigured(
            "Set PICTURES_DIR in config.py to keep pictures (see docs/production.md)."
        )
    path = base / ("unit-" + str(int(unit_id)))
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def path(picture):
    """Where a picture's working copy is."""
    return _unit_folder(picture.unit_id) / picture.file


def original_path(picture):
    """Where a picture's original upload is."""
    return _unit_folder(picture.unit_id) / picture.original_file


def for_unit(unit):
    """A Unit's pictures, the main one first."""
    return (
        UnitPicture.query.filter_by(unit_id=unit.id)
        .order_by(UnitPicture.position, UnitPicture.id)
        .all()
    )


def _write(target, data):
    # Write beside it and rename, so a reader never sees half a file.
    temp = target.with_name(target.name + ".part")
    temp.write_bytes(data)
    temp.replace(target)


def _unlink(*paths):
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Couldn't remove " + str(p) + ": " + str(e))


def add(unit, data, upload_name=None):
    """
    Keep a picture (its bytes) as the Unit's last one: the original as it
    came and a working copy. Returns the new UnitPicture. Raises ValueError
    if it isn't a picture fbc can read, and NotConfigured without a folder.
    """
    from fbc import db

    working, fmt = images.working_copy(data)
    where = _unit_folder(unit.id, create=True)
    token = secrets.token_hex(8)
    file = token + ".jpg"
    original_file = token + "-original." + images.EXTENSIONS[fmt]
    _write(where / original_file, data)
    _write(where / file, working.getvalue())
    last = max((p.position for p in for_unit(unit)), default=0)
    picture = UnitPicture(
        unit_id=unit.id,
        position=last + 1,
        file=file,
        original_file=original_file,
        upload_name=(upload_name or "")[:255] or None,
    )
    db.session.add(picture)
    db.session.flush()
    return picture


def _renumber(ordered):
    for position, picture in enumerate(ordered, start=1):
        picture.position = position


def remove(picture):
    """Delete a picture's row and files, and close the gap in the order."""
    from fbc import db

    unit_id = picture.unit_id
    paths = [path(picture), original_path(picture)]
    db.session.delete(picture)
    db.session.flush()
    rest = (
        UnitPicture.query.filter_by(unit_id=unit_id)
        .order_by(UnitPicture.position, UnitPicture.id)
        .all()
    )
    _renumber(rest)
    _unlink(*paths)


def make_first(picture):
    """Make this the Unit's main picture, the others keeping their order."""
    rest = [p for p in for_unit(picture.unit) if p.id != picture.id]
    _renumber([picture] + rest)


def rotate(picture, direction):
    """
    Turn a picture 90 degrees, "left" or "right": both files, into new files,
    then the old ones go. Its eBay URL no longer shows the picture, so it's
    forgotten. Raises ValueError if Pillow can't read either file.
    """
    where = _unit_folder(picture.unit_id)
    old = [where / picture.file, where / picture.original_file]
    try:
        working = old[0].read_bytes()
        original = old[1].read_bytes()
    except OSError as e:
        raise ValueError("Couldn't read the stored picture: " + str(e))
    turned_working, _ = images.rotate_picture(working, direction)
    turned_original, _ = images.rotate_picture(original, direction)
    token = secrets.token_hex(8)
    new_file = token + old[0].suffix
    new_original = token + "-original" + old[1].suffix
    _write(where / new_file, turned_working.getvalue())
    _write(where / new_original, turned_original.getvalue())
    picture.file = new_file
    picture.original_file = new_original
    picture.ebay_url = None
    picture.ebay_uploaded_at = None
    _unlink(*old)


def set_ebay_url(picture, url):
    """Remember where eBay's picture hosting has this picture."""
    picture.ebay_url = url
    picture.ebay_uploaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
