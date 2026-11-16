import io
import zipfile

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from auth import signed_in_client
from fake_pictures import jpeg
from PIL import Image
from test_listing import SETTINGS, _carnaval
from test_migrations import _migration

import fbc
from fbc import db
from fbc.models.ebay import EbayDraft
from fbc.models.inventory import Unit
from fbc.pictures import store


@pytest.fixture
def client(app):
    return signed_in_client(app)


@pytest.fixture
def unit(db):
    unit = Unit(name="Spyro Gyra - Carnaval")
    db.session.add(unit)
    db.session.commit()
    return unit


def _add(unit, *colors, size=(60, 40)):
    pictures = [
        store.add(unit, jpeg(*size, color=color), color + ".jpg") for color in colors
    ]
    db.session.commit()
    return pictures


def _path(unit, n):
    return "/unitpictures/file/%d/unit-%d-%d.jpg" % (unit.id, unit.id, n)


def _size(data):
    return Image.open(io.BytesIO(data)).size


def test_the_page_shows_each_picture_full_size_under_its_real_name(client, unit):
    _add(unit, "red", "blue")

    page = client.get("/unitpictures/show/%d" % unit.id)
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert "unit-%d-1.jpg" % unit.id in html and "unit-%d-2.jpg" % unit.id in html
    assert 'src="%s"' % _path(unit, 1) in html
    # Thumbnails would upload thumbnails.
    assert "img-thumbnail" not in html and "img-responsive" not in html
    assert "max-width: none" in html
    assert "Download all (zip)" in html
    assert "from red.jpg" in html


def test_the_page_with_no_pictures_says_so_and_has_no_zip_link(client, unit):
    html = client.get("/unitpictures/show/%d" % unit.id).get_data(as_text=True)

    assert "No pictures yet." in html
    assert "/unitpictures/zip/" not in html


def test_an_unknown_unit_is_a_404(client):
    assert client.get("/unitpictures/show/999").status_code == 404
    assert client.post("/unitpictures/add/999").status_code == 404


def test_a_picture_is_served_whole_under_its_name(client, unit):
    (picture,) = _add(unit, "red")

    served = client.get(_path(unit, 1))

    assert served.status_code == 200
    assert served.mimetype == "image/jpeg"
    assert "filename=unit-%d-1.jpg" % unit.id in served.headers["Content-Disposition"]
    assert served.data == store.path(picture).read_bytes()
    assert _size(served.data) == (60, 40)
    assert (
        "no-cache" in served.headers["Cache-Control"]
        or "max-age=0" in (served.headers["Cache-Control"])
    )


def test_a_picture_url_with_a_wrong_name_or_place_is_a_404(client, unit):
    _add(unit, "red")
    other = Unit(name="Another")
    db.session.add(other)
    db.session.commit()
    _add(other, "blue")
    base = "/unitpictures/file/%d/" % unit.id

    assert client.get(base + "unit-%d-2.jpg" % unit.id).status_code == 404
    assert client.get(base + "unit-%d-0.jpg" % unit.id).status_code == 404
    assert client.get(base + "unit-%d-1.jpg" % other.id).status_code == 404
    assert client.get(base + "red.jpg").status_code == 404
    assert client.get(base + "..%2Funit-1-1.jpg").status_code == 404
    assert client.get(base + "unit-%d-1.png" % unit.id).status_code == 404


