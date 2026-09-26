"""The strip rendition: a sharpened, high-DPI image for the photo strip.

A 300x400 list thumbnail is too soft for a 186 css px strip figure on a 3x
phone; the list rows are small enough for it, so they keep it.

**A strip URL names one set of bytes, forever.** The service worker is
cache-first and the photo route is cached for a year, so a URL whose bytes
change is never fetched again. The version in the URL is derived from the
recipe, and every path that makes a strip -- upload, the command, a request --
makes the same bytes.
"""

import hashlib

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageStat

from movingbox import renditions


def a_full_photo(photo_dir, name="B-0001-abc123def456.jpg", size=(1536, 2048)):
    """A stored full image with hard edges in it, as storage would leave one."""
    photo_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (200, 170, 130))
    draw = ImageDraw.Draw(image)
    # Fine black-on-light bars: the kind of detail -- printed text, a ruler's
    # ticks -- a soft thumbnail smears and a sharp one keeps.
    for x in range(100, size[0] - 100, 24):
        draw.rectangle([x, 300, x + 8, size[1] - 300], fill=(20, 20, 20))
    image.save(photo_dir / name, format="JPEG", quality=95)
    return name


def sharpness(image):
    """Variance of the edge map: higher is crisper. A standard focus measure."""
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestTheSize:
    def test_the_long_edge_is_sized_for_the_strip_at_3x(self):
        # A portrait strip figure is 186 css px wide on the phone, so 558
        # device px at 3x. 800 on the long edge is 600 across a portrait.
        assert renditions.STRIP_MAX == 800
        assert renditions.fitted(1536, 2048, renditions.STRIP_MAX) == (600, 800)

    def test_it_is_never_enlarged(self):
        assert renditions.fitted(500, 300, renditions.STRIP_MAX) == (500, 300)

    @pytest.mark.parametrize(
        "size",
        [(1536, 2048), (2048, 1536), (2048, 2048), (1537, 2048), (2048, 999), (1234, 2047), (7, 5)],
    )
    @pytest.mark.parametrize("longest", [400, 800])
    def test_fitted_agrees_with_pillow_to_the_pixel(self, size, longest):
        # srcset's width descriptors come from this; they must describe the
        # image that is actually served, not an approximation of it.
        image = Image.new("RGB", size)
        image.thumbnail((longest, longest), Image.LANCZOS)

        assert renditions.fitted(*size, longest) == image.size


class TestTheImage:
    def test_it_is_sharpened(self, tmp_path):
        name = a_full_photo(tmp_path)
        with Image.open(tmp_path / name) as full:
            plain = full.copy()
            plain.thumbnail((renditions.STRIP_MAX,) * 2, Image.LANCZOS)
            strip = renditions.render_strip(full)

        assert strip.size == plain.size
        # Without the mask this ratio is exactly 1.0; with it, about 1.1 on
        # these already hard-edged bars.
        assert sharpness(strip) > sharpness(plain) * 1.05

    def test_it_is_not_over_sharpened(self, tmp_path):
        # Halos and amplified JPEG noise are worse than softness. The recipe
        # was picked by eye; this only stops it drifting far past that.
        name = a_full_photo(tmp_path)
        with Image.open(tmp_path / name) as full:
            plain = full.copy()
            plain.thumbnail((renditions.STRIP_MAX,) * 2, Image.LANCZOS)
            strip = renditions.render_strip(full)

        assert sharpness(strip) < sharpness(plain) * 2.5

    def test_the_same_full_image_always_makes_the_same_bytes(self, tmp_path):
        # The cache invariant: however a strip comes to be made, a URL must
        # only ever be answered with one set of bytes.
        name = a_full_photo(tmp_path)

        renditions.write_strip(tmp_path, name)
        first = (tmp_path / renditions.strip_name(name)).read_bytes()
        (tmp_path / renditions.strip_name(name)).unlink()
        renditions.write_strip(tmp_path, name)

        assert (tmp_path / renditions.strip_name(name)).read_bytes() == first


class TestTheVersion:
    def test_it_is_derived_from_the_recipe_not_typed(self):
        # A hand-bumped version is one nobody bumps.
        assert renditions.VERSION == renditions.version()

    @pytest.mark.parametrize(
        "change",
        [
            {"strip_max": 1000},
            {"unsharp": (1.0, 120, 3)},
            {"quality": 90},
        ],
    )
    def test_any_change_to_the_recipe_changes_it(self, change):
        # Which is what makes a regenerated strip a new URL, and so fetched.
        assert renditions.version(**change) != renditions.VERSION

    def test_it_is_in_the_filename(self):
        assert renditions.VERSION in renditions.strip_name("B-0001-abc123def456.jpg")

    def test_the_filename_follows_the_version_in_force_not_the_one_at_import(self, monkeypatch):
        # A default argument is bound once, when the module loads. Named that
        # way, a changed recipe would still write its strips under the old
        # version's filename -- a file its URL could never find.
        monkeypatch.setattr(renditions, "VERSION", "abcdef0123")

        assert renditions.strip_name("B-0001-abc123def456.jpg").endswith("-strip-abcdef0123.jpg")

    def test_it_is_safe_in_a_url_and_a_filename(self):
        assert renditions.VERSION.isalnum() and renditions.VERSION.islower()


