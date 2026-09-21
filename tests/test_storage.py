"""Photo storage: writing, thumbnailing, and not storing the same shot twice."""

import io
import sqlite3

import pytest
from PIL import Image

from movingbox import db, renditions, storage, store


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


class TestCover:
    """Exactly one photo per box is its cover, and it survives a deletion.

    The list and the search results render a box by its cover, so "no cover"
    and "two covers" are both wrong in ways a person notices: a row that shows
    nothing, or a row whose picture changes depending on which query ran.
    """

    def three(self, config, conn, code):
        return [
            storage.save_photo(conn, config, code, a_jpeg(colour=shade), filename=f"{n}.jpg")
            for n, shade in enumerate([(1, 2, 3), (9, 9, 9), (200, 30, 30)])
        ]

    def covers(self, conn):
        rows = conn.execute("SELECT id FROM photos WHERE is_primary = 1 ORDER BY id")
        return [r["id"] for r in rows]

    def test_any_photo_can_be_made_the_cover(self, config, conn_with_box):
        conn, code = conn_with_box
        first, second, _ = self.three(config, conn, code)

        storage.set_cover(conn, second["id"])

        assert self.covers(conn) == [second["id"]]
        assert storage.get_photo(conn, first["id"])["is_primary"] == 0

    def test_the_chosen_cover_is_returned(self, config, conn_with_box):
        conn, code = conn_with_box
        _, second, _ = self.three(config, conn, code)

        assert storage.set_cover(conn, second["id"])["is_primary"] == 1

    def test_choosing_the_cover_twice_is_harmless(self, config, conn_with_box):
        conn, code = conn_with_box
        _, second, _ = self.three(config, conn, code)

        storage.set_cover(conn, second["id"])
        storage.set_cover(conn, second["id"])

        assert self.covers(conn) == [second["id"]]

    def test_an_unknown_photo_cannot_be_made_the_cover(self, config, conn_with_box):
        conn, _ = conn_with_box

        with pytest.raises(LookupError):
            storage.set_cover(conn, 9999)

    def test_the_cover_of_one_box_does_not_disturb_another(self, config, conn_with_box):
        conn, code = conn_with_box
        other = store.create_box(conn, content_summary="garage")["code"]
        mine = storage.save_photo(conn, config, code, a_jpeg(colour=(4, 4, 4)), filename="a.jpg")
        theirs = storage.save_photo(conn, config, other, a_jpeg(colour=(4, 4, 4)), filename="b.jpg")

        storage.set_cover(conn, mine["id"])

        assert sorted(self.covers(conn)) == sorted([mine["id"], theirs["id"]])

    def test_deleting_the_cover_promotes_another_photo(self, config, conn_with_box):
        # A box that has photos must never be left without one to show.
        conn, code = conn_with_box
        first, second, _ = self.three(config, conn, code)

        storage.delete_photo(conn, config, first["id"])

        assert self.covers(conn) == [second["id"]]

    def test_deleting_a_photo_that_is_not_the_cover_leaves_the_cover_alone(
        self, config, conn_with_box
    ):
        # The promote-on-delete rule predates being able to choose a cover, so
        # it promoted the lowest-numbered survivor unconditionally. Once the
        # cover is somebody's choice that is both a silent change of picture
        # and a second cover on the same box.
        conn, code = conn_with_box
        first, second, third = self.three(config, conn, code)
        storage.set_cover(conn, second["id"])

        storage.delete_photo(conn, config, third["id"])

        assert self.covers(conn) == [second["id"]]

    def test_deleting_the_last_photo_leaves_no_cover(self, config, conn_with_box):
        conn, code = conn_with_box
        only = storage.save_photo(conn, config, code, a_jpeg(), filename="a.jpg")

        storage.delete_photo(conn, config, only["id"])

        assert self.covers(conn) == []

    def test_the_database_refuses_a_second_cover_on_one_box(self, config, conn_with_box):
        # The invariant is the schema's, not just the code path's: anything
        # that writes is_primary directly is caught rather than trusted.
        conn, code = conn_with_box
        _, second, _ = self.three(config, conn, code)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE photos SET is_primary = 1 WHERE id = ?", (second["id"],))

    def test_the_cover_is_listed_first(self, config, conn_with_box):
        conn, code = conn_with_box
        _, second, _ = self.three(config, conn, code)

        storage.set_cover(conn, second["id"])

        assert storage.list_photos(conn, code)[0]["id"] == second["id"]


class TestTheStripImage:
    """The sharpened, high-DPI image the photo strip draws. See renditions.py."""

    def test_an_upload_makes_one(self, config, conn_with_box):
        conn, code = conn_with_box

        photo = storage.save_photo(conn, config, code, a_jpeg(size=(3024, 4032)))

        strip = config.photo_dir / renditions.strip_name(photo["filename"])
        with Image.open(strip) as image:
            assert image.size == (600, 800)

    def test_it_is_byte_identical_to_the_one_the_command_makes_later(self, config, conn_with_box):
        # The cache invariant, across the paths that make one. An upload has
        # the original in hand and could render from that -- but the command
        # and a request only ever have the stored full image, so all three
        # render from it. Otherwise one URL would have two sets of bytes.
        conn, code = conn_with_box
        photo = storage.save_photo(conn, config, code, a_jpeg(size=(3024, 4032)))
        strip = config.photo_dir / renditions.strip_name(photo["filename"])
        made_at_upload = strip.read_bytes()

        strip.unlink()
        renditions.write_strip(config.photo_dir, photo["filename"])

        assert strip.read_bytes() == made_at_upload

    def test_the_list_thumbnail_is_left_exactly_as_it_was(self, config, conn_with_box):
        # Measured as ample already -- 2.4x the pixels a 3x phone's list row
        # needs -- and sharpening it made no difference anyone could see. Its
        # bytes must not move either: its URL is cached for a year.
        conn, code = conn_with_box
        data = a_jpeg(size=(3024, 4032))

        photo = storage.save_photo(conn, config, code, data)

        expected = Image.open(io.BytesIO(data)).convert("RGB")
        expected.thumbnail((400, 400), Image.LANCZOS)
        buffer = io.BytesIO()
        expected.save(buffer, format="JPEG", quality=storage.JPEG_QUALITY, optimize=True)
        assert (config.photo_dir / photo["thumb_filename"]).read_bytes() == buffer.getvalue()

    def test_deleting_a_photo_takes_its_strips_with_it(self, config, conn_with_box):
        conn, code = conn_with_box
        photo = storage.save_photo(conn, config, code, a_jpeg())
        old_recipe = config.photo_dir / renditions.strip_name(photo["filename"], "0123456789")
        old_recipe.write_bytes(b"a strip from an older recipe")

        storage.delete_photo(conn, config, photo["id"])

        assert renditions.strip_files(config.photo_dir, photo["filename"]) == []

    def test_purging_a_record_takes_its_strips_with_it(self, config, conn_with_box):
        conn, code = conn_with_box
        photo = storage.save_photo(conn, config, code, a_jpeg())
        store.delete_box(conn, config, code)

        store.purge_box(conn, config, code)

        assert renditions.strip_files(config.photo_dir, photo["filename"]) == []

    def test_a_photo_offers_the_page_both_sizes(self, config, conn_with_box):
        conn, code = conn_with_box
        storage.save_photo(conn, config, code, a_jpeg(size=(3024, 4032)))

        (photo,) = storage.list_photos(conn, code)

        assert f"/photos/{photo['id']}/strip?v={renditions.VERSION} 600w" in photo["srcset"]
        assert f"/photos/{photo['id']}/thumb 300w" in photo["srcset"]