def test_the_zip_holds_every_picture_named_in_order(client, unit):
    first, second = _add(unit, "red", "blue")

    response = client.get("/unitpictures/zip/%d" % unit.id)

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert (
        "attachment; filename=unit-%d-pictures.zip" % unit.id
        in response.headers["Content-Disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == [
            "unit-%d-1.jpg" % unit.id,
            "unit-%d-2.jpg" % unit.id,
        ]
        assert archive.read("unit-%d-1.jpg" % unit.id) == store.path(first).read_bytes()
        assert (
            archive.read("unit-%d-2.jpg" % unit.id) == store.path(second).read_bytes()
        )
        assert archive.testzip() is None


def test_the_zip_follows_a_new_main_picture(client, unit):
    first, second = _add(unit, "red", "blue")
    client.post("/unitpictures/picture/%d/1/first" % unit.id)

    with zipfile.ZipFile(
        io.BytesIO(client.get("/unitpictures/zip/%d" % unit.id).data)
    ) as archive:
        assert (
            archive.read("unit-%d-1.jpg" % unit.id) == store.path(second).read_bytes()
        )


def test_a_zip_of_no_pictures_is_refused_with_a_message(client, unit):
    page = client.get("/unitpictures/zip/%d" % unit.id, follow_redirects=True)

    assert "This Unit has no pictures." in page.get_data(as_text=True)


def test_adding_pictures_from_the_unit_page(client, unit):
    client.post(
        "/unitpictures/add/%d" % unit.id,
        data={
            "pictures": [
                (io.BytesIO(jpeg(60, 40, orientation=6)), "sideways.jpg"),
                (io.BytesIO(b"junk"), "bad.jpg"),
            ]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    (picture,) = store.for_unit(unit)
    assert picture.upload_name == "sideways.jpg"
    assert _size(store.path(picture).read_bytes()) == (40, 60)


def test_a_non_picture_is_refused_with_its_name(client, unit):
    page = client.post(
        "/unitpictures/add/%d" % unit.id,
        data={"pictures": [(io.BytesIO(b"junk"), "bad.jpg")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "bad.jpg: Couldn&#39;t read the picture" in page.get_data(as_text=True)
    assert store.for_unit(unit) == []


def test_make_main_and_remove(client, unit):
    first, second, third = _add(unit, "red", "blue", "green")

    client.post("/unitpictures/picture/%d/2/first" % unit.id)
    assert [p.id for p in store.for_unit(unit)] == [third.id, first.id, second.id]

    gone = [store.path(first), store.original_path(first)]
    client.post("/unitpictures/picture/%d/1/remove" % unit.id)
    assert [p.id for p in store.for_unit(unit)] == [third.id, second.id]
    assert not any(p.exists() for p in gone)

    assert client.post("/unitpictures/picture/%d/0/burn" % unit.id).status_code == 400
    assert client.post("/unitpictures/picture/%d/9/first" % unit.id).status_code == 404


@pytest.mark.parametrize("direction", ["left", "right"])
def test_rotate_turns_the_stored_files(client, unit, direction):
    _add(unit, "red", "blue")

    client.post("/unitpictures/rotate/%d/1/%s" % (unit.id, direction))

    first, second = store.for_unit(unit)
    assert _size(store.path(first).read_bytes()) == (60, 40)
    assert _size(store.path(second).read_bytes()) == (40, 60)
    assert _size(store.original_path(second).read_bytes()) == (40, 60)
    assert client.post("/unitpictures/rotate/%d/0/up" % unit.id).status_code == 400
    assert client.post("/unitpictures/rotate/%d/7/left" % unit.id).status_code == 404


def test_rotating_an_unreadable_file_says_so(client, unit):
    (picture,) = _add(unit, "red")
    store.path(picture).write_bytes(b"junk")

    page = client.post(
        "/unitpictures/rotate/%d/0/left" % unit.id, follow_redirects=True
    )

    assert "Couldn&#39;t read the picture" in page.get_data(as_text=True)


def test_without_a_folder_the_page_says_so_and_adding_fails(
    client, unit, app, monkeypatch
):
    monkeypatch.setitem(app.config, "PICTURES_DIR", None)

    html = client.get("/unitpictures/show/%d" % unit.id).get_data(as_text=True)
    page = client.post(
        "/unitpictures/add/%d" % unit.id,
        data={"pictures": [(io.BytesIO(jpeg()), "a.jpg")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "PICTURES_DIR isn" in html
    assert "Set PICTURES_DIR in config.py" in page.get_data(as_text=True)


def test_signed_out_is_sent_to_the_login_page(app, unit):
    anonymous = app.test_client()

    for method, path in [
        ("get", "/unitpictures/show/%d" % unit.id),
        ("get", "/unitpictures/zip/%d" % unit.id),
        ("get", _path(unit, 1)),
        ("post", "/unitpictures/add/%d" % unit.id),
        ("post", "/unitpictures/picture/%d/0/remove" % unit.id),
        ("post", "/unitpictures/rotate/%d/0/left" % unit.id),
    ]:
        response = getattr(anonymous, method)(path)
        assert response.status_code == 302, path
        assert "/login/" in response.headers["Location"], path


@pytest.fixture
def full_unit(app, monkeypatch):
    """A Unit with a release, as the draft and list pages need."""
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    return _carnaval()


def test_pictures_added_here_are_the_drafts_and_the_other_way_round(client, full_unit):
    unit = full_unit
    client.get("/ebaydraftmodelview/fromunit/%d" % unit.id)
    draft = db.session.query(EbayDraft).one()

    client.post(
        "/unitpictures/add/%d" % unit.id,
        data={"pictures": [(io.BytesIO(jpeg()), "one.jpg")]},
        content_type="multipart/form-data",
    )
    client.post(
        "/ebaydraftmodelview/pictures/%d" % draft.id,
        data={"pictures": [(io.BytesIO(jpeg()), "two.jpg")]},
        content_type="multipart/form-data",
    )

    assert [p.upload_name for p in draft.stored_pictures()] == ["one.jpg", "two.jpg"]
    html = client.get("/unitpictures/show/%d" % unit.id).get_data(as_text=True)
    assert "two.jpg" in html
    draft_page = client.get("/ebaydraftmodelview/show/%d" % draft.id).get_data(
        as_text=True
    )
    assert "/unitpictures/show/%d" % unit.id in draft_page


def test_edit_unit_and_the_unit_list_link_to_the_pictures_page(client, full_unit):
    unit = full_unit
    edit = client.get("/unitmodelview/edit/%d" % unit.id).get_data(as_text=True)
    listing = client.get("/unitmodelview/list/").get_data(as_text=True)

    assert "/unitpictures/show/%d" % unit.id in edit
    assert "/unitpictures/show/%d" % unit.id in listing


def test_dropping_ebay_draft_pictures_keeps_the_rest_of_the_row():
    migration = _migration("f7c2a9d1e485_drop_ebay_draft_pictures")
    engine = sa.create_engine("sqlite://")
    draft = sa.Table(
        "ebay_draft",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("pictures", sa.Text),
    )
    with engine.begin() as conn:
        draft.create(conn)
        conn.execute(draft.insert().values(id=1, title="Carnaval", pictures='["a"]'))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("ebay_draft")] == [
            "id",
            "title",
        ]
        assert conn.execute(sa.text("SELECT id, title FROM ebay_draft")).one() == (
            1,
            "Carnaval",
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "pictures" in [
            c["name"] for c in sa.inspect(conn).get_columns("ebay_draft")
        ]
