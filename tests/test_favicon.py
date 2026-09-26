"""The favicon is the home mark, generated from its one source.

Asked for as "create a favicon from the logo". The tab showed the PWA icon's
white bar, and /favicon.ico was a 404.
"""

from __future__ import annotations

import importlib.util
import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox.api import app as app_module
from movingbox.api.app import create_app
from movingbox.config import ROOT

REV = "abc1234def"
WEB = ROOT / "web"


def make_icons():
    spec = importlib.util.spec_from_file_location(
        "make_icons", ROOT / "scripts" / "claude" / "make_icons.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(config, monkeypatch):
    monkeypatch.setattr(app_module, "deployed_revision", lambda: REV)
    with TestClient(create_app(config)) as c:
        yield c


class TestTheFiles:
    def test_they_are_what_the_generator_makes_from_the_home_mark(self):
        # Change docs/design/home-mark.svg, and this fails until
        # `uv run python scripts/claude/make_icons.py` is run.
        icons = make_icons()

        assert (WEB / "favicon.svg").read_text() == icons.favicon_svg()
        with Image.open(WEB / "favicon.ico") as ico:
            for size in icons.FAVICON_SIZES:
                ico.size = (size, size)
                assert ico.convert("RGB").tobytes() == icons.favicon(size).tobytes(), size

    def test_the_svg_draws_the_home_mark_s_own_path(self):
        mark = re.search(r'\sd="([^"]+)"', (ROOT / "docs/design/home-mark.svg").read_text())[1]

        assert f'd="{mark}"' in (WEB / "favicon.svg").read_text()

    def test_the_ico_holds_the_mark_s_grid_sizes_only(self):
        # 16-unit grid: 16, 32, 48. Never a size between, where the ring blurs.
        with Image.open(WEB / "favicon.ico") as ico:
            assert ico.info["sizes"] == {(16, 16), (32, 32), (48, 48)}

    def test_an_edge_on_the_grid_is_pure_black_or_white(self):
        # Only the roof is diagonal. A grey anywhere else is a fill that
        # spilled past its edge, which is what ImageDraw.polygon does.
        image = make_icons().favicon(16).convert("L")
        roof_rows = range(3, 8)  # the house's roof spans y 4..8 in mark units
        for y in range(16):
            if y in roof_rows:
                continue
            row = [image.getpixel((x, y)) for x in range(16)]
            assert set(row) <= {0, 255}, (y, row)


class TestServingThem:
    def test_the_page_names_both_versioned(self, client):
        page = client.get("/").text

        assert f'href="/favicon.ico?v={REV}"' in page
        assert f'href="/favicon.svg?v={REV}"' in page

    @pytest.mark.parametrize(
        "url, kind", [("/favicon.svg", "image/svg+xml"), ("/favicon.ico", "icon")]
    )
    def test_each_is_served_as_an_image(self, client, url, kind):
        response = client.get(f"{url}?v={REV}")

        assert response.status_code == 200
        assert kind in response.headers["content-type"]

    def test_the_bare_ico_a_browser_asks_for_on_its_own_is_there(self, client):
        response = client.get("/favicon.ico")

        assert response.status_code == 200
        assert Image.open(io.BytesIO(response.content)).format == "ICO"

    def test_the_service_worker_keeps_them_for_offline(self, client):
        worker = client.get("/sw.js").text

        assert f'"/favicon.svg?v={REV}"' in worker
        assert f'"/favicon.ico?v={REV}"' in worker
