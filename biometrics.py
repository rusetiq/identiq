"""Local YuNet detection, SFace embeddings, and SQLite identity storage."""
import base64
import binascii
import io
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SAMPLES = 5
MAX_FACES = 8
THRESHOLD = 0.45
MARGIN = 0.06


class FaceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize(vector):
    vector = np.asarray(vector, dtype=np.float32).flatten()
    norm = np.linalg.norm(vector)
    if vector.size != 128 or not np.isfinite(vector).all() or norm < 1e-8:
        raise FaceError("invalid_embedding", "Could not read a valid face pattern. Try again.")
    return vector / norm


def decode_frame(value):
    if not isinstance(value, str) or not value.startswith("data:image/jpeg;base64,"):
        raise FaceError("invalid_image", "Send a JPEG camera frame.")
    if len(value) > 2_000_000:
        raise FaceError("invalid_image", "Camera frame is too large.")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        # Read JPEG dimensions before decoding to bound decompression memory.
        with Image.open(io.BytesIO(raw)) as header:
            if header.format != "JPEG" or min(header.size) < 80 or max(header.size) > 1920:
                raise FaceError("invalid_image", "Frame dimensions must be between 80 and 1920 pixels.")
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError()
    except (ValueError, binascii.Error, OSError, Image.DecompressionBombError) as exc:
        if isinstance(exc, FaceError):
            raise
        raise FaceError("invalid_image", "Camera frame could not be decoded.") from exc
    return frame


