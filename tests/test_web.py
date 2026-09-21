"""Serving the PWA, and behaving correctly behind Tailscale's HTTPS proxy."""

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


def test_the_root_serves_the_app_shell(client):
    # Without this, a scanned label 307s to /#/b/CODE and lands on a 404.
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_web_manifest_is_served_so_the_app_can_be_installed(client):
    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200


def test_the_service_worker_is_served_from_the_root_scope(client):
    # A service worker may only control paths at or below its own URL, so it
    # has to be served from / rather than /static/.
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


class TestScannedLabels:
    def test_a_scanned_code_redirects_to_the_app(self, client):
        code = client.post("/api/boxes", json={}).json()["code"]

        response = client.get(f"/b/{code}", follow_redirects=False)

        assert response.status_code == 307

    def test_the_redirect_is_relative_so_https_survives_the_proxy(self, client):
        # Tailscale terminates TLS and proxies plain HTTP to us, so an absolute
        # redirect built from the request would say http:// and downgrade the
        # phone out of HTTPS. A relative Location keeps the browser's scheme.
        code = client.post("/api/boxes", json={}).json()["code"]

        location = client.get(f"/b/{code}", follow_redirects=False).headers["location"]

        assert location.startswith("/"), f"{location!r} must be relative, not absolute"
        assert "http://" not in location

    def test_the_redirect_survives_a_forwarded_https_request(self, client):
        code = client.post("/api/boxes", json={}).json()["code"]

        response = client.get(
            f"/b/{code}",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "100.64.0.1"},
            follow_redirects=False,
        )

        assert response.status_code == 307
        assert "http://" not in response.headers["location"]

    def test_following_a_scanned_code_ends_at_the_app_shell(self, client):
        code = client.post("/api/boxes", json={}).json()["code"]

        response = client.get(f"/b/{code}")  # follows the redirect

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_an_unknown_code_is_still_404(self, client):
        assert client.get("/b/B-9999", follow_redirects=False).status_code == 404


def test_the_api_is_still_reachable_under_the_static_mount(client):
    assert client.get("/api/boxes").status_code == 200
    assert client.get("/health").status_code == 200
