"""
Shared by the dev-scripts: sets up the app, refuses to touch anything but a
dev database, and builds the fake records.

Every fake record is easy to tell from a real one, so clear_fake.py can find
them all again:
  Discogs releases, artists and folders  ids from 900,000,000
  Discogs instances                       ids from 9,000,000,000
  eBay listings                           item ids starting 99, 12 digits
  eBay orders                             order ids like 99-00000-00001
  Units                                   Notes of FAKE_NOTE
"""

import datetime
import json
import logging
import os
import random
import sys

from sqlalchemy import func
from sqlalchemy.engine import make_url

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# The app and its database, once setup() has checked it's safe to load them.
app = None
db = None

FAKE_ID_START = 900_000_000
FAKE_INSTANCE_START = 9_000_000_000
FAKE_LISTING_PREFIX = "99"
FAKE_ORDER_PREFIX = "99-"
FAKE_NOTE = "Fake record from dev-scripts"

# The hosts a dev database is reached at: this machine, or the db service in
# docker/compose.dev.yml.
DEV_HOSTS = {None, "localhost", "127.0.0.1", "::1", "db"}

SIZES = {
    # record size: (Discogs format descriptions, Unit Type)
    '12"': (["LP", "Album"], '12" LP'),
    '10"': (['10"', "Album"], '10" LP'),
    '7"': (['7"', "Single", "45 RPM"], '7" Single'),
}
GRADES = [
    ("Mint (M)", "M"),
    ("Near Mint (NM or M-)", "NM"),
    ("Very Good Plus (VG+)", "VG+"),
    ("Very Good (VG)", "VG"),
    ("Good Plus (G+)", "G+"),
]
GENRES = {
    "Rock": ["Classic Rock", "Psychedelic Rock", "Prog Rock"],
    "Jazz": ["Hard Bop", "Cool Jazz", "Soul-Jazz"],
    "Electronic": ["Synth-pop", "Ambient", "House"],
    "Funk / Soul": ["Soul", "Funk", "Disco"],
    "Pop": ["Ballad", "Vocal", "Europop"],
}
WORDS = (
    "Velvet Static Midnight Paper Harbor Neon Copper Lantern Hollow Glass"
    " Orchid Thunder Silver Comet Echo Wild Quiet River Signal Golden Ember"
    " Ghost Electric Summer Winter Northern Sundown Crystal Arcade"
).split()
LABELS = ["Fakeway Records", "Nowhere Sound", "Dev Null Music", "Placeholder"]


def _settings():
    """The app's settings, from fbc/settings.py loaded on its own: importing
    it as fbc.settings would import the app first."""
    import importlib.util

    from flask import Config

    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    path = os.path.join(root, "fbc", "settings.py")
    spec = importlib.util.spec_from_file_location("_fbc_settings", path)
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    config = Config(root)
    settings.load(config)
    return config


def setup():
    """
    Load the app and push an app context, after checking the database is a
    dev one: config.py must set ALLOW_FAKE_DATA = True and point at a
    database on this machine or in docker/compose.dev.yml. Exits otherwise.

    The check reads the settings itself, before the app is imported, because
    importing the app already connects to the database and sets up its
    security tables. It reads them as the app does, FBC_CONFIG_* overrides
    included, so it checks the database the app will use.
    """
    global app, db
    config = _settings()

    url = make_url(config["SQLALCHEMY_DATABASE_URI"])
    if not config.get("ALLOW_FAKE_DATA", False):
        sys.exit(
            "Refusing to add fake records: config.py doesn't set"
            " ALLOW_FAKE_DATA = True. Only set it in a dev config."
        )
    if url.host not in DEV_HOSTS:
        sys.exit(
            "Refusing to add fake records: the database is on "
            + str(url.host)
            + ", which isn't this machine or docker/compose.dev.yml's db."
        )
    # fbc logs every route it registers at DEBUG as it's imported; keep the
    # output to the script's own.
    logging.disable(logging.INFO)
    from fbc import app, db

    logging.disable(logging.NOTSET)
    logging.getLogger().setLevel(logging.WARNING)
    app.app_context().push()
    print("Using the " + str(url.database) + " database on " + str(url.host or "-"))


def next_id(column, start):
    """The next free fake id in `column`, from `start`."""
    highest = db.session.query(func.max(column)).filter(column >= start).scalar()
    return start if highest is None else highest + 1


def next_number(column, prefix):
    """
    The next free running number for fake string ids in `column`: the digits
    after `prefix`, dashes left out.
    """
    codes = db.session.query(column).filter(column.like(prefix + "%")).all()
    numbers = [int(code[len(prefix) :].replace("-", "")) for (code,) in codes]
    return max(numbers, default=0) + 1


def listing_id(n):
    return FAKE_LISTING_PREFIX + f"{n:010d}"


def order_id(n):
    return FAKE_ORDER_PREFIX + f"{n // 100000:05d}-{n % 100000:05d}"


def phrase(words=2):
    return " ".join(random.sample(WORDS, words))


def pick_size(size):
    """`size`, or a random record size (mostly 12") for "mix"."""
    if size != "mix":
        return size
    return random.choices(list(SIZES), weights=[6, 1, 3])[0]