class FaceEngine:
    def __init__(self, model_dir):
        self.lock = threading.Lock()
        self.detector = cv2.FaceDetectorYN.create(
            str(model_dir / "face_detection_yunet_2023mar.onnx"), "", (640, 480), 0.75, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(model_dir / "face_recognition_sface_2021dec.onnx"), "")

    def _detect(self, image):
        height, width = image.shape[:2]
        scale = min(1.0, 640 / max(width, height))
        detector_image = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
        self.detector.setInputSize((detector_image.shape[1], detector_image.shape[0]))
        _, faces = self.detector.detect(detector_image)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32)
        faces = faces.copy()
        faces[:, :14] /= scale
        return faces

    def _quality(self, image, face):
        height, width = image.shape[:2]
        x, y, w, h = face[:4]
        crop = image[max(0, int(y)):min(height, int(y+h)), max(0, int(x)):min(width, int(x+w))]
        anchors = [[float(face[i] / width), float(face[i+1] / height)] for i in range(4, 14, 2)]
        # Evaluate every face at the same resolution. Raw-pixel variance penalized
        # larger, smoother webcam frames even when facial features were usable.
        sharpness, brightness = 0.0, 0.0
        if crop.size:
            gray = cv2.cvtColor(cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            brightness = float(gray.mean())
        quality = {"face_size_px": round(float(min(w, h))), "sharpness": round(sharpness, 1),
                   "brightness": round(brightness, 1), "detection_score": round(float(face[14]), 3)}
        details = {"face_count": 1, "box": [float(x / width), float(y / height), float(w / width), float(h / height)], "landmarks": anchors, "quality": quality}
        if min(w, h) < 64 or min(w / width, h / height) < 0.10:
            return {**details, "status": "adjust", "code": "too_small", "message": "Face detected. Move closer so your face fills more of the frame."}, face
        if crop.size == 0 or sharpness < 6:
            return {**details, "status": "adjust", "code": "blurry", "message": "Face detected, but details are very soft. Hold still and check camera focus."}, face
        return {**details, "status": "ready", "message": "Face detected. Ready to capture."}, face

    def _analyze(self, image):
        faces = self._detect(image)
        if len(faces) == 0:
            return {"status": "adjust", "code": "no_face", "face_count": 0,
                    "message": "No face detected. Look toward the camera."}, None
        if len(faces) != 1:
            height, width = image.shape[:2]
            boxes = [[float(f[0]/width),float(f[1]/height),float(f[2]/width),float(f[3]/height)] for f in faces]
            return {"status": "adjust", "code": "multiple_faces", "face_count": len(faces), "boxes": boxes,
                    "message": "More than one face detected. Keep one person in the frame for enrollment or selected-person verification."}, None
        return self._quality(image, faces[0])

    def _group(self, image, extract=False):
        detected = self._detect(image)
        if len(detected) > MAX_FACES:
            return {"status": "adjust", "code": "too_many_faces", "face_count": len(detected), "faces": [],
                    "message": f"Up to {MAX_FACES} faces can be scanned together. Split into smaller groups."}
        faces = []
        for face in detected:
            analysis, _ = self._quality(image, face)
            if extract and analysis['status'] == 'ready':
                aligned = self.recognizer.alignCrop(image, face)
                analysis['embedding'] = normalize(self.recognizer.feature(aligned).copy())
            faces.append(analysis)
        ready = sum(face['status'] == 'ready' for face in faces)
        return {"status": "ready" if ready else "adjust", "face_count": len(faces), "ready_count": ready, "faces": faces,
                "code": None if ready else "no_face" if not faces else "quality",
                "message": f"{len(faces)} faces detected · {ready} ready to recognize." if faces else "No faces detected. Look toward the camera."}

    def analyze_group(self, image):
        with self.lock:
            return self._group(image)

    def extract_group(self, image):
        with self.lock:
            return self._group(image, extract=True)

    def analyze(self, image):
        with self.lock:
            return self._analyze(image)[0]

    def extract(self, image):
        with self.lock:
            analysis, face = self._analyze(image)
            if analysis["status"] != "ready":
                raise FaceError(analysis["code"], analysis["message"])
            aligned = self.recognizer.alignCrop(image, face)
            embedding = normalize(self.recognizer.feature(aligned).copy())
            return embedding, analysis["box"]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self.lock = threading.RLock()
        self._identity_cache = None
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    vector BLOB NOT NULL,
                    source TEXT NOT NULL DEFAULT 'laptop'
                );
                CREATE TABLE IF NOT EXISTS captures (
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attendance_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'closed'))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_attendance_session
                    ON attendance_sessions(status) WHERE status='active';
                CREATE TABLE IF NOT EXISTS attendance_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
                    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'present', 'absent')),
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    method TEXT,
                    UNIQUE(session_id, person_id)
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(embeddings)")}
            if "source" not in columns:
                db.execute("ALTER TABLE embeddings ADD COLUMN source TEXT NOT NULL DEFAULT 'laptop'")
            db.executescript("""
                CREATE INDEX IF NOT EXISTS embeddings_person ON embeddings(person_id);
                CREATE INDEX IF NOT EXISTS attendance_person_session ON attendance_records(person_id,session_id DESC);
                CREATE TABLE IF NOT EXISTS identity_revision (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
                INSERT OR IGNORE INTO identity_revision VALUES(1,0);
            """)
            for table in ('people','embeddings'):
                for event in ('INSERT','UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS revision_{table}_{event.lower()} AFTER {event} ON {table} BEGIN UPDATE identity_revision SET value=value+1 WHERE id=1; END")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def people(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT p.*, count(e.person_id) AS samples,
                    sum(e.source='iphone_truedepth') AS iphone_samples FROM people p
                JOIN embeddings e ON p.id=e.person_id GROUP BY p.id ORDER BY p.id DESC
            """)]

    def candidates_batch(self, embeddings):
        """One version-checked snapshot and one matrix multiplication for a frame."""
        if not len(embeddings):
            return []
        with self.lock, self.connect() as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT value FROM identity_revision WHERE id=1').fetchone()[0]
            if self._identity_cache is None or self._identity_cache[0] != revision:
                rows = db.execute("SELECT p.id,p.name,e.vector FROM people p JOIN embeddings e ON p.id=e.person_id ORDER BY p.id").fetchall()
                grouped = {}
                for row in rows:
                    grouped.setdefault(row['id'], {'name':row['name'],'vectors':[]})['vectors'].append(np.frombuffer(row['vector'],dtype=np.float32))
                people = [{'id':person_id,'name':person['name']} for person_id,person in grouped.items()]
                # Unnormalized means preserve the existing average sample similarity.
                matrix = np.asarray([np.mean(person['vectors'],axis=0,dtype=np.float64) for person in grouped.values()],dtype=np.float64).reshape((-1,128))
                matrix.setflags(write=False)
                self._identity_cache = (revision, people, matrix)
            _, people, matrix = self._identity_cache
        if not people:
            return [[] for _ in embeddings]
        scores = np.asarray(embeddings,dtype=np.float64) @ matrix.T
        return [[{**people[index], 'similarity':float(row[index])} for index in np.argsort(-row,kind='stable')] for row in scores]

    def candidates(self, embedding):
        return self.candidates_batch([embedding])[0]

    def enroll(self, name, vectors, source="laptop", metadata=None, person_id=None):
        # Serialize duplicate checking and insertion across concurrent enrollments.
        with self.lock:
            centroid = normalize(np.mean(vectors, axis=0))
            pairwise = np.asarray(vectors) @ np.asarray(vectors).T
            if float(pairwise.min()) < 0.363:
                raise FaceError("inconsistent_faces", "Samples do not show the same person clearly. Start again.")
            candidates = self.candidates(centroid)
            if person_id is not None:
                existing = next((p for p in candidates if p["id"] == person_id), None)
                if existing is None:
                    raise FaceError("person_missing", "That enrollment no longer exists.")
                if (existing["similarity"] < THRESHOLD or candidates[0]["id"] != person_id or
                        (len(candidates) > 1 and candidates[0]["similarity"] - candidates[1]["similarity"] < MARGIN)):
                    raise FaceError("identity_mismatch", "Phone samples do not clearly match the selected person. No patterns were added.")
            elif candidates and candidates[0]["similarity"] >= THRESHOLD:
                raise FaceError("duplicate_face", f"This face is already enrolled as {candidates[0]['name']}.")
            with self.connect() as db:
                if person_id is None:
                    cursor = db.execute("INSERT INTO people (name) VALUES (?)", (name,))
                    person_id = cursor.lastrowid
                else:
                    name = existing["name"]
                    if db.execute("SELECT count(*) FROM embeddings WHERE person_id=? AND source=?", (person_id, source)).fetchone()[0] >= SAMPLES:
                        raise FaceError("phone_already_added", "This person already has phone samples. Remove and enroll again to replace them.")
                db.executemany("INSERT INTO embeddings (person_id, vector, source) VALUES (?, ?, ?)",
                               [(person_id, normalize(vector).tobytes(), source) for vector in vectors])
                if metadata is not None:
                    db.execute("INSERT INTO captures (person_id, source, metadata) VALUES (?, ?, ?)",
                               (person_id, source, json.dumps(metadata, allow_nan=False)))
            return {"id": person_id, "name": name, "samples": len(vectors)}

    def delete(self, person_id):
        with self.lock, self.connect() as db:
            return db.execute("DELETE FROM people WHERE id=?", (person_id,)).rowcount > 0


def identify(store, embedding, claimed_id=None):
    return identify_candidates(store.candidates(embedding), claimed_id)


def identify_candidates(candidates, claimed_id=None):
    if claimed_id is not None and not any(p["id"] == claimed_id for p in candidates):
        raise FaceError("person_missing", "That enrollment no longer exists. Choose another person.")
    if not candidates:
        return {"status": "empty", "message": "Enroll someone to begin recognition."}
    best = candidates[0]
    similarity = round(best["similarity"], 4)
    if best["similarity"] < THRESHOLD:
        return {"status": "unknown", "message": "Face not recognized.", "similarity": similarity}
    if len(candidates) > 1 and best["similarity"] - candidates[1]["similarity"] < MARGIN:
        return {"status": "ambiguous", "message": "Match is too close to another enrollment. Try again.", "similarity": similarity}
    if claimed_id is not None and best["id"] != claimed_id:
        return {"status": "mismatch", "message": "Face does not match the selected person.", "similarity": similarity}
    return {"status": "matched", "person": {"id": best["id"], "name": best["name"]}, "similarity": similarity}
