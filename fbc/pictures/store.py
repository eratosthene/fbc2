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

from fbc.pictures import images

logger = logging.getLogger()


def _model():
    # Imported when used, not at the top: this module is reached while fbc
    # itself is being imported, and a model that names the unit table can't
    # load before the other models have (see fbc/models/pictures.py).
    from fbc.models.pictures import UnitPicture

    return UnitPicture


# eBay allows up to 24 pictures on a listing, and a Unit's pictures go to it.
MAX_PICTURES = 24


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
    return for_unit_id(unit.id)


def for_unit_id(unit_id):
    return (
        _model()
        .query.filter_by(unit_id=unit_id)
        .order_by(_model().position, _model().id)
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
    picture = _model()(
        unit_id=unit.id,
        position=last + 1,
        file=file,
        original_file=original_file,
        upload_name=(upload_name or "")[:255] or None,
    )
    db.session.add(picture)
    db.session.flush()
    return picture


def add_files(unit, files):
    """
    Keep uploaded files (werkzeug FileStorage) as the Unit's next pictures,
    up to MAX_PICTURES. A file that isn't a picture is refused and the rest are
    kept. Returns [(message, level)] for the page to flash.
    """
    messages = []
    count = len(for_unit(unit))
    for f in files:
        if not f.filename:
            continue
        if count >= MAX_PICTURES:
            messages.append(
                ("eBay allows at most " + str(MAX_PICTURES) + " pictures.", "warning")
            )
            break
        try:
            add(unit, f.read(), f.filename)
        except NotConfigured as e:
            messages.append((str(e), "danger"))
            break
        except ValueError as e:
            messages.append((f.filename + ": " + str(e), "danger"))
            continue
        count += 1
    return messages


def display_name(unit_id, position):
    """The name a picture is shown and downloaded under, such as unit-123-1.jpg."""
    return "unit-" + str(int(unit_id)) + "-" + str(int(position)) + ".jpg"


def zip_of(unit):
    """
    The Unit's working copies in a zip, named unit-123-1.jpg and so on in
    their order. Returns a BytesIO, or None if the Unit has no pictures.
    """
    import io
    import zipfile

    pictures = for_unit(unit)
    if not pictures:
        return None
    out = io.BytesIO()
    # The pictures are JPEGs already, so storing them as they are loses nothing.
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for picture in pictures:
            archive.write(path(picture), display_name(unit.id, picture.position))
    out.seek(0)
    return out


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
        _model()
        .query.filter_by(unit_id=unit_id)
        .order_by(_model().position, _model().id)
        .all()
    )
    _renumber(rest)
    _unlink(*paths)


def make_first(picture):
    """Make this the Unit's main picture, the others keeping their order."""
    rest = [p for p in for_unit_id(picture.unit_id) if p.id != picture.id]
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
