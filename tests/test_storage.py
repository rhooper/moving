"""Photo storage: writing, thumbnailing, and not storing the same shot twice."""

import io

import pytest
from PIL import Image

from movingbox import db, storage, store


def a_jpeg(size=(1600, 1200), colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def conn_with_box(config):
    conn = db.connect(config.db_path)
    box = store.create_box(conn, content_summary="kitchen things")
    yield conn, box["code"]
    conn.close()


def test_saving_a_photo_records_it_against_the_box(config, conn_with_box):
    conn, code = conn_with_box

    photo = storage.save_photo(conn, config, code, a_jpeg(), filename="box.jpg")

    assert photo["box_id"]
    assert (config.photo_dir / photo["filename"]).is_file()


def test_a_thumbnail_is_written_alongside(config, conn_with_box):
    conn, code = conn_with_box

    photo = storage.save_photo(conn, config, code, a_jpeg(), filename="box.jpg")

    thumb = config.photo_dir / photo["thumb_filename"]
    assert thumb.is_file()
    with Image.open(thumb) as image:
        assert max(image.size) <= storage.THUMB_MAX


def test_a_large_photo_is_downscaled_for_storage(config, conn_with_box):
    # Phone cameras produce 4000px images. Storing them whole fills the disk
    # for no benefit -- these are inventory snapshots, not photographs.
    conn, code = conn_with_box

    photo = storage.save_photo(conn, config, code, a_jpeg((4032, 3024)), filename="big.jpg")

    with Image.open(config.photo_dir / photo["filename"]) as image:
        assert max(image.size) <= storage.FULL_MAX
    assert photo["width"] <= storage.FULL_MAX


def test_the_same_photo_uploaded_twice_is_stored_once(config, conn_with_box):
    # The phone retries failed uploads, so the same bytes will arrive again.
    conn, code = conn_with_box
    data = a_jpeg()

    first = storage.save_photo(conn, config, code, data, filename="a.jpg")
    second = storage.save_photo(conn, config, code, data, filename="a.jpg")

    assert first["id"] == second["id"]
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 1


def test_different_photos_are_both_kept(config, conn_with_box):
    conn, code = conn_with_box

    storage.save_photo(conn, config, code, a_jpeg(colour=(10, 10, 10)), filename="a.jpg")
    storage.save_photo(conn, config, code, a_jpeg(colour=(200, 30, 30)), filename="b.jpg")

    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 2


def test_the_first_photo_becomes_the_primary(config, conn_with_box):
    conn, code = conn_with_box

    first = storage.save_photo(conn, config, code, a_jpeg(colour=(1, 2, 3)), filename="a.jpg")
    second = storage.save_photo(conn, config, code, a_jpeg(colour=(9, 9, 9)), filename="b.jpg")

    assert first["is_primary"] == 1
    assert second["is_primary"] == 0


def test_exif_orientation_is_applied_not_stored(config, conn_with_box):
    # An unrotated phone photo displays sideways in an <img>. Bake the rotation
    # in rather than hoping every viewer honours the EXIF tag.
    conn, code = conn_with_box
    buffer = io.BytesIO()
    image = Image.new("RGB", (1000, 500), (50, 50, 50))
    exif = image.getexif()
    exif[274] = 6  # rotate 90 CW
    image.save(buffer, format="JPEG", exif=exif)

    photo = storage.save_photo(conn, config, code, buffer.getvalue(), filename="rot.jpg")

    with Image.open(config.photo_dir / photo["filename"]) as saved:
        assert saved.height > saved.width  # portrait after rotation
        assert not saved.getexif().get(274)


def test_metadata_is_stripped(config, conn_with_box):
    # Photos taken inside a house carry GPS. The database is shared on a
    # tailnet and exported; the location of your home should not ride along.
    conn, code = conn_with_box
    buffer = io.BytesIO()
    image = Image.new("RGB", (800, 600), (70, 70, 70))
    exif = image.getexif()
    exif[271] = "MadeUpCamera"
    image.save(buffer, format="JPEG", exif=exif)

    photo = storage.save_photo(conn, config, code, buffer.getvalue(), filename="gps.jpg")

    with Image.open(config.photo_dir / photo["filename"]) as saved:
        assert dict(saved.getexif()) == {}


def test_saving_makes_the_box_findable_by_its_caption(config, conn_with_box):
    from movingbox import search

    conn, code = conn_with_box
    box_id = store.get_box(conn, code)["id"]

    storage.save_photo(conn, config, code, a_jpeg(), filename="a.jpg", caption="camping stove")

    assert search.search(conn, "camping stove") == [box_id]


def test_deleting_a_photo_removes_its_files(config, conn_with_box):
    conn, code = conn_with_box
    photo = storage.save_photo(conn, config, code, a_jpeg(), filename="a.jpg")
    full = config.photo_dir / photo["filename"]

    assert storage.delete_photo(conn, config, photo["id"]) is True

    assert not full.exists()
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 0


def test_rubbish_that_is_not_an_image_is_rejected(config, conn_with_box):
    conn, code = conn_with_box

    with pytest.raises(storage.NotAnImage):
        storage.save_photo(conn, config, code, b"this is not a jpeg", filename="a.jpg")


def test_nothing_is_written_to_disk_when_the_upload_is_rejected(config, conn_with_box):
    conn, code = conn_with_box

    with pytest.raises(storage.NotAnImage):
        storage.save_photo(conn, config, code, b"nope", filename="a.jpg")

    assert list(config.photo_dir.glob("*")) == [] if config.photo_dir.exists() else True
