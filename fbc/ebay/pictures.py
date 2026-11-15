"""Uploading, rotating and fetching pictures for eBay listings."""

import logging

from fbc.core import devmode
from fbc.ebay import api, oauth

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


# JPEG quality for pictures fbc re-saves (a rotation or an EXIF fix), high so
# the extra save costs little.
PICTURE_QUALITY = 95


def _save_picture(image, fmt):
    """Encode a Pillow image in its original format; returns a BytesIO."""
    import io

    out = io.BytesIO()
    kwargs = {}
    if fmt == "JPEG":
        kwargs = {"quality": PICTURE_QUALITY, "subsampling": 0}
        if image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
    if image.info.get("icc_profile"):
        kwargs["icc_profile"] = image.info["icc_profile"]
    image.save(out, format=fmt, **kwargs)
    out.seek(0)
    return out


def upright_picture(fileobj):
    """
    Return a file with the picture's pixels the right way up. Phones often
    store a photo sideways and set an EXIF orientation tag that some viewers
    ignore; this applies the tag and drops it. A picture Pillow can't read
    is passed on as it is, for eBay to judge.
    """
    from PIL import Image, ImageOps

    try:
        image = Image.open(fileobj)
        fmt = image.format
        if (
            fmt not in ("JPEG", "PNG", "WEBP", "GIF")
            or image.getexif().get(274, 1) == 1
        ):
            fileobj.seek(0)
            return fileobj
        return _save_picture(ImageOps.exif_transpose(image), fmt)
    except Exception as e:
        logger.warning("Couldn't straighten a picture: " + str(e))
        fileobj.seek(0)
        return fileobj


def rotate_picture(data, direction):
    """
    Rotate picture bytes 90 degrees, "left" (anticlockwise) or "right".
    Returns (BytesIO, content type). Raises ValueError if it isn't a picture
    Pillow can read.
    """
    import io

    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        fmt = image.format
        image = ImageOps.exif_transpose(image)
    except Exception as e:
        raise ValueError("Couldn't read the picture: " + str(e))
    if fmt not in ("JPEG", "PNG", "WEBP", "GIF"):
        raise ValueError("Can't rotate a " + str(fmt) + " picture.")
    turn = (
        Image.Transpose.ROTATE_90 if direction == "left" else Image.Transpose.ROTATE_270
    )
    return _save_picture(image.transpose(turn), fmt), Image.MIME[fmt]


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
