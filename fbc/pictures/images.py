"""
Reading, straightening, rotating and shrinking pictures with Pillow. Nothing
here touches the disk, the database, eBay or Discogs.
"""

import logging

logger = logging.getLogger()

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


# The long side of the working copy fbc keeps of each picture. eBay wants at
# least 1600 for its zoom; 2048 leaves room, and is far under its 12 MB limit.
WORKING_SIZE = 2048
WORKING_QUALITY = 90

# The formats fbc keeps, and the extension each is saved with.
EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


def working_copy(data):
    """
    Make the working copy of a picture's bytes: the right way up, no more
    than WORKING_SIZE on the long side, as a JPEG. Returns
    (BytesIO, format of the original). Raises ValueError if it isn't a
    picture fbc keeps.
    """
    import io

    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        fmt = image.format
        if fmt not in EXTENSIONS:
            raise ValueError("Can't keep a " + str(fmt) + " picture.")
        image = ImageOps.exif_transpose(image)
        image.load()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("Couldn't read the picture: " + str(e))
    image.thumbnail((WORKING_SIZE, WORKING_SIZE), Image.Resampling.LANCZOS)
    if image.mode in ("RGBA", "LA", "P"):
        # A JPEG has no transparency; show it on white, as a browser would.
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    out = io.BytesIO()
    kwargs = {"quality": WORKING_QUALITY, "subsampling": 0}
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if image.info.get("icc_profile"):
        kwargs["icc_profile"] = image.info["icc_profile"]
    image.save(out, format="JPEG", **kwargs)
    out.seek(0)
    return out, fmt
