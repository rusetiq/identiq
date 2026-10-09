import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from biometrics import FaceEngine, FaceError, SAMPLES, THRESHOLD, Store, decode_frame, identify, identify_candidates
from iphone import valid_name, validate_capture
from attendance import Attendance
from parent_portal import PortalAccess

ROOT = Path(__file__).resolve().parent


def create_app(database=None, engine=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    token = secrets.token_urlsafe(32)
    store = Store(database or ROOT / "data" / "identities.sqlite3")
    attendance = Attendance(store)
    access = PortalAccess(store)
    app.extensions["portal_access"] = access
    app.extensions["attendance"] = attendance
    engine_lock = threading.Lock()

    def get_engine():
        nonlocal engine
        with engine_lock:
            if engine is None:
                if not models_ready():
                    raise FaceError("models_missing", "Install the face models: python scripts/download_models.py")
                engine = FaceEngine(ROOT / "models")
            return engine

    def models_ready():
        return engine is not None or all((ROOT / "models" / name).is_file() for name in (
            "face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"))

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
            return jsonify(error="Use localhost or 127.0.0.1.", code="invalid_host"), 403
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin requests are not allowed.", code="invalid_origin"), 403
        public = request.path.startswith('/static/') or request.path == '/parents' or request.path in {
            '/api/parent/login', '/api/parent/logout', '/api/parent/attendance', '/api/staff/login'}
        if request.path.startswith('/api/parent/') or request.path == '/api/staff/login':
            if request.method == 'POST' and request.headers.get('X-Portal-Request') != '1':
                return jsonify(error='Reload the sign-in page and try again.', code='invalid_portal_request'), 403
        if not public and not access.authenticate(request.cookies.get('identiq_staff'), 'staff'):
            if request.path == '/':
                return render_template('staff_login.html')
            return jsonify(error='Staff sign-in required.', code='staff_required'), 401
        if request.method in {"POST", "DELETE", "PUT", "PATCH"} and not public:
            if not secrets.compare_digest(request.headers.get("X-Local-Token", ""), token):
                return jsonify(error="Reload the app and try again.", code="invalid_token"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(FaceError)
    def face_error(error):
        return jsonify(error=str(error), code=error.code), 422

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description, code="request_error"), error.code

    @app.get("/")
    def index():
        return render_template("index.html", token=token)

    @app.get("/api/status")
    def status():
        return jsonify(models_ready=models_ready(), people=len(store.people()), threshold=THRESHOLD, samples=SAMPLES)

    @app.get("/api/people")
    def people():
        return jsonify(people=store.people())

    def payload():
        data = request.get_json()
        if not isinstance(data, dict):
            raise FaceError("invalid_request", "Send a JSON object.")
        return data

    @app.get('/parents')
    def parent_page():
        return render_template('parents.html')

    def sign_in(role):
        session_token = access.login(role, payload().get('code'), request.remote_addr)
        response = jsonify(signed_in=True)
        response.set_cookie('identiq_' + role, session_token, max_age=access.SESSION_SECONDS,
                            httponly=True, samesite='Strict', secure=request.is_secure,
                            path='/api/parent' if role == 'parent' else '/')
        return response

    @app.post('/api/staff/login')
    def staff_login():
        return sign_in('staff')

    @app.post('/api/staff/logout')
    def staff_logout():
        access.logout(request.cookies.get('identiq_staff'))
        response = jsonify(signed_out=True)
        response.delete_cookie('identiq_staff', path='/')
        return response

    @app.post('/api/parent/login')
    def parent_login():
        return sign_in('parent')

    @app.post('/api/parent/logout')
    def parent_logout():
        access.logout(request.cookies.get('identiq_parent'))
        response = jsonify(signed_out=True)
        response.delete_cookie('identiq_parent', path='/api/parent')
        return response

    @app.get('/api/parent/attendance')
    def parent_attendance():
        session = access.authenticate(request.cookies.get('identiq_parent'), 'parent')
        if session is None:
            return jsonify(error='Sign in with your parent access code.', code='parent_required'), 401
        return jsonify(access.student_view(session['parent_id'], request.args.get('month')))

    @app.get('/api/parents')
    def parent_links():
        return jsonify(parents=access.links())

    @app.post('/api/parents')
    def create_parent_link():
        data = payload()
        return jsonify(parent=access.create_link(data.get('name'), data.get('person_id'))), 201

    @app.post('/api/parents/<int:parent_id>/revoke')
    def revoke_parent_link(parent_id):
        access.revoke(parent_id)
        return jsonify(revoked=True)

    @app.post("/api/analyze")
    def analyze():
        data = payload()
        if 'group' in data and type(data['group']) is not bool:
            raise FaceError('invalid_request', 'Group mode must be true or false.')
        frame = decode_frame(data.get('image'))
        return jsonify(get_engine().analyze_group(frame) if data.get('group') else get_engine().analyze(frame))

    @app.post("/api/enroll")
    def enroll():
        data = payload()
        name = valid_name(data.get("name"))
        if data.get("consent") is not True:
            raise FaceError("consent_required", "Confirm permission to save this person's face pattern.")
        frames = data.get("images")
        if not isinstance(frames, list) or len(frames) != SAMPLES:
            raise FaceError("invalid_samples", f"Capture exactly {SAMPLES} face samples.")
        vectors = [get_engine().extract(decode_frame(frame))[0] for frame in frames]
        return jsonify(person=store.enroll(name.strip(), vectors)), 201

    @app.post("/api/enroll/iphone")
    def enroll_iphone():
        data = payload()
        if data.get("consent") is not True:
            raise FaceError("consent_required", "Confirm permission to import and save this face pattern.")
        name, frames, metadata = validate_capture(data.get("capture"))
        person_id = data.get("person_id")
        if person_id is not None and (type(person_id) is not int or person_id < 1):
            raise FaceError("invalid_person", "Choose a valid enrolled person.")
        vectors = [get_engine().extract(decode_frame(frame))[0] for frame in frames]
        return jsonify(person=store.enroll(name, vectors, "iphone_truedepth", metadata, person_id)), 201

    @app.post("/api/recognize")
    def recognize():
        data = payload()
        claimed_id = data.get("person_id")
        if claimed_id is not None and (type(claimed_id) is not int or claimed_id < 1):
            raise FaceError("invalid_person", "Choose a valid enrolled person.")
        session_id = data.get("attendance_session_id")
        scanner_id = None
        if session_id is not None:
            attendance.ensure_active(session_id)
            scanner_id = attendance.scanner(data.get("scanner_id"))
        try:
            vector, box = get_engine().extract(decode_frame(data.get("image")))
            recognition = identify(store, vector, claimed_id)
        except FaceError:
            if session_id is not None:
                attendance.reset(session_id, scanner_id)
            raise
        tracking = attendance.observe(session_id, scanner_id, recognition) if session_id is not None else None
        return jsonify(**recognition, box=box, threshold=THRESHOLD, attendance=tracking)

    @app.post('/api/recognize/group')
    def recognize_group():
        data = payload()
        if data.get('person_id') is not None:
            raise FaceError('invalid_person', 'Selected-person verification requires one face. Use the single-face scanner.')
        session_id = data.get('attendance_session_id')
        scanner_id = None
        if session_id is not None:
            attendance.ensure_active(session_id)
            scanner_id = attendance.scanner(data.get('scanner_id'))
        try:
            result = get_engine().extract_group(decode_frame(data.get('image')))
            matches = iter(identify_candidates(candidates) for candidates in store.candidates_batch([face['embedding'] for face in result['faces'] if face['status']=='ready']))
            faces = []
            for face in result['faces']:
                details = {key:value for key,value in face.items() if key != 'embedding'}
                faces.append({**details, **next(matches)} if face['status'] == 'ready' else details)
            counts = {}
            for face in faces:
                if face['status'] == 'matched':
                    person_id = face['person']['id']
                    counts[person_id] = counts.get(person_id, 0) + 1
            for face in faces:
                if face['status'] == 'matched' and counts[face['person']['id']] > 1:
                    face['status'] = 'ambiguous'
                    face['message'] = 'Multiple faces match the same enrollment. No attendance marked for those faces.'
            if session_id is not None:
                updates = attendance.observe_group(session_id, scanner_id, faces)
                for face, update in zip(faces, updates):
                    face['attendance'] = update
            return jsonify(**{**result, 'faces':faces}, threshold=THRESHOLD)
        except FaceError:
            if session_id is not None:
                attendance.reset(session_id, scanner_id)
            raise

    @app.get("/api/attendance/sessions")
    def attendance_sessions():
        return jsonify(sessions=attendance.sessions())

    @app.get('/api/attendance/overview')
    def attendance_overview():
        selected = request.args.get('session_id')
        if selected is not None and (len(selected) > 18 or not selected.isascii() or not selected.isdecimal() or int(selected) < 1):
            raise FaceError('invalid_session', 'Choose a valid attendance session.')
        with store.lock:
            sessions = attendance.sessions()
            session_id = int(selected) if selected is not None else next((s['id'] for s in sessions if s['status']=='active'), sessions[0]['id'] if sessions else None)
            detail = attendance.detail(session_id) if session_id is not None else None
        return jsonify(sessions=sessions, session=detail)

    @app.delete('/api/attendance/sessions')
    def delete_all_attendance():
        if payload().get('confirmed') is not True:
            raise FaceError('confirmation_required','Confirm deletion of all attendance first.')
        return jsonify(deleted=True, **attendance.clear_all())

    @app.post("/api/attendance/sessions")
    def start_attendance():
        return jsonify(session=attendance.start(payload().get("name"))), 201

    @app.get("/api/attendance/sessions/<int:session_id>")
    def attendance_detail(session_id):
        return jsonify(session=attendance.detail(session_id))

    @app.post("/api/attendance/sessions/<int:session_id>/finish")
    def finish_attendance(session_id):
        return jsonify(session=attendance.finish(session_id))

    @app.patch("/api/attendance/sessions/<int:session_id>/records/<int:record_id>")
    def correct_attendance(session_id, record_id):
        return jsonify(session=attendance.correct(session_id, record_id, payload().get("status")))

    @app.get("/api/attendance/sessions/<int:session_id>/export.csv")
    def export_attendance(session_id):
        return Response(attendance.export(session_id), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="attendance-{session_id}.csv"'})

    @app.delete("/api/people/<int:person_id>")
    def delete(person_id):
        if not store.delete(person_id):
            return jsonify(error="Person not found.", code="not_found"), 404
        return jsonify(deleted=True)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), debug=False, threaded=True)