def fake_folder():
    """The Folder fake releases go in, made the first time."""
    from fbc.models.discogs import Folder

    folder = db.session.query(Folder).filter(Folder.folder_id >= FAKE_ID_START).first()
    if not folder:
        folder = Folder(folder_id=FAKE_ID_START, name="Fake Releases")
        db.session.add(folder)
    return folder


def fake_release(size="mix", instances=1):
    """
    A fake DiscogsRelease of `size`, in the collection `instances` times,
    with an artist, genre and style. Real genre and style names are used,
    and shared with real releases.
    """
    from fbc.models.discogs import (
        Artist,
        DiscogsInstance,
        DiscogsRelease,
        Genre,
        Style,
    )

    size = pick_size(size)
    artist = Artist(
        artist_id=next_id(Artist.artist_id, FAKE_ID_START),
        name=phrase() + " (fake)",
        sort_name="",
    )
    db.session.add(artist)
    genre_name = random.choice(list(GENRES))
    genre = db.session.query(Genre).filter_by(name=genre_name).first() or Genre(
        name=genre_name
    )
    style_name = random.choice(GENRES[genre_name])
    style = db.session.query(Style).filter_by(name=style_name).first() or Style(
        name=style_name
    )
    first_instance = next_id(DiscogsInstance.instance_id, FAKE_INSTANCE_START)
    media, sleeve = random.choice(GRADES[1:4]), random.choice(GRADES[1:5])
    year = random.randint(1958, 1995)
    release = DiscogsRelease(
        release_id=next_id(DiscogsRelease.release_id, FAKE_ID_START),
        title=phrase(random.randint(1, 3)),
        artists=[artist],
        year=year,
        genres=[genre],
        styles=[style],
        master_id=0,
        master_year=year,
        formats=json.dumps(
            [{"name": "Vinyl", "qty": "1", "descriptions": SIZES[size][0]}]
        ),
        labels=json.dumps(
            [
                {
                    "name": random.choice(LABELS),
                    "catno": "FAKE-" + str(random.randint(100, 999)),
                }
            ]
        ),
        country="US",
        discogs_instances=[
            DiscogsInstance(instance_id=first_instance + i) for i in range(instances)
        ],
        released=str(year),
        notes=json.dumps(
            [
                {"field_id": 1, "value": media[0]},
                {"field_id": 2, "value": sleeve[0]},
            ]
        ),
        folder=fake_folder(),
        date_added=datetime.date.today(),
    )
    db.session.add(release)
    db.session.flush()
    return release


def fake_unit(release=None, size="mix"):
    """
    A fake unsold Unit: of `release` and its first instance when given,
    otherwise of no release at all.
    """
    from fbc.models.inventory import Unit

    if release:
        notes = {n["field_id"]: n["value"] for n in json.loads(release.notes)}
        grading = dict(GRADES)[notes[1]] + "/" + dict(GRADES)[notes[2]]
        descriptions = json.loads(release.formats)[0]["descriptions"]
        size = next((s for s, (d, _) in SIZES.items() if d == descriptions), '12"')
        name = str(release)
    else:
        size = pick_size(size)
        grading = random.choice(GRADES[1:4])[1]
        name = phrase() + " (fake) - " + phrase()
    unit = Unit(
        name=name,
        unit_type=SIZES[size][1],
        discogs_release=release,
        discogs_instance=release.discogs_instances[0] if release else None,
        discogs_master=release.discogs_master_key() if release else "",
        grading=grading,
        pressing="",
        matrix="",
        notes=FAKE_NOTE,
        retail_price=round(random.uniform(8, 60), 2),
        sold=False,
    )
    db.session.add(unit)
    db.session.flush()
    return unit


def fake_listing(unit=None):
    """A fake EbayListing, for `unit` when given."""
    from fbc.models.ebay import EbayListing

    item_id = listing_id(next_number(EbayListing.item_id, FAKE_LISTING_PREFIX))
    if unit:
        title = (unit.name[:70] + " " + unit.grading).strip()
        price = unit.retail_price
    else:
        title = phrase() + " (fake) - " + phrase() + " LP"
        price = round(random.uniform(8, 60), 2)
    listing = EbayListing(
        item_id=item_id,
        title=title[:100],
        price=price,
        url="https://www.ebay.com/itm/" + item_id,
        start_time=datetime.datetime.now().replace(microsecond=0)
        - datetime.timedelta(days=random.randint(1, 90)),
    )
    db.session.add(listing)
    if unit:
        unit.ebay_listing = listing
    db.session.flush()
    return listing


def fake_order(listing):
    """A fake EbayOrder for `listing`, placed in the last few days."""
    from fbc.models.ebay import EbayOrder

    order = EbayOrder(
        order_id=order_id(next_number(EbayOrder.order_id, FAKE_ORDER_PREFIX)),
        created_time=datetime.datetime.now().replace(microsecond=0)
        - datetime.timedelta(hours=random.randint(1, 96)),
        buyer="fake-buyer-" + str(random.randint(100, 999)),
        price=listing.price,
        title=listing.title,
        ebay_listing=listing,
    )
    db.session.add(order)
    db.session.flush()
    return order
