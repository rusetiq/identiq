"""Local YuNet detection, ArcFace embeddings, and SQLite identity storage."""
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
# pyrefly: ignore[missing-import]
import onnxruntime as ort
from PIL import Image
from attendance_policy import MATCH_THRESHOLD, MATCH_MARGIN, quality_issue

SAMPLES = 5
MAX_FACES = 20
EMBEDDING_DIMENSION = 512
ENROLLMENT_THRESHOLD = 0.45
ENROLLMENT_MARGIN = 0.06

# The five-point ArcFace reference landmark locations for a 112 x 112 crop.
ARCFACE_REFERENCE = np.asarray([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)


class FaceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize(vector):
    vector = np.asarray(vector, dtype=np.float32).flatten()
    norm = np.linalg.norm(vector)
    if vector.size != EMBEDDING_DIMENSION or not np.isfinite(vector).all() or norm < 1e-8:
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
        # Retaining thousands of proposed boxes is wasted work for a school
        # scanner that accepts at most MAX_FACES people in one frame. Keeping
        # a small headroom preserves crowded-frame detection while cutting the
        # detector's post-processing cost on modest CPUs.
        self.detector = cv2.FaceDetectorYN.create(
            str(model_dir / "face_detection_yunet_2023mar.onnx"), "", (640, 480), 0.75, 0.3, max(80, MAX_FACES * 4))
        model_path = model_dir / "arcface_w600k_r50.onnx"
        if not model_path.is_file():
            raise FaceError("models_missing", "Install the research face model: python scripts/download_models.py")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.recognizer = ort.InferenceSession(str(model_path), sess_options=options,
                                               providers=["CPUExecutionProvider"])
        self.recognizer_input = self.recognizer.get_inputs()[0].name
        self.recognizer_output = self.recognizer.get_outputs()[0].name

    def _arcface_embedding(self, image, face):
        if hasattr(self.recognizer, "alignCrop") and hasattr(self.recognizer, "feature"):
            aligned = self.recognizer.alignCrop(image, face)
            return normalize(self.recognizer.feature(aligned).copy())
        landmarks = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
        transform, _ = cv2.estimateAffinePartial2D(landmarks, ARCFACE_REFERENCE, method=cv2.LMEDS)
        if transform is None:
            raise FaceError("invalid_embedding", "Could not align facial features. Keep your face toward the camera.")
        aligned = cv2.warpAffine(image, transform, (112, 112), flags=cv2.INTER_LINEAR, borderValue=0)
        blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112),
                                     (127.5, 127.5, 127.5), swapRB=True)
        vector = self.recognizer.run([self.recognizer_output], {self.recognizer_input: blob})[0]
        return normalize(vector)

    def _detect_at_size(self, image, max_side):
        height, width = image.shape[:2]
        scale = min(1.0, max_side / max(width, height))
        detector_image = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
        self.detector.setInputSize((detector_image.shape[1], detector_image.shape[0]))
        _, faces = self.detector.detect(detector_image)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32)
        faces = faces.copy()
        faces[:, :14] /= scale
        return faces

    def _detect(self, image, max_side=640, far=False):
        faces=self._detect_at_size(image,max_side)
        # A second, larger pass preserves small facial features without upscaling
        # beyond the pixels actually supplied by the device camera.
        if far and max(image.shape[:2])>max_side and (not len(faces) or np.min(faces[:,2:4])<128):
            detailed=self._detect_at_size(image,1280)
            if len(detailed):
                candidates=np.concatenate([detailed,faces])
                kept=[]
                for face in sorted(candidates,key=lambda f:float(f[14]),reverse=True):
                    duplicate=False
                    for existing in kept:
                        left,top=max(face[0],existing[0]),max(face[1],existing[1])
                        right,bottom=min(face[0]+face[2],existing[0]+existing[2]),min(face[1]+face[3],existing[1]+existing[3])
                        intersection=max(0,right-left)*max(0,bottom-top)
                        union=face[2]*face[3]+existing[2]*existing[3]-intersection
                        if union>0 and intersection/union>.45:
                            duplicate=True;break
                    if not duplicate:kept.append(face)
                faces=np.asarray(kept,dtype=np.float32)
        return faces

    def _quality(self, image, face, minimum_fraction=.10, attendance=False):
        height, width = image.shape[:2]
        x, y, w, h = face[:4]
        crop = image[max(0, int(y)):min(height, int(y+h)), max(0, int(x)):min(width, int(x+w))]
        anchors = [[float(face[i] / width), float(face[i+1] / height)] for i in range(4, 14, 2)]
        # Evaluate every face at the same resolution. Raw-pixel variance penalized
        # larger, smoother webcam frames even when facial features were usable.
        sharpness, brightness = 0.0, 0.0
        if crop.size:
            # Convert 3-channel BGR crop to 1-channel grayscale first before downsampling to 128x128.
            # Downsampling a 1-channel array reduces interpolation math and memory bandwidth by ~66% compared to 3-channel BGR.
            gray = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (128, 128), interpolation=cv2.INTER_AREA)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            brightness = float(gray.mean())
        quality = {"face_size_px": round(float(min(w, h))), "sharpness": round(sharpness, 1),
                   "brightness": round(brightness, 1), "detection_score": round(float(face[14]), 3)}
        details = {"face_count": 1, "box": [float(x / width), float(y / height), float(w / width), float(h / height)], "landmarks": anchors, "quality": quality}
        if min(w, h) < 64 or min(w / width, h / height) < minimum_fraction:
            return {**details, "status": "adjust", "code": "too_small", "message": "Face detected. Move closer so your face fills more of the frame."}, face
        if crop.size == 0 or sharpness < 6:
            return {**details, "status": "adjust", "code": "blurry", "message": "Face detected, but details are very soft. Hold still and check camera focus."}, face
        issue = quality_issue(quality) if attendance else None
        if issue:
            return {**details, 'status':'adjust', **issue}, face
        return {**details, "status": "ready", "message": "Face detected. Ready to capture."}, face

    def _analyze(self, image, attendance=False):
        faces = self._detect(image)
        if len(faces) == 0:
            return {"status": "adjust", "code": "no_face", "face_count": 0,
                    "message": "No face detected. Look toward the camera."}, None
        if len(faces) != 1:
            height, width = image.shape[:2]
            boxes = [[float(f[0]/width),float(f[1]/height),float(f[2]/width),float(f[3]/height)] for f in faces]
            return {"status": "adjust", "code": "multiple_faces", "face_count": len(faces), "boxes": boxes,
                    "message": "More than one face detected. Keep one person in the frame for enrollment or selected-person verification."}, None
        return self._quality(image, faces[0], attendance=attendance)

    def _group(self, image, extract=False, attendance=False):
        detected = self._detect(image,max_side=960,far=True)
        if len(detected) > MAX_FACES:
            return {"status": "adjust", "code": "too_many_faces", "face_count": len(detected), "faces": [],
                    "message": f"Up to {MAX_FACES} faces can be scanned together. Split into smaller groups."}
        faces = []
        for face in detected:
            # Attendance can recognize a small, clear face in a wide scene;
            # enrollment still requires a face that fills enough of the frame.
            analysis, _ = self._quality(image, face,minimum_fraction=0,attendance=attendance)
            if extract and analysis['status'] == 'ready':
                analysis['embedding'] = self._arcface_embedding(image, face)
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

    def extract_attendance_group(self, image):
        with self.lock:
            return self._group(image, extract=True, attendance=True)

    def analyze(self, image):
        with self.lock:
            return self._analyze(image)[0]

    def extract(self, image):
        embedding, analysis = self.extract_with_quality(image)
        return embedding, analysis['box']

    def extract_with_quality(self, image, attendance=False):
        with self.lock:
            analysis, face = self._analyze(image, attendance=attendance)
            if analysis["status"] != "ready":
                raise FaceError(analysis["code"], analysis["message"])
            embedding = self._arcface_embedding(image, face)
            return embedding, analysis


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
            people_columns={row['name'] for row in db.execute('PRAGMA table_info(people)')}
            for column in ('student_id','class_name','grade','section','academic_year','employee_code'):
                if column not in people_columns:db.execute(f"ALTER TABLE people ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            if 'person_type' not in people_columns:db.execute("ALTER TABLE people ADD COLUMN person_type TEXT NOT NULL DEFAULT 'student'")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS school_student_id ON people(student_id COLLATE NOCASE) WHERE student_id<>''")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS school_employee_code ON people(employee_code COLLATE NOCASE) WHERE employee_code<>''")
            session_columns={row['name'] for row in db.execute('PRAGMA table_info(attendance_sessions)')}
            if 'class_name' not in session_columns:db.execute("ALTER TABLE attendance_sessions ADD COLUMN class_name TEXT NOT NULL DEFAULT ''")
            attendance_columns={row['name'] for row in db.execute('PRAGMA table_info(attendance_records)')}
            for column in ('student_id','class_name','person_type','grade','section','academic_year','employee_code'):
                if column not in attendance_columns:db.execute(f"ALTER TABLE attendance_records ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            for column in ('checked_in_at','checked_out_at'):
                if column not in attendance_columns: db.execute(f'ALTER TABLE attendance_records ADD COLUMN {column} TEXT')
            if 'campus_state' not in attendance_columns:
                db.execute("ALTER TABLE attendance_records ADD COLUMN campus_state TEXT NOT NULL DEFAULT 'not_arrived' CHECK(campus_state IN ('not_arrived','on_campus','checked_out','unknown'))")
                db.execute("UPDATE attendance_records SET campus_state='unknown' WHERE status='present'")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attendance_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL REFERENCES attendance_records(id) ON DELETE CASCADE,
                    action TEXT NOT NULL CHECK(action IN ('check_in','check_out','correction')),
                    source TEXT NOT NULL CHECK(source IN ('camera','automatic','staff')),
                    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS movements_record ON attendance_movements(record_id,id);
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
                    coalesce(sum(length(e.vector)=?),0) AS compatible_samples,
                    coalesce(sum(e.source='iphone_truedepth'),0) AS iphone_samples FROM people p
                LEFT JOIN embeddings e ON p.id=e.person_id GROUP BY p.id ORDER BY p.id DESC
            """, (EMBEDDING_DIMENSION * 4,))]

    def save_profile(self, data, person_id=None):
        fields=('name','person_type','student_id','grade','section','academic_year','employee_code','class_name')
        with self.lock,self.connect() as db:
            previous=dict(db.execute('SELECT * FROM people WHERE id=?',(person_id,)).fetchone() or {}) if person_id else {}
            if person_id and not previous:raise FaceError('person_missing','That school record was not found.')
            values={field:data.get(field,previous.get(field,'')) for field in fields}
            values['person_type']=data.get('person_type',previous.get('person_type','student'))
            if 'admission_number' in data:values['student_id']=data['admission_number']
            for field,value in values.items():
                if not isinstance(value,str) or len(value)>80 or any(ord(c)<32 for c in value):
                    raise FaceError('invalid_profile','Use school details up to 80 characters, without control characters.')
                values[field]=value.strip()
            if values['person_type'] not in ('student','staff'):raise FaceError('invalid_profile','Choose Student or Staff.')
            required=('name','student_id','grade','section','academic_year') if values['person_type']=='student' else ('name','employee_code')
            if any(not values[field] for field in required):raise FaceError('invalid_profile','Complete the required school details.')
            if values['person_type']=='student':
                values['employee_code']=''
                values['class_name']=f"{values['grade']} {values['section']}"
                if len(values['class_name'])>80:raise FaceError('invalid_profile','Grade and section together must fit within 80 characters.')
            else:
                for field in ('student_id','grade','section','academic_year','class_name'):values[field]=''
                has_parent_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parent_access'").fetchone()
                if person_id and has_parent_table and db.execute('SELECT 1 FROM parent_access WHERE person_id=? AND revoked_at IS NULL',(person_id,)).fetchone():
                    raise FaceError('parent_linked','Revoke this student’s parent access before changing them to staff.')
            try:
                if person_id:
                    db.execute('UPDATE people SET '+','.join(field+'=?' for field in fields)+' WHERE id=?',(*[values[f] for f in fields],person_id))
                else:
                    person_id=db.execute('INSERT INTO people('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',[values[f] for f in fields]).lastrowid
            except sqlite3.IntegrityError as error:
                raise FaceError('duplicate_school_id','That admission number or employee code is already assigned.') from error
        return next(person for person in self.people() if person['id']==person_id)

    def update_student_profile(self, person_id, student_id, class_name):
        for value in (student_id,class_name):
            if not isinstance(value,str) or len(value)>80 or any(ord(c)<32 for c in value):
                raise FaceError('invalid_profile','Use student IDs and class names up to 80 characters.')
        with self.lock,self.connect() as db:
            if db.execute('SELECT id FROM people WHERE id=?',(person_id,)).fetchone() is None:
                raise FaceError('person_missing','That student enrollment was not found.')
            try:db.execute('UPDATE people SET student_id=?,class_name=? WHERE id=?',(student_id.strip(),class_name.strip(),person_id))
            except sqlite3.IntegrityError as error:raise FaceError('duplicate_student_id','That student ID is already assigned.') from error
        return next(person for person in self.people() if person['id']==person_id)

    def candidates_batch(self, embeddings, top_two=False):
        """One version-checked snapshot and one matrix multiplication for a frame."""
        if not len(embeddings):
            return []
        with self.lock, self.connect() as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT value FROM identity_revision WHERE id=1').fetchone()[0]
            if self._identity_cache is None or self._identity_cache[0] != revision:
                rows = db.execute("SELECT p.id,p.name,e.vector FROM people p JOIN embeddings e ON p.id=e.person_id WHERE length(e.vector)=? ORDER BY p.id",
                                  (EMBEDDING_DIMENSION * 4,)).fetchall()
                grouped = {}
                for row in rows:
                    grouped.setdefault(row['id'], {'name':row['name'],'vectors':[]})['vectors'].append(np.frombuffer(row['vector'],dtype=np.float32))
                people = [{'id':person_id,'name':person['name']} for person_id,person in grouped.items()]
                # Unnormalized means preserve the existing average sample similarity.
                # Use float32 matrix operations to leverage SIMD vectorization and cut cache memory in half.
                matrix = np.asarray([np.mean(person['vectors'],axis=0,dtype=np.float32) for person in grouped.values()],dtype=np.float32).reshape((-1,EMBEDDING_DIMENSION))
                matrix.setflags(write=False)
                self._identity_cache = (revision, people, matrix)
            _, people, matrix = self._identity_cache
        if not people:
            return [[] for _ in embeddings]
        scores = np.asarray(embeddings,dtype=np.float32) @ matrix.T
        if top_two and len(people) > 2:
            # Score the entire directory, then keep the exact global winner and
            # runner-up. Two stable argmax passes avoid sorting/materializing
            # thousands of candidate dictionaries for every camera face.
            rows = np.arange(len(scores))
            first = np.argmax(scores, axis=1)
            first_scores = scores[rows, first].copy()
            scores[rows, first] = -np.inf
            second = np.argmax(scores, axis=1)
            return [[{**people[a], 'similarity':float(first_scores[i])},
                     {**people[b], 'similarity':float(scores[i,b])}]
                    for i,(a,b) in enumerate(zip(first,second))]
        return [[{**people[index], 'similarity':float(row[index])} for index in np.argsort(-row,kind='stable')] for row in scores]

    def candidates(self, embedding, top_two=False):
        return self.candidates_batch([embedding], top_two=top_two)[0]

    def enroll(self, name, vectors, source="laptop", metadata=None, person_id=None):
        # Serialize duplicate checking and insertion across concurrent enrollments.
        with self.lock:
            centroid = normalize(np.mean(vectors, axis=0))
            pairwise = np.asarray(vectors) @ np.asarray(vectors).T
            if float(pairwise.min()) < 0.363:
                raise FaceError("inconsistent_faces", "Samples do not show the same person clearly. Start again.")
            candidates = self.candidates(centroid)
            if person_id is not None:
                with self.connect() as db:
                    registered=db.execute('SELECT name FROM people WHERE id=?',(person_id,)).fetchone()
                if registered is None:raise FaceError('person_missing','That school record no longer exists.')
                existing = next((p for p in candidates if p["id"] == person_id), None)
                if existing is None and candidates and candidates[0]['similarity']>=ENROLLMENT_THRESHOLD:
                    raise FaceError('duplicate_face',f"This face is already enrolled as {candidates[0]['name']}.")
                if existing is not None and (existing["similarity"] < ENROLLMENT_THRESHOLD or candidates[0]["id"] != person_id or
                        (len(candidates) > 1 and candidates[0]["similarity"] - candidates[1]["similarity"] < ENROLLMENT_MARGIN)):
                    raise FaceError("identity_mismatch", "Samples do not clearly match the selected person. No patterns were added.")
            elif candidates and candidates[0]["similarity"] >= ENROLLMENT_THRESHOLD:
                raise FaceError("duplicate_face", f"This face is already enrolled as {candidates[0]['name']}.")
            with self.connect() as db:
                if person_id is None:
                    cursor = db.execute("INSERT INTO people (name) VALUES (?)", (name,))
                    person_id = cursor.lastrowid
                else:
                    name = registered['name']
                    if db.execute("SELECT count(*) FROM embeddings WHERE person_id=? AND source=? AND length(vector)=?", (person_id, source, EMBEDDING_DIMENSION * 4)).fetchone()[0] >= SAMPLES:
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
    return identify_candidates(store.candidates(embedding, top_two=claimed_id is None), claimed_id)


def identify_candidates(candidates, claimed_id=None):
    if claimed_id is not None and not any(p["id"] == claimed_id for p in candidates):
        raise FaceError("person_missing", "That enrollment no longer exists. Choose another person.")
    if not candidates:
        return {"status": "empty", "message": "Enroll someone to begin recognition."}
    best = candidates[0]
    similarity = round(best["similarity"], 4)
    margin = best['similarity'] - candidates[1]['similarity'] if len(candidates) > 1 else None
    # Safe to log or return to an authenticated staff client: it contains only
    # numerical scores, never embeddings, images, or identity labels.
    diagnostics = {'best_similarity': similarity,
                   'second_similarity': round(candidates[1]['similarity'], 4) if len(candidates) > 1 else None,
                   'margin': round(margin, 4) if margin is not None else None,
                   'candidate_count': len(candidates)}
    evidence = {'similarity': similarity, 'match_margin': margin, 'candidate_count': len(candidates),
                'match_diagnostics': diagnostics}
    if best["similarity"] < MATCH_THRESHOLD:
        return {"status": "unknown", "message": "Face not recognized.", **evidence}
    if margin is not None and margin < MATCH_MARGIN:
        return {"status": "ambiguous", "message": "Match is too close to another enrollment. Try again.", **evidence}
    if claimed_id is not None and best["id"] != claimed_id:
        return {"status": "mismatch", "message": "Face does not match the selected person.", **evidence}
    return {"status": "matched", "person": {"id": best["id"], "name": best["name"]}, **evidence}
