"""Session attendance: immutable roster snapshots and server-confirmed sightings."""
import csv
import io
import time
import uuid
import sqlite3

from biometrics import FaceError
from iphone import valid_name


class Attendance:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.sightings = {}

    def sessions(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT s.*, count(r.id) AS total,
                    coalesce(sum(r.status='present'),0) AS present,
                    coalesce(sum(r.status='absent'),0) AS absent,
                    coalesce(sum(r.status='pending'),0) AS pending
                FROM attendance_sessions s LEFT JOIN attendance_records r ON r.session_id=s.id
                GROUP BY s.id ORDER BY s.id DESC
            """)]

    def clear_all(self):
        """Remove attendance only; keep IDs monotonic and all enrollment/access data."""
        def purge(db):
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts={name:db.execute(f'SELECT count(*) FROM {name}').fetchone()[0] if name in tables else 0
                    for name in ('attendance_sessions','attendance_records')}
            db.execute('PRAGMA secure_delete=ON')
            with db:
                if 'attendance_records' in tables: db.execute('DELETE FROM attendance_records')
                if 'attendance_sessions' in tables: db.execute('DELETE FROM attendance_sessions')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return counts
        with self.store.lock:
            backups=0
            for path in (self.store.path.parent/'backups').glob('*.sqlite3'):
                if path.is_symlink() or not path.is_file(): continue
                with sqlite3.connect(path,timeout=10) as db: purge(db)
                backups+=1
            with self.store.connect() as db: counts=purge(db)
            self.sightings.clear()
        return {'sessions_deleted':counts['attendance_sessions'],'records_deleted':counts['attendance_records'],'backups_cleared':backups}

    def detail(self, session_id):
        with self.store.connect() as db:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            roster = [dict(row) for row in db.execute(
                "SELECT * FROM attendance_records WHERE session_id=? ORDER BY name COLLATE NOCASE, id", (session_id,))]
        result = dict(session)
        result["roster"] = roster
        result["counts"] = {status: sum(row["status"] == status for row in roster) for status in ("present", "absent", "pending")}
        result["counts"]["total"] = len(roster)
        return result

    def start(self, name):
        name = valid_name(name)
        with self.store.lock, self.store.connect() as db:
            if db.execute("SELECT 1 FROM attendance_sessions WHERE status='active'").fetchone():
                raise FaceError("session_active", "Finish the current session before starting another.")
            people = db.execute("SELECT DISTINCT p.id, p.name FROM people p JOIN embeddings e ON e.person_id=p.id ORDER BY p.id").fetchall()
            if not people:
                raise FaceError("empty_roster", "Enroll at least one person before starting attendance.")
            session_id = db.execute("INSERT INTO attendance_sessions(name) VALUES(?)", (name,)).lastrowid
            db.executemany("INSERT INTO attendance_records(session_id,person_id,name) VALUES(?,?,?)",
                           [(session_id, person["id"], person["name"]) for person in people])
        return self.detail(session_id)

    def ensure_active(self, session_id, db=None):
        if type(session_id) is not int or session_id < 1:
            raise FaceError("invalid_session", "Choose a valid attendance session.")
        if db is None:
            with self.store.connect() as connection:
                return self.ensure_active(session_id, connection)
        session = db.execute("SELECT status FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise FaceError("session_missing", "That attendance session was not found.")
        if session["status"] != "active":
            raise FaceError("session_closed", "This session has ended. Start a new session to take attendance.")

    def scanner(self, value):
        if not isinstance(value, str) or len(value) != 36:
            raise FaceError("invalid_scanner", "Restart the camera scan and try again.")
        try:
            uuid.UUID(value)
        except ValueError as exc:
            raise FaceError("invalid_scanner", "Restart the camera scan and try again.") from exc
        return value

    def reset(self, session_id, scanner_id):
        with self.store.lock:
            self.sightings = {key: value for key, value in self.sightings.items() if key[:2] != (session_id, scanner_id)}

    def observe_group(self, session_id, scanner_id, recognitions):
        scanner_id = self.scanner(scanner_id)
        with self.store.lock, self.store.connect() as db:
            self.ensure_active(session_id, db)
            now = self.clock()
            present_ids = {item['person']['id'] for item in recognitions if item['status'] == 'matched'}
            # Observation state belongs to an identity, never a face-array position.
            # Drop missing identities from the short-lived duplicate-request cache.
            self.sightings = {key: value for key, value in self.sightings.items()
                             if now-value['time'] < 30 and not (len(key) == 3 and key[:2] == (session_id, scanner_id) and key[2] not in present_ids)}
            return [self.observe(session_id, scanner_id, item, group=True, _db=db, _now=now, _checked=True) if item['status'] == 'matched'
                    else {'status':'unrecognized','confirmations':0,'message':'No attendance marked.'} for item in recognitions]

    def observe(self, session_id, scanner_id, recognition, group=False, _db=None, _now=None, _checked=False):
        if _db is None:
            with self.store.lock, self.store.connect() as db:
                return self.observe(session_id, scanner_id, recognition, group, db)
        scanner_id = self.scanner(scanner_id)
        key = (session_id, scanner_id, recognition['person']['id']) if group else (session_id, scanner_id)
        with self.store.lock:
            if not _checked:
                self.ensure_active(session_id, _db)
            now = self.clock() if _now is None else _now
            # Limit ephemeral confirmation state; timestamps are not stored images.
            if not _checked:
                self.sightings = {k: v for k, v in self.sightings.items() if now-v["time"] < 30}
            if recognition["status"] != "matched":
                self.sightings.pop(key, None)
                return {"status": "unrecognized", "confirmations": 0, "message": "No attendance marked."}
            person_id = recognition["person"]["id"]
            row = _db.execute("SELECT * FROM attendance_records WHERE session_id=? AND person_id=?", (session_id, person_id)).fetchone()
            if row is None:
                self.sightings.pop(key, None)
                return {"status": "outside_roster", "confirmations": 0, "message": "Recognized, but not in this session's starting roster."}
            previous = self.sightings.get(key)
            count = 1
            if previous and previous["person"] == person_id and now-previous["time"] <= 4:
                # Avoid rewriting timestamps for rapid duplicate observations.
                if now-previous["time"] < 0.25:
                    status = 'manual_override' if row['method']=='manual' else 'present' if row['status']=='present' else 'confirming'
                    return {"status":status,"confirmations":previous['count'],"record_id":row['id'],"newly_present":False,
                            "attendance_status":row['status'],"message":"Manual attendance status kept."}
            if len(self.sightings) >= 256 and key not in self.sightings:
                self.sightings.pop(next(iter(self.sightings)))
            self.sightings[key] = {"person": person_id, "count": count, "time": now}
            if row["method"] == "manual":
                return {"status": "manual_override", "confirmations": count, "record_id": row["id"],
                        "attendance_status":row["status"], "message": "Manual attendance status kept."}
            newly_present = row["status"] != "present"
            _db.execute("""UPDATE attendance_records SET status='present', method='camera',
                    first_seen_at=coalesce(first_seen_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    last_seen_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""", (row["id"],))
            return {"status": "present", "confirmations": 1, "record_id": row["id"], "newly_present": newly_present}

    def finish(self, session_id):
        with self.store.lock, self.store.connect() as db:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            if session["status"] == "active":
                db.execute("UPDATE attendance_records SET status='absent',method='session_end' WHERE session_id=? AND status='pending'", (session_id,))
                db.execute("UPDATE attendance_sessions SET status='closed',ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (session_id,))
            self.sightings = {key: value for key, value in self.sightings.items() if key[0] != session_id}
        return self.detail(session_id)

    def correct(self, session_id, record_id, status):
        if not isinstance(status, str) or status not in {"present", "absent", "pending"}:
            raise FaceError("invalid_attendance", "Choose Present, Absent or Not seen yet.")
        with self.store.lock, self.store.connect() as db:
            session = db.execute("SELECT status FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            if session["status"] == "closed" and status == "pending":
                raise FaceError("invalid_attendance", "Ended sessions can only contain Present or Absent.")
            updated = db.execute("UPDATE attendance_records SET status=?,method=? WHERE session_id=? AND id=?",
                                 (status, None if status == "pending" else "manual", session_id, record_id)).rowcount
            if not updated:
                raise FaceError("record_missing", "That person is not in this attendance session.")
            self.sightings = {key: value for key, value in self.sightings.items() if key[0] != session_id}
        return self.detail(session_id)

    def export(self, session_id):
        session = self.detail(session_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["Session", "Started (UTC)", "Ended (UTC)", "Name", "Status", "First seen (UTC)", "Last seen (UTC)", "Source"])
        def safe(value):
            text = str(value or "")
            return "'"+text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for record in session["roster"]:
            writer.writerow([safe(value) for value in (session["name"], session["started_at"], session["ended_at"], record["name"],
                record["status"], record["first_seen_at"], record["last_seen_at"], record["method"])])
        return stream.getvalue()
