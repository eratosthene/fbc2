import io

import pytest
from PIL import Image

from fbc.models.inventory import Unit
from fbc.models.pictures import UnitPicture
from fbc.pictures import images, store


def _jpeg(size=(400, 300), orientation=None, color="red"):
    out = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    Image.new("RGB", size, color).save(out, "JPEG", exif=exif)
    return out.getvalue()


def _png(size=(50, 40), mode="RGBA"):
    out = io.BytesIO()
    Image.new(mode, size, (0, 0, 255, 0) if mode == "RGBA" else "blue").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def folder(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "PICTURES_DIR", str(tmp_path / "pictures"))
    return tmp_path / "pictures"


@pytest.fixture
def unit(db):
    unit = Unit(name="A record")
    db.session.add(unit)
    db.session.commit()
    return unit


def _size(path):
    with Image.open(path) as image:
        return image.size


def test_working_copy_shrinks_a_big_picture_to_2048_on_the_long_side():
    working, fmt = images.working_copy(_jpeg((4000, 3000)))
    assert fmt == "JPEG"
    assert Image.open(working).size == (2048, 1536)


def test_working_copy_does_not_enlarge_a_small_picture():
    working, _ = images.working_copy(_jpeg((400, 300)))
    assert Image.open(working).size == (400, 300)


def test_working_copy_applies_the_exif_orientation():
    # Orientation 6 means the phone held it sideways: 400x300 shows as 300x400.
    working, _ = images.working_copy(_jpeg((400, 300), orientation=6))
    image = Image.open(working)
    assert image.size == (300, 400)
    assert image.getexif().get(274, 1) == 1


def test_working_copy_flattens_transparency_onto_white():
    working, fmt = images.working_copy(_png())
    assert fmt == "PNG"
    image = Image.open(working)
    assert image.format == "JPEG"
    assert image.getpixel((1, 1)) == (255, 255, 255)


def test_working_copy_refuses_what_is_not_a_picture():
    with pytest.raises(ValueError, match="Couldn't read"):
        images.working_copy(b"not a picture")


def test_add_needs_a_folder(app, unit, monkeypatch):
    monkeypatch.setitem(app.config, "PICTURES_DIR", None)
    with pytest.raises(store.NotConfigured):
        store.add(unit, _jpeg())
    assert UnitPicture.query.count() == 0


def test_add_keeps_the_original_and_a_working_copy(folder, unit, db):
    data = _jpeg((4000, 3000))
    picture = store.add(unit, data, "IMG_0001.jpg")
    db.session.commit()
    assert picture.position == 1
    assert picture.upload_name == "IMG_0001.jpg"
    assert store.original_path(picture).read_bytes() == data
    assert _size(store.path(picture)) == (2048, 1536)
    assert store.path(picture).parent == folder / f"unit-{unit.id}"


def test_add_names_files_by_token_not_by_what_was_uploaded(folder, unit):
    picture = store.add(unit, _jpeg(), "../../etc/passwd.jpg")
    assert "passwd" not in picture.file + picture.original_file
    assert store.path(picture).parent == folder / f"unit-{unit.id}"


def test_add_refuses_a_non_picture_and_writes_nothing(folder, unit):
    with pytest.raises(ValueError):
        store.add(unit, b"nope")
    assert not folder.exists() or not list(folder.rglob("*.*"))
    assert UnitPicture.query.count() == 0


def test_add_numbers_pictures_in_order(folder, unit, db):
    first = store.add(unit, _jpeg())
    second = store.add(unit, _png(mode="RGB"))
    db.session.commit()
    assert [p.id for p in store.for_unit(unit)] == [first.id, second.id]
    assert second.position == 2
    assert second.original_file.endswith(".png")


def test_make_first_moves_a_picture_to_the_front(folder, unit, db):
    a, b, c = (store.add(unit, _jpeg()) for _ in range(3))
    store.make_first(c)
    db.session.commit()
    assert [p.id for p in store.for_unit(unit)] == [c.id, a.id, b.id]
    assert [p.position for p in store.for_unit(unit)] == [1, 2, 3]


def test_remove_deletes_the_files_and_closes_the_gap(folder, unit, db):
    a, b = store.add(unit, _jpeg()), store.add(unit, _jpeg())
    gone = [store.path(a), store.original_path(a)]
    store.remove(a)
    db.session.commit()
    assert not any(p.exists() for p in gone)
    assert store.path(b).exists()
    assert [(p.id, p.position) for p in store.for_unit(unit)] == [(b.id, 1)]


def test_rotate_turns_both_files_into_new_ones(folder, unit, db):
    picture = store.add(unit, _jpeg((400, 300)))
    store.set_ebay_url(picture, "https://i.ebayimg.com/a.jpg")
    old = [store.path(picture), store.original_path(picture)]
    store.rotate(picture, "right")
    db.session.commit()
    assert _size(store.path(picture)) == (300, 400)
    assert _size(store.original_path(picture)) == (300, 400)
    assert not any(p.exists() for p in old)
    assert picture.ebay_url is None and picture.ebay_uploaded_at is None


def test_rotate_left_and_right_turn_opposite_ways(folder, unit):
    data = io.BytesIO()
    image = Image.new("RGB", (40, 20), "white")
    image.putpixel((0, 0), (255, 0, 0))
    image.save(data, "PNG")
    picture = store.add(unit, data.getvalue())
    store.rotate(picture, "right")
    with Image.open(store.original_path(picture)) as turned:
        # A clockwise turn takes the top-left corner to the top-right.
        assert turned.getpixel((19, 0)) == (255, 0, 0)


def test_rotate_leaves_the_old_files_when_one_cannot_be_read(folder, unit):
    picture = store.add(unit, _jpeg())
    store.path(picture).write_bytes(b"damaged")
    with pytest.raises(ValueError):
        store.rotate(picture, "left")
    assert store.original_path(picture).exists()


def test_set_ebay_url_records_the_upload(folder, unit):
    picture = store.add(unit, _jpeg())
    store.set_ebay_url(picture, "https://i.ebayimg.com/a.jpg")
    assert picture.ebay_url == "https://i.ebayimg.com/a.jpg"
    assert picture.ebay_uploaded_at is not None


def test_pictures_of_other_units_are_kept_apart(folder, unit, db):
    other = Unit(name="Another")
    db.session.add(other)
    db.session.commit()
    mine = store.add(unit, _jpeg())
    theirs = store.add(other, _jpeg())
    assert [p.id for p in store.for_unit(unit)] == [mine.id]
    assert theirs.position == 1
    assert store.path(mine).parent != store.path(theirs).parent
