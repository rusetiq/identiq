import json
import base64
import re
import sqlite3
import itertools

import cv2
import numpy as np
import pytest

from app import create_app
from biometrics import EMBEDDING_DIMENSION, FaceError, Store, decode_frame, identify, normalize


def vector(index=0, second=None):
    result = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
    result[index] = 1
    if second is not None:
        result[second] = 1
    return normalize(result)


class FakeEngine:
    def __init__(self):
        self.vector = vector()
        self.error = None

    def analyze(self, frame):
        if self.error:
            return {"status": "adjust", "code": self.error.code, "message": str(self.error), "face_count": 0}
        return {"status": "ready", "face_count": 1, "box": [0.1, 0.1, 0.5, 0.5],
                "landmarks": [[0.3,0.3],[0.6,0.3],[0.45,0.5],[0.3,0.7],[0.6,0.7]],
                "quality": {"face_size_px": 160, "sharpness": 20, "brightness": 120, "detection_score": .99}}

    def extract(self, frame):
        if self.error:
            raise self.error
        return self.vector.copy(), [0.1, 0.1, 0.5, 0.5]

    def extract_with_quality(self, frame, attendance=False):
        embedding, box = self.extract(frame)
        return embedding, {**self.analyze(frame), 'box':box}

    def extract_attendance_group(self, frame):
        return self.extract_group(frame)


_frames = itertools.count(1)


def fresh_image(image):
    """Synthetic successive camera frames, with actual differing decoded pixels."""
    frame = decode_frame(image)
    cv2.putText(frame, str(next(_frames)), (2, 25), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,255,255), 2)
    _, encoded = cv2.imencode('.jpg', frame)
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode()


@pytest.fixture
def image():
    _, encoded = cv2.imencode(".jpg", np.zeros((320, 320, 3), dtype=np.uint8))
    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode()


@pytest.fixture
def setup(tmp_path):
    engine = FakeEngine()
    database = tmp_path / "private" / "test.sqlite3"
    app = create_app(database, engine)
    app.config["TESTING"] = True
    client = app.test_client()
    credentials = json.loads(app.extensions['portal_access'].staff_login_path.read_text())
    assert client.post('/api/staff/login', json=credentials, headers={'X-Portal-Request':'1'}).status_code == 200
    html = client.get("/").get_data(as_text=True)
    token = re.search(r'name="local-token" content="([^"]+)"', html).group(1)
    return client, {"X-Local-Token": token}, engine, database


def enroll(client, headers, image, **overrides):
    payload = {"name": "Alex Morgan", "consent": True, "images": [image] * 5}
    payload.update(overrides)
    return client.post("/api/enroll", json=payload, headers=headers)


def test_enroll_recognize_verify_persist_and_delete(setup, image):
    client, headers, engine, database = setup
    assert client.get("/api/status").json["people"] == 0
    saved = enroll(client, headers, image)
    assert saved.status_code == 201
    person_id = saved.json["person"]["id"]
    recognized = client.post("/api/recognize", json={"image": image}, headers=headers)
    assert recognized.json["person"] == {"id": person_id, "name": "Alex Morgan"}
    assert recognized.json["status"] == "matched"
    verified = client.post("/api/recognize", json={"image": image, "person_id": person_id}, headers=headers)
    assert verified.json["status"] == "matched"
    assert Store(database).people()[0]["samples"] == 5
    assert database.stat().st_mode & 0o777 == 0o600
    engine.vector = vector(1)
    assert client.post("/api/recognize", json={"image": image}, headers=headers).json["status"] == "unknown"
    assert client.delete(f"/api/people/{person_id}", headers=headers).json["deleted"]
    assert client.get("/api/people").json["people"] == []
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 0
    assert client.delete(f"/api/people/{person_id}", headers=headers).status_code == 404


def test_duplicate_does_not_create_second_identity(setup, image):
    client, headers, _, _ = setup
    assert enroll(client, headers, image).status_code == 201
    duplicate = enroll(client, headers, image, name="Another name")
    assert duplicate.json["code"] == "duplicate_face"
    assert len(client.get("/api/people").json["people"]) == 1


