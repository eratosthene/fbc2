"""Uploading, rotating and fetching pictures for eBay listings."""

import logging

from fbc.core import devmode
from fbc.ebay import api, oauth

# The picture handling that isn't eBay's moved to fbc/pictures/images.py;
# the eBay views and tests still reach it here.
from fbc.pictures.images import rotate_picture, upright_picture  # noqa: F401

logger = logging.getLogger()


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
