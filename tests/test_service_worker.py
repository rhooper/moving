"""The service worker's cache version must roll with every deploy.

Serving /sw.js with the deployed revision substituted makes the cache roll by
itself: the browser re-checks sw.js bytes, sees a new VERSION, reinstalls the
shell, and skipWaiting/clients.claim take it live. A hand-bumped literal is one
nobody bumps.
"""

import re

from fastapi.testclient import TestClient

from movingbox.api.app import create_app


def sw_body(config) -> str:
    with TestClient(create_app(config)) as client:
        response = client.get("/sw.js")
        assert response.status_code == 200
        return response.text


def version_in(body: str) -> str:
    match = re.search(r'const VERSION = "([^"]+)"', body)
    assert match, "sw.js no longer declares const VERSION"
    return match.group(1)


class TestServedServiceWorker:
    def test_the_cache_version_is_the_deployed_revision(self, config, monkeypatch):
        from movingbox.api import app as app_module

        monkeypatch.setattr(app_module, "deployed_revision", lambda: "abc1234")

        assert version_in(sw_body(config)) == "abc1234"

    def test_an_unknown_revision_keeps_the_file_as_written(self, config, monkeypatch):
        # Dev checkouts have no var/deployed-revision; substituting "unknown"
        # would pin every dev client to one cache name forever.
        from movingbox.api import app as app_module

        monkeypatch.setattr(app_module, "deployed_revision", lambda: "unknown")

        assert version_in(sw_body(config)) == version_in(
            (app_module.WEB_ROOT / "sw.js").read_text()
        )

    def test_it_is_served_as_javascript_and_never_cached(self, config, monkeypatch):
        from movingbox.api import app as app_module

        monkeypatch.setattr(app_module, "deployed_revision", lambda: "abc1234")

        with TestClient(create_app(config)) as client:
            response = client.get("/sw.js")
        assert "javascript" in response.headers["content-type"]
        # An HTTP-cached sw.js would defeat the whole point.
        assert response.headers["cache-control"] == "no-cache"

    def test_the_rest_of_the_worker_is_untouched(self, config, monkeypatch):
        from movingbox.api import app as app_module

        monkeypatch.setattr(app_module, "deployed_revision", lambda: "abc1234")
        body = sw_body(config)

        assert "skipWaiting" in body
        assert "clients.claim" in body


class TestTheRevisionCheckIsNeverAnsweredFromACache:
    """An open page learns that a deploy happened by asking /health.

    The worker serves everything outside /api/ cache-first, so without an
    exception a page's first /health answer would be the only one it ever got,
    and auto-reload would silently never fire.
    """

    def test_the_worker_passes_health_straight_to_the_network(self):
        from movingbox.api import app as app_module

        source = (app_module.WEB_ROOT / "sw.js").read_text()
        handler = source[source.index('addEventListener("fetch"') :]
        bypass = re.search(r'url\.pathname === "/health"\)\s*return;', handler)

        assert bypass, "sw.js does not let /health through uncached"
        assert bypass.start() < handler.index("respondWith"), "the bypass comes after the cache"

    def test_and_the_answer_itself_says_not_to_keep_it(self, config):
        with TestClient(create_app(config)) as client:
            response = client.get("/health")

        assert response.headers["cache-control"] == "no-store"