@pytest.mark.parametrize("payload,code", [
    ({"name": " "}, "invalid_name"), ({"name": "A" * 81}, "invalid_name"),
    ({"name": "A\nB"}, "invalid_name"), ({"name": 42}, "invalid_name"),
    ({"consent": False}, "consent_required"), ({"consent": "true"}, "consent_required"),
    ({"images": []}, "invalid_samples"), ({"images": ["not-a-frame"] * 5}, "invalid_image"),
])
def test_invalid_enrollment_is_atomic(setup, image, payload, code):
    client, headers, _, _ = setup
    response = enroll(client, headers, image, **payload)
    assert response.status_code == 422
    assert response.json["code"] == code
    assert client.get("/api/people").json["people"] == []


@pytest.mark.parametrize("code", ["no_face", "multiple_faces", "too_small", "blurry"])
def test_bad_camera_frame_is_rejected(setup, image, code):
    client, headers, engine, _ = setup
    engine.error = FaceError(code, "Try again")
    assert enroll(client, headers, image).json["code"] == code
    assert client.post("/api/recognize", json={"image": image}, headers=headers).json["code"] == code
    assert client.get("/api/people").json["people"] == []


def test_local_request_protection(setup, image):
    client, headers, _, _ = setup
    assert enroll(client, {}, image).status_code == 403
    assert enroll(client, {**headers, "Origin": "https://example.com"}, image).status_code == 403
    assert client.get("/api/people", headers={"Host": "evil.example"}).status_code == 403
    assert enroll(client, {**headers, "Origin": "http://localhost"}, image).status_code == 201
    response = client.get("/")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_wrong_claim_does_not_return_other_person(setup, image):
    client, headers, engine, _ = setup
    first = enroll(client, headers, image).json["person"]["id"]
    engine.vector = vector(1)
    second = enroll(client, headers, image, name="Taylor").json["person"]["id"]
    response = client.post("/api/recognize", json={"image": image, "person_id": first}, headers=headers)
    assert response.json["status"] == "mismatch"
    assert "person" not in response.json
    assert client.post("/api/recognize", json={"image": image, "person_id": second}, headers=headers).json["status"] == "matched"


@pytest.mark.parametrize("person_id", [True, "1", 0, -1])
def test_invalid_claim(setup, image, person_id):
    client, headers, _, _ = setup
    assert client.post("/api/recognize", json={"image": image, "person_id": person_id}, headers=headers).json["code"] == "invalid_person"


def test_deleted_claim_returns_actionable_error(setup, image):
    client, headers, _, _ = setup
    response = client.post("/api/recognize", json={"image": image, "person_id": 99}, headers=headers)
    assert response.json["code"] == "person_missing"


def test_request_size_and_non_object_payload(setup):
    client, headers, _, _ = setup
    assert client.post("/api/enroll", json=[], headers=headers).status_code == 422
    assert client.post("/api/enroll", data="x" * (12 * 1024 * 1024 + 1), content_type="application/json", headers=headers).status_code == 413


def test_ambiguous_match_is_not_accepted(tmp_path):
    store = Store(tmp_path / "private" / "test.db")
    store.enroll("Alex", [vector(0)] * 5)
    store.enroll("Taylor", [vector(1)] * 5)
    result = identify(store, vector(0, 1))
    assert result["status"] == "ambiguous"
    assert "person" not in result


def test_mixed_people_samples_not_saved(tmp_path):
    store = Store(tmp_path / "private" / "test.db")
    with pytest.raises(FaceError, match="same person"):
        store.enroll("Mixed", [vector(0), vector(1), vector(0), vector(1), vector(0)])
    assert store.people() == []


def test_empty_directory(tmp_path):
    store = Store(tmp_path / "private" / "test.db")
    assert identify(store, vector(0))["status"] == "empty"


@pytest.mark.parametrize("value", [None, 42, "data:image/png;base64,abc", "data:image/jpeg;base64,!!!", "data:image/jpeg;base64,eA=="])
def test_malformed_frames(value):
    with pytest.raises(FaceError):
        decode_frame(value)


def test_oversized_dimensions_are_rejected():
    _, encoded = cv2.imencode(".jpg", np.zeros((80, 2000, 3), dtype=np.uint8))
    with pytest.raises(FaceError, match="dimensions"):
        decode_frame("data:image/jpeg;base64," + base64.b64encode(encoded).decode())
