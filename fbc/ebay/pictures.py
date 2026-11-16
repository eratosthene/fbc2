"""Sending a Unit's stored pictures to eBay, and fetching pictures from it."""

import datetime
import logging

from fbc.core import devmode
from fbc.ebay import api, oauth

# The picture handling that isn't eBay's moved to fbc/pictures/images.py;
# the eBay views and tests still reach it here.
from fbc.pictures import store
from fbc.pictures.images import rotate_picture, upright_picture  # noqa: F401

logger = logging.getLogger()

# How long fbc trusts the eBay address it saved for a picture. I haven't
# confirmed how long eBay keeps an uploaded picture that no listing uses, so
# an older address is uploaded again before publishing: it costs only the
# upload, and the stored file is the real copy.
EBAY_URL_DAYS = 30


def upload_picture(fileobj, filename, content_type=None):
    """
    Upload one picture to eBay Picture Services through the Media API's
    createImageFromFile (Trading's UploadSiteHostedPictures was retired on
    2026-09-30). Returns (url, summary); url is None if the upload failed.
    """
    import requests

    summary = api._empty_summary()
    try:
        token = oauth.oauth_token()
    except oauth.OAuthError as e:
        summary["errors"].append(str(e))
        return None, summary
    headers = {"Authorization": "Bearer " + token}
    try:
        r = requests.post(
            oauth.MEDIA_URL,
            headers=headers,
            files={
                "image": (filename, fileobj, content_type or "application/octet-stream")
            },
            timeout=120,
        )
    except requests.RequestException as e:
        logger.error(e)
        summary["errors"].append(
            "Couldn't reach eBay to upload " + filename + ": " + str(e)
        )
        return None, summary
    if r.status_code not in (200, 201):
        if r.status_code == 401:
            # A revoked token; fetch a new one next time.
            oauth.forget_access_token(oauth.MEDIA_SCOPE)
        summary["errors"].extend(
            "Uploading " + filename + ": " + m for m in oauth._rest_errors(r)
        )
        return None, summary
    try:
        url = r.json().get("imageUrl")
    except ValueError:
        url = None
    if not url and r.headers.get("Location"):
        # The image's details live at the Location the create call returns.
        image = requests.get(r.headers["Location"], headers=headers, timeout=30)
        if image.status_code == 200:
            url = image.json().get("imageUrl")
    if not url:
        summary["errors"].append(
            "eBay accepted "
            + filename
            + " but didn't say where it is: "
            + oauth._http_error(r)
        )
    return url, summary


def fetch_picture(url):
    """Download a picture from its eBay URL. Returns (bytes, error)."""
    import requests

    if devmode.on():
        return None, devmode.message("fetching pictures")
    try:
        r = requests.get(url, timeout=30)
    except requests.RequestException as e:
        logger.error(e)
        return None, "Couldn't fetch the picture from eBay: " + str(e)
    if r.status_code != 200:
        return None, "eBay's picture host answered " + str(r.status_code) + "."
    return r.content, None


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _current(picture, now):
    if not picture.ebay_url or not picture.ebay_uploaded_at:
        return False
    return now - picture.ebay_uploaded_at < datetime.timedelta(days=EBAY_URL_DAYS)


def ensure_uploaded(draft):
    """
    Make sure eBay has every stored picture of the draft's Unit, uploading the
    working copy of any that has no address yet (never sent, turned since, or
    saved long ago) and saving the address it gets. An upload that worked is
    kept even if a later one fails. Returns a summary; its errors say what
    couldn't be sent, and eBay can't list the draft until there are none.
    """
    from fbc import db

    summary = api._empty_summary()
    now = _utcnow()
    todo = [p for p in draft.stored_pictures() if not _current(p, now)]
    if todo and devmode.on():
        summary["errors"].append(devmode.message("uploading pictures to eBay"))
        return summary
    for picture in todo:
        name = "unit-" + str(picture.unit_id) + "-" + str(picture.position) + ".jpg"
        try:
            with open(store.path(picture), "rb") as f:
                url, result = upload_picture(f, name, "image/jpeg")
        except (OSError, store.NotConfigured) as e:
            summary["errors"].append("Couldn't read " + name + ": " + str(e))
            continue
        summary["errors"].extend(result["errors"])
        summary["warnings"].extend(result["warnings"])
        if url:
            store.set_ebay_url(picture, url)
            db.session.commit()
    return summary
