"""Export, manifest and backup endpoints."""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def seeded(client):
    room = client.post("/api/rooms", json={"name": "Kitchen"}).json()
    code = client.post(
        "/api/boxes",
        json={"destination_room_id": room["id"], "content_summary": "pots", "weight_kg": 12.4},
    ).json()["code"]
    client.post(f"/api/boxes/{code}/items", json={"name": "cafetiere", "qty": 2})
    client.post("/api/boxes", json={"content_summary": "unassigned stuff"})
    return code


def test_json_export_is_downloadable(client, seeded):
    response = client.get("/api/export.json")

    assert response.status_code == 200
    assert response.json()["box_count"] == 2


def test_json_export_is_offered_as_a_file(client, seeded):
    # It is a backup you keep, not a page you read.
    response = client.get("/api/export.json")

    assert "attachment" in response.headers.get("content-disposition", "")


def test_csv_export_parses_as_csv(client, seeded):
    response = client.get("/api/export.csv")

    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["code"] for r in rows] == ["B-0001", "B-0002"]
    assert rows[0]["items"] == "cafetiere x2"


def test_the_manifest_groups_by_room(client, seeded):
    response = client.get("/api/manifest")

    rooms = {group["room"]: group["count"] for group in response.json()}
    assert rooms == {"Kitchen": 1, "Unassigned": 1}


def test_the_manifest_pdf_is_a_pdf(client, seeded):
    response = client.get("/api/manifest.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_the_manifest_pdf_works_with_no_boxes_at_all(client):
    # Printing the manifest before packing anything must not 500.
    response = client.get("/api/manifest.pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_a_backup_can_be_triggered_over_http(client, seeded, config):
    response = client.post("/api/backup")

    assert response.status_code == 200
    body = response.json()
    assert body["kept"] == 1
    assert (config.backup_dir or config.db_path.parent / "backups").is_dir()


def test_the_backup_endpoint_reports_the_file_it_wrote(client, seeded):
    body = client.post("/api/backup").json()

    assert body["file"].endswith(".db")
    assert body["bytes"] > 0
