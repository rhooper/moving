"""The service worker's cache version must roll with every deploy.

The shell cache was pinned by a hand-bumped literal (`VERSION = "v3"`), and
nobody bumped it -- so phones kept serving a stale app.js against a newer API
until buttons errored. Serving /sw.js with the deployed revision substituted
makes the cache roll automatically: the browser re-checks sw.js bytes, sees a
new VERSION, reinstalls the shell, and skipWaiting/clients.claim take it live.
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
