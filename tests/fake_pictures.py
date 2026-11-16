"""Stored pictures for tests of eBay Drafts."""

import datetime
import io

from PIL import Image

from fbc import db
from fbc.pictures import store


def jpeg(width=40, height=20, orientation=None, color="red"):
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG", exif=exif)
    return out.getvalue()


def give_pictures(draft, urls, uploaded=None):
    """
    Store a picture for the draft's Unit for each eBay address, as if it had
    been uploaded `uploaded` (a datetime, default now). Returns the pictures.
    """
    uploaded = uploaded or datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None
    )
    pictures = []
    for url in urls:
        picture = store.add(draft.unit, jpeg(), url.rsplit("/", 1)[-1])
        picture.ebay_url = url
        picture.ebay_uploaded_at = uploaded
        pictures.append(picture)
    db.session.commit()
    return pictures