class TestWritingOne:
    def test_it_is_made_from_the_full_image_never_the_thumbnail(self, tmp_path):
        # Remaking a thumbnail from the old thumbnail compounds the blur. The
        # thumbnail here is garbage; a strip made from it would not decode.
        name = a_full_photo(tmp_path)
        (tmp_path / name.replace(".jpg", "-thumb.jpg")).write_bytes(b"not a jpeg")

        renditions.write_strip(tmp_path, name)

        with Image.open(tmp_path / renditions.strip_name(name)) as strip:
            assert strip.size == (600, 800)

    def test_the_full_image_is_never_touched(self, tmp_path):
        # It is the record, and uploads are deduplicated by its hash.
        name = a_full_photo(tmp_path)
        before = sha(tmp_path / name)

        renditions.write_strip(tmp_path, name)

        assert sha(tmp_path / name) == before

    def test_writing_it_again_does_nothing(self, tmp_path):
        name = a_full_photo(tmp_path)
        assert renditions.write_strip(tmp_path, name) is True
        stamp = (tmp_path / renditions.strip_name(name)).stat().st_mtime_ns

        assert renditions.write_strip(tmp_path, name) is False
        assert (tmp_path / renditions.strip_name(name)).stat().st_mtime_ns == stamp

    def test_it_is_written_whole_or_not_at_all(self, tmp_path):
        # The service may be serving this directory while the command writes
        # to it, so a file is written aside and renamed into place.
        name = a_full_photo(tmp_path)

        renditions.write_strip(tmp_path, name)

        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
        assert leftovers == []
        with Image.open(tmp_path / renditions.strip_name(name)) as strip:
            strip.load()

    def test_every_version_on_disk_is_found(self, tmp_path):
        # So deleting a photo leaves no strip of any recipe behind.
        name = a_full_photo(tmp_path)
        renditions.write_strip(tmp_path, name)
        stale = tmp_path / name.replace(".jpg", "-strip-0123456789.jpg")
        stale.write_bytes(b"an old recipe")

        found = sorted(p.name for p in renditions.strip_files(tmp_path, name))

        assert found == sorted([renditions.strip_name(name), stale.name])

    def test_another_photos_strips_are_not_mistaken_for_this_ones(self, tmp_path):
        a = a_full_photo(tmp_path, "B-0001-aaaaaaaaaaaa.jpg")
        b = a_full_photo(tmp_path, "B-0001-aaaaaaaaaaaab.jpg")
        renditions.write_strip(tmp_path, a)
        renditions.write_strip(tmp_path, b)

        assert [p.name for p in renditions.strip_files(tmp_path, a)] == [renditions.strip_name(a)]


class TestTheSrcset:
    def photo(self, **overrides):
        return {"id": 7, "width": 1536, "height": 2048, "sha256": "ab12" * 16, **overrides}

    def test_it_offers_the_thumbnail_and_the_strip_with_their_real_widths(self):
        srcset = renditions.srcset(self.photo())

        assert srcset == (
            "/photos/7/thumb?k=ab12ab12ab12 300w, "
            f"/photos/7/strip?v={renditions.VERSION}&k=ab12ab12ab12 600w"
        )

    def test_a_landscape_photo_is_described_by_its_own_widths(self):
        srcset = renditions.srcset(self.photo(width=2048, height=1536))

        assert "/photos/7/thumb?k=ab12ab12ab12 400w" in srcset
        assert f"/photos/7/strip?v={renditions.VERSION}&k=ab12ab12ab12 800w" in srcset

    def test_a_photo_too_small_for_a_larger_version_offers_only_one(self):
        # Two candidates of the same width is not a choice, and a duplicate
        # width descriptor is invalid srcset.
        srcset = renditions.srcset(self.photo(width=300, height=200))

        assert srcset == "/photos/7/thumb?k=ab12ab12ab12 300w"

    def test_every_url_names_the_bytes_not_just_the_id(self):
        # Ids were reused before migration 0011, and browsers keep a photo's
        # bytes for a year under its URL: the key is what tells them apart.
        a = renditions.srcset(self.photo(sha256="a" * 64))
        b = renditions.srcset(self.photo(sha256="b" * 64))

        assert a != b
        assert all("k=" in candidate for candidate in a.split(", "))

    def test_a_photo_with_no_recorded_size_offers_nothing(self):
        # Rather than a width descriptor that is a guess.
        assert renditions.srcset(self.photo(width=None, height=None)) == ""


