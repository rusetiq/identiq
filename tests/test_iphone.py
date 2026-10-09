import copy
import json
import sqlite3

import pytest

from biometrics import Store
from iphone import POSES
from test_app import setup, image, vector, enroll


def capture(image, name="Phone person"):
    return {"format": "identiq.capture", "version": 1, "sensor": "truedepth", "consent": True,
            "name": name, "device": "iPhone / iOS test", "captured_at": "2026-10-06T12:00:00Z",
            "samples": [{"pose": pose, "yaw_radians": yaw, "image": image,
                         "depth": {"valid_fraction": 0.9, "median_m": 0.4, "spread_m": 0.03}}
                        for pose, yaw in POSES.items()]}


def post(client, headers, bundle, **extra):
    return client.post("/api/enroll/iphone", headers=headers,
                       json={"capture": bundle, "consent": True, **extra})


def test_phone_enrollment_recognition_and_metadata(setup, image):
    client, headers, _, database = setup
    bundle = capture(image)
    bundle["untrusted_extra"] = "Should not be retained"
    response = post(client, headers, bundle)
    assert response.status_code == 201
    people = client.get("/api/people").json["people"]
    assert people[0]["name"] == "Phone person"
    assert people[0]["iphone_samples"] == 5
    assert people[0]["samples"] == 5
    recognized = client.post("/api/recognize", json={"image": image}, headers=headers)
    assert recognized.json["person"]["name"] == "Phone person"
    with sqlite3.connect(database) as db:
        metadata = json.loads(db.execute("SELECT metadata FROM captures").fetchone()[0])
        assert metadata["sensor"] == "truedepth"
        assert "image" not in metadata["samples"][0]
        assert "untrusted_extra" not in metadata
        assert db.execute("SELECT count(*) FROM embeddings WHERE source='iphone_truedepth'").fetchone()[0] == 5
    assert client.delete(f"/api/people/{people[0]['id']}", headers=headers).status_code == 200
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM captures").fetchone()[0] == 0


def test_add_phone_to_existing_person_and_reject_repeat(setup, image):
    client, headers, _, _ = setup
    person_id = enroll(client, headers, image).json["person"]["id"]
    # Existing identity name stays unchanged; the file name is only used for new people.
    response = post(client, headers, capture(image, "Different file name"), person_id=person_id)
    assert response.status_code == 201
    assert response.json["person"]["name"] == "Alex Morgan"
    person = client.get("/api/people").json["people"][0]
    assert person["samples"] == 10 and person["iphone_samples"] == 5
    assert post(client, headers, capture(image), person_id=person_id).json["code"] == "phone_already_added"
    assert client.get("/api/people").json["people"][0]["samples"] == 10


def test_wrong_person_does_not_change_existing_enrollment(setup, image):
    client, headers, engine, _ = setup
    person_id = enroll(client, headers, image).json["person"]["id"]
    engine.vector = vector(1)
    response = post(client, headers, capture(image), person_id=person_id)
    assert response.json["code"] == "identity_mismatch"
    assert client.get("/api/people").json["people"][0]["samples"] == 5


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("format", "random"), ("sensor", "lidar"),
    ("consent", False), ("name", ""), ("device", {}), ("captured_at", "no date"),
    ("captured_at", "2026-10-06T12:00:00"), ("samples", []),
])
def test_invalid_file_rejected_atomically(setup, image, field, value):
    client, headers, _, _ = setup
    bundle = capture(image)
    bundle[field] = value
    assert post(client, headers, bundle).status_code == 422
    assert client.get("/api/people").json["people"] == []


@pytest.mark.parametrize("field,value", [("valid_fraction", 0.5), ("median_m", 3), ("spread_m", 0),
                                         ("valid_fraction", True), ("median_m", float("nan")),
                                         ("spread_m", float("inf"))])
def test_invalid_depth_summary_rejected(setup, image, field, value):
    client, headers, _, _ = setup
    bundle = capture(image)
    bundle["samples"][0]["depth"][field] = value
    assert post(client, headers, bundle).json["code"] == "invalid_capture"
    assert client.get("/api/people").json["people"] == []


def test_duplicate_pose_and_wrong_yaw_rejected(setup, image):
    client, headers, _, _ = setup
    bundle = capture(image)
    bundle["samples"][1] = copy.deepcopy(bundle["samples"][0])
    assert post(client, headers, bundle).json["code"] == "invalid_capture"
    bundle = capture(image)
    bundle["samples"][0]["yaw_radians"] = 0.4
    assert post(client, headers, bundle).json["code"] == "invalid_capture"


def test_bad_jpeg_and_missing_depth_rejected(setup, image):
    client, headers, _, _ = setup
    bundle = capture(image)
    bundle["samples"][0]["image"] = "invalid"
    assert post(client, headers, bundle).json["code"] == "invalid_image"
    bundle = capture(image)
    del bundle["samples"][0]["depth"]
    assert post(client, headers, bundle).json["code"] == "invalid_capture"


def test_import_requires_local_permission_and_valid_target(setup, image):
    client, headers, _, _ = setup
    assert post(client, {}, capture(image)).status_code == 403
    assert post(client, headers, capture(image), consent=False).json["code"] == "consent_required"
    assert post(client, headers, capture(image), person_id=True).json["code"] == "invalid_person"
    assert post(client, headers, capture(image), person_id=99).json["code"] == "person_missing"


def test_existing_database_migration_keeps_identity(tmp_path):
    database = tmp_path / "private" / "legacy.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT, created_at TEXT);"
                         "CREATE TABLE embeddings(person_id INTEGER REFERENCES people(id) ON DELETE CASCADE, vector BLOB);")
        db.execute("INSERT INTO people VALUES(1, 'Existing person', '2026-10-06T12:00:00Z')")
        db.executemany("INSERT INTO embeddings VALUES(1, ?)", [(vector().tobytes(),)] * 5)
    store = Store(database)
    assert store.people()[0]["name"] == "Existing person"
    assert store.people()[0]["samples"] == 5
    assert store.people()[0]["iphone_samples"] == 0
    assert store.candidates(vector())[0]["similarity"] == 1
    Store(database)  # Migration is repeatable.
