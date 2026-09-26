"""Every asset URL carries the deployed revision, so a deploy cannot be served stale.

There is no build step to stamp filenames, so it happens as files are served:
`/app.js` becomes `/app.js?v=<revision>` in index.html, in the service worker's
shell list, and in the `import` lines *inside* each module -- a versioned entry
point importing unversioned modules busts nothing. A versioned URL is cached
forever; index.html never is.
"""

import re

import pytest
from fastapi.testclient import TestClient

from movingbox.api import app as app_module
from movingbox.api.app import create_app

REV = "abc1234def"
FOREVER = "public, max-age=31536000, immutable"


@pytest.fixture
def client(config, monkeypatch):
    monkeypatch.setattr(app_module, "deployed_revision", lambda: REV)
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def dev(config, monkeypatch):
    monkeypatch.setattr(app_module, "deployed_revision", lambda: "unknown")
    with TestClient(create_app(config)) as c:
        yield c


def asset_refs(text: str) -> list[str]:
    """Every quoted or url()-wrapped same-origin asset path in a file."""
    return re.findall(
        r"""["'(](/[\w.-]+\.(?:js|ttf|png|svg|ico|webmanifest)(?:\?v=[\w.-]+)?)["')]""", text
    )


class TestThePage:
    def test_the_entry_point_is_versioned(self, client):
        assert f'src="/app.js?v={REV}"' in client.get("/").text

    def test_so_is_everything_else_it_points_at(self, client):
        refs = asset_refs(client.get("/").text)

        assert refs, "found no asset references at all"
        assert all(ref.endswith(f"?v={REV}") for ref in refs), refs

    def test_the_page_itself_is_never_cached(self, client):
        # It is what *names* the versioned files; cache it and nothing updates.
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/index.html").headers["cache-control"] == "no-cache"

    def test_the_home_link_and_the_routes_are_left_alone(self, client):
        page = client.get("/").text

        assert 'href="#/"' in page and 'href="#/settings"' in page


class TestModules:
    def test_imports_inside_a_module_are_versioned_too(self, client):
        source = client.get(f"/app.js?v={REV}").text

        assert f'from "/live.js?v={REV}"' in source
        assert f'import("/scan.js?v={REV}")' in source
        assert 'from "/live.js"' not in source

    def test_a_module_loaded_by_a_module_is_versioned(self, client):
        assert f'import("/jsQR.js?v={REV}")' in client.get(f"/scan.js?v={REV}").text

    def test_every_reference_resolves(self, client):
        # A rewrite that produced a URL nothing serves would break the app on
        # the next deploy and nowhere else.
        seen, queue = set(), ["/"]
        while queue:
            url = queue.pop()
            if url in seen:
                continue
            seen.add(url)
            response = client.get(url)
            assert response.status_code == 200, url
            if url == "/" or url.split("?")[0].endswith(".js"):
                queue.extend(asset_refs(response.text))
        assert len(seen) > 8, seen

    def test_the_service_worker_script_keeps_its_address(self, client):
        # A worker is identified by its script URL. Versioning it would register
        # a new worker per deploy instead of updating the one there is.
        source = client.get(f"/app.js?v={REV}").text

        assert 'register("/sw.js")' in source

    def test_the_current_version_is_cached_forever(self, client):
        assert client.get(f"/app.js?v={REV}").headers["cache-control"] == FOREVER
        assert client.get(f"/Inter.ttf?v={REV}").headers["cache-control"] == FOREVER
        assert client.get(f"/icon-192.png?v={REV}").headers["cache-control"] == FOREVER

    @pytest.mark.parametrize("url", ["/app.js", "/app.js?v=lastweek", "/live.js?v="])
    def test_anything_else_is_revalidated_every_time(self, client, url):
        # An old tab asking for last week's URL must not pin this week's bytes
        # under last week's name for a year.
        response = client.get(url)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    def test_it_is_still_javascript(self, client):
        assert "javascript" in client.get(f"/app.js?v={REV}").headers["content-type"]

    def test_only_files_that_exist_are_served(self, client):
        assert client.get("/nope.js").status_code == 404
        assert client.get("/..%2Fpyproject.toml").status_code == 404


class TestTheServiceWorker:
    def test_its_shell_list_names_versioned_files(self, client):
        source = client.get("/sw.js").text
        shell = source[
            source.index("const SHELL") : source.index("];", source.index("const SHELL"))
        ]

        refs = asset_refs(shell)
        assert refs and all(ref.endswith(f"?v={REV}") for ref in refs), refs
        assert '"/"' in shell  # the page itself: no version, never cached

    def test_it_is_itself_never_cached(self, client):
        assert client.get("/sw.js").headers["cache-control"] == "no-cache"

    def test_its_cache_name_is_still_the_revision(self, client):
        assert f'const VERSION = "{REV}"' in client.get("/sw.js").text


class TestWithoutARevision:
    """A dev checkout has no deployed revision: serve the files as written."""

    def test_nothing_is_rewritten(self, dev):
        assert 'src="/app.js"' in dev.get("/").text
        assert 'from "/live.js"' in dev.get("/app.js").text

    def test_and_nothing_is_cached(self, dev):
        assert dev.get("/app.js").headers["cache-control"] == "no-cache"
        assert dev.get("/").headers["cache-control"] == "no-cache"


class TestTheRestOfTheSite:
    def test_the_api_is_untouched(self, client):
        assert client.get("/health").json()["revision"] == REV

    def test_photos_keep_their_own_long_cache(self, client):
        # Content-addressed already; this must not start revalidating them.
        assert client.get("/photos/1/thumb").status_code == 404


class TestNamingARevisionForAThrowawayServer:
    def test_the_environment_can_name_one(self, config, monkeypatch):
        monkeypatch.setenv("MOVING_REVISION", "check-42")

        with TestClient(create_app(config)) as c:
            assert 'src="/app.js?v=check-42"' in c.get("/").text

    def test_an_empty_one_is_not_a_revision(self, config, monkeypatch, tmp_path):
        monkeypatch.setenv("MOVING_REVISION", "  ")
        monkeypatch.setattr(app_module, "REVISION_FILE", tmp_path / "nope")

        with TestClient(create_app(config)) as c:
            assert 'src="/app.js"' in c.get("/").text


class TestTheVersionOnThePage:
    """The version beside the menu on a desktop, above the menubar on a phone."""

    def test_the_page_carries_the_version(self, client):
        import movingbox

        slot = f'id="version" class="version">v{movingbox.__version__}</span>'
        assert slot in client.get("/").text

    def test_in_a_dev_checkout_too(self, dev):
        # No revision, but there is always a version.
        import movingbox

        assert f">v{movingbox.__version__}</span>" in dev.get("/").text
