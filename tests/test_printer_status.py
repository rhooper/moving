"""Printer status, and what happens when a print actually fails."""

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app
from movingbox.labels import printer


class TestStatus:
    def test_the_fake_backend_says_it_prints_nothing(self, config):
        # It returns success, so the UI has to be told that no tape came out.
        state = printer.status(config)

        assert state["backend"] == "fake"
        assert state["prints"] is False
        assert state["ready"] is True
        assert "preview" in state["detail"].lower()

    def test_a_connected_printer_is_ready(self, config):
        state = printer.status(
            config.replace(printer_backend="brother_ql"), find_device=lambda: "usb-device"
        )

        assert state["ready"] is True
        assert state["prints"] is True

    def test_a_missing_printer_is_not_ready_and_says_why(self, config):
        state = printer.status(
            config.replace(printer_backend="brother_ql"), find_device=lambda: None
        )

        assert state["ready"] is False
        assert "usb" in state["detail"].lower()

    def test_a_usb_probe_that_blows_up_is_reported_not_raised(self, config):
        def explode():
            raise OSError("no backend available")

        state = printer.status(config.replace(printer_backend="brother_ql"), find_device=explode)

        assert state["ready"] is False
        assert "no backend available" in state["detail"]

    def test_cups_without_a_queue_is_not_ready(self, config):
        state = printer.status(config.replace(printer_backend="cups_raw"))

        assert state["ready"] is False
        assert "queue" in state["detail"].lower()


class TestPrintFailures:
    @pytest.fixture
    def client(self, config):
        with TestClient(create_app(config)) as c:
            yield c

    def test_a_hardware_failure_is_reported_not_a_bare_500(self, client, config, monkeypatch):
        code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

        class Broken:
            def print_label(self, image, *, code, copies=1):
                raise OSError("[Errno 19] No such device")

        monkeypatch.setattr(printer, "get_backend", lambda cfg: Broken())

        response = client.post("/api/labels/print", json={"codes": [code]})

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "No such device" in detail

    def test_a_failed_print_is_not_recorded_as_printed(self, client, monkeypatch):
        # A print count that rises when nothing came out is worse than useless.
        code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

        class Broken:
            def print_label(self, image, *, code, copies=1):
                raise OSError("printer is asleep")

        monkeypatch.setattr(printer, "get_backend", lambda cfg: Broken())
        client.post("/api/labels/print", json={"codes": [code]})

        assert client.get(f"/api/boxes/{code}").json()["label_print_count"] == 0

    def test_the_status_endpoint_is_reachable(self, client):
        response = client.get("/api/printer")

        assert response.status_code == 200
        assert response.json()["backend"] == "fake"