class TestTheCommand:
    """`moving thumbnails`: give every existing photo its strip.

    It is run by hand against a running service, so it must be safe to run
    twice and safe to run underneath it.
    """

    @pytest.fixture
    def legacy(self, config):
        """Photos as they were before strips existed: full image and thumb only."""
        import io as _io

        from movingbox import db, storage, store

        conn = db.connect(config.db_path)
        box = store.create_box(conn)
        photos = []
        for colour in ((140, 110, 80), (60, 90, 120), (200, 200, 200)):
            buffer = _io.BytesIO()
            Image.new("RGB", (1536, 2048), colour).save(buffer, format="JPEG")
            photo = storage.save_photo(conn, config, box["code"], buffer.getvalue())
            for strip in renditions.strip_files(config.photo_dir, photo["filename"]):
                strip.unlink()
            photos.append(photo)
        conn.commit()
        yield conn, photos
        conn.close()

    def strips(self, config, photos):
        return [(config.photo_dir / renditions.strip_name(p["filename"])).is_file() for p in photos]

    def test_every_photo_without_one_gets_one(self, config, legacy):
        conn, photos = legacy

        report = renditions.backfill(conn, config.photo_dir)

        assert report["made"] == 3
        assert self.strips(config, photos) == [True, True, True]

    def test_running_it_again_does_nothing(self, config, legacy):
        conn, photos = legacy
        renditions.backfill(conn, config.photo_dir)

        report = renditions.backfill(conn, config.photo_dir)

        assert (report["made"], report["present"]) == (0, 3)

    def test_a_dry_run_says_what_it_would_do_and_does_none_of_it(self, config, legacy):
        conn, photos = legacy

        report = renditions.backfill(conn, config.photo_dir, dry_run=True)

        assert report["made"] == 3
        assert self.strips(config, photos) == [False, False, False]

    def test_the_full_image_and_the_list_thumbnail_are_never_touched(self, config, legacy):
        # The full image is the record, deduplicated by its hash; the list
        # thumbnail's URL is cached for a year, so its bytes must not move.
        conn, photos = legacy
        names = [p["filename"] for p in photos] + [p["thumb_filename"] for p in photos]
        before = {n: sha(config.photo_dir / n) for n in names}

        renditions.backfill(conn, config.photo_dir)

        assert {n: sha(config.photo_dir / n) for n in names} == before

    def test_it_never_writes_to_the_database(self, config, legacy):
        # So it cannot take a lock the running service is waiting on.
        conn, _ = legacy
        before = conn.total_changes

        renditions.backfill(conn, config.photo_dir, prune=True)

        assert conn.total_changes == before

    def test_a_photo_whose_full_image_is_gone_is_reported_not_fatal(self, config, legacy):
        conn, photos = legacy
        (config.photo_dir / photos[1]["filename"]).unlink()

        report = renditions.backfill(conn, config.photo_dir)

        assert report["missing"] == [photos[1]["filename"]]
        assert report["made"] == 2

    def test_older_recipes_are_left_alone_unless_asked(self, config, legacy):
        # A copy of the service still on the old code would be advertising
        # exactly those, so removing them is a deliberate step.
        conn, photos = legacy
        old = config.photo_dir / renditions.strip_name(photos[0]["filename"], "0123456789")
        old.write_bytes(b"an older recipe")

        renditions.backfill(conn, config.photo_dir)

        assert old.is_file()

    def test_pruning_removes_older_recipes_and_keeps_the_current_one(self, config, legacy):
        conn, photos = legacy
        old = config.photo_dir / renditions.strip_name(photos[0]["filename"], "0123456789")
        old.write_bytes(b"an older recipe")

        report = renditions.backfill(conn, config.photo_dir, prune=True)

        assert report["pruned"] == 1
        assert not old.is_file()
        assert self.strips(config, photos) == [True, True, True]

    def test_the_cli_runs_it(self, config, legacy, monkeypatch, capsys):
        from movingbox import cli

        _, photos = legacy
        monkeypatch.setenv("MOVING_DB_PATH", str(config.db_path))
        monkeypatch.setenv("MOVING_PHOTO_DIR", str(config.photo_dir))

        assert cli.main(["thumbnails", "--dry-run"]) == 0
        assert self.strips(config, photos) == [False, False, False]
        assert "would make 3" in capsys.readouterr().out

        assert cli.main(["thumbnails"]) == 0
        assert self.strips(config, photos) == [True, True, True]
        assert "made 3" in capsys.readouterr().out
