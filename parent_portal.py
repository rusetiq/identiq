"""Scoped parent attendance views and local staff/parent access sessions."""
import hashlib
import os
import secrets
import time
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from biometrics import FaceError
from iphone import valid_name


class PortalAccess:
    SESSION_SECONDS = 12 * 60 * 60

    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        self.failures = {}
        self.staff_code_path = store.path.parent / 'staff-access-code.txt'
        try:
            descriptor = os.open(self.staff_code_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                file.write(secrets.token_urlsafe(24) + '\n')
        except FileExistsError:
            pass
        self.staff_code_path.chmod(0o600)
        self.staff_hash = self.digest(self.staff_code_path.read_text().strip())
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS parent_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS parent_person ON parent_access(person_id);
                CREATE TABLE IF NOT EXISTS portal_sessions (
                    token_hash TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('staff','parent')),
                    parent_id INTEGER REFERENCES parent_access(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS portal_parent ON portal_sessions(parent_id);
                CREATE INDEX IF NOT EXISTS portal_expiry ON portal_sessions(expires_at);
            ''')

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def login(self, role, code, remote):
        now = self.clock()
        key = (role, remote)
        with self.store.lock:
            self.failures = {k: [t for t in times if now-t < 60] for k, times in self.failures.items() if any(now-t < 60 for t in times)}
            if len(self.failures.get(key, [])) >= 10:
                raise FaceError('login_limited', 'Too many attempts. Wait one minute before trying again.')
            candidate = self.digest(code.strip()) if isinstance(code, str) and 1 <= len(code) <= 128 else ''
            parent_id = None
            valid = role == 'staff' and secrets.compare_digest(candidate, self.staff_hash)
            if role == 'parent':
                with self.store.connect() as db:
                    parent = db.execute('SELECT id FROM parent_access WHERE code_hash=? AND revoked_at IS NULL', (candidate,)).fetchone()
                valid = parent is not None
                parent_id = parent['id'] if valid else None
            if not valid:
                self.failures.setdefault(key, []).append(now)
                raise FaceError('invalid_access', 'That access code is invalid or has been revoked.')
            self.failures.pop(key, None)
            session_token = secrets.token_urlsafe(32)
            with self.store.connect() as db:
                db.execute('DELETE FROM portal_sessions WHERE expires_at<=?', (now,))
                db.execute('INSERT INTO portal_sessions VALUES(?,?,?,?)', (self.digest(session_token), role, parent_id, now+self.SESSION_SECONDS))
            return session_token

    def authenticate(self, token, role):
        if not isinstance(token, str) or len(token) > 128:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT s.* FROM portal_sessions s LEFT JOIN parent_access p ON s.parent_id=p.id
                WHERE s.token_hash=? AND s.role=? AND s.expires_at>? AND
                (s.role='staff' OR (p.id IS NOT NULL AND p.revoked_at IS NULL))''',
                (self.digest(token), role, self.clock())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        if isinstance(token, str) and len(token) <= 128:
            with self.store.connect() as db:
                db.execute('DELETE FROM portal_sessions WHERE token_hash=?', (self.digest(token),))

    def links(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('''SELECT a.id,a.name,a.person_id,p.name AS student_name,a.created_at
                FROM parent_access a JOIN people p ON p.id=a.person_id WHERE a.revoked_at IS NULL ORDER BY a.id DESC''')]

    def create_link(self, name, person_id):
        name = valid_name(name)
        if type(person_id) is not int or person_id < 1:
            raise FaceError('invalid_person', 'Choose an enrolled student.')
        code = secrets.token_urlsafe(24)
        with self.store.lock, self.store.connect() as db:
            person = db.execute('SELECT name FROM people WHERE id=?', (person_id,)).fetchone()
            if person is None:
                raise FaceError('person_missing', 'That student is no longer enrolled.')
            parent_id = db.execute('INSERT INTO parent_access(name,person_id,code_hash) VALUES(?,?,?)',
                                  (name, person_id, self.digest(code))).lastrowid
        return {'id': parent_id, 'name': name, 'student_name': person['name'], 'person_id': person_id, 'access_code': code}

    def revoke(self, parent_id):
        with self.store.lock, self.store.connect() as db:
            changed = db.execute("UPDATE parent_access SET revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND revoked_at IS NULL", (parent_id,)).rowcount
            db.execute('DELETE FROM portal_sessions WHERE parent_id=?', (parent_id,))
        if not changed:
            raise FaceError('parent_missing', 'That parent access was not found.')

    def student_view(self, parent_id, month=None):
        attendance_timezone = os.environ.get('ATTENDANCE_TIMEZONE', 'Asia/Dubai')
        zone = ZoneInfo(attendance_timezone)
        if month is not None:
            if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}',month):
                raise FaceError('invalid_month','Choose a valid calendar month.')
            year, number = map(int, month.split('-'))
            if not 1900 <= year <= 2100 or not 1 <= number <= 12:
                raise FaceError('invalid_month','Choose a valid calendar month.')
            start = datetime(year,number,1,tzinfo=zone)
            end = datetime(year+1,1,1,tzinfo=zone) if number==12 else datetime(year,number+1,1,tzinfo=zone)
            bounds = [value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z') for value in (start,end)]
        with self.store.connect() as db:
            parent = db.execute('''SELECT a.name,p.id,p.name AS student_name,p.student_id,p.class_name FROM parent_access a
                JOIN people p ON p.id=a.person_id WHERE a.id=? AND a.revoked_at IS NULL''', (parent_id,)).fetchone()
            if parent is None:
                raise FaceError('parent_missing', 'This parent access is no longer available.')
            records = [dict(row) for row in db.execute('''SELECT s.name AS session_name,s.started_at,s.ended_at,s.status AS session_status,
                r.id AS record_id,r.status,r.first_seen_at,r.last_seen_at,r.method,r.checked_in_at,r.checked_out_at,r.campus_state FROM attendance_records r
                JOIN attendance_sessions s ON s.id=r.session_id WHERE r.person_id=? ORDER BY s.id DESC LIMIT 100''', (parent['id'],))]
            calendar = [dict(row) for row in db.execute("""SELECT s.name AS session_name,s.started_at,s.ended_at,s.status AS session_status,
                r.id AS record_id,r.status,r.first_seen_at,r.last_seen_at,r.method,r.checked_in_at,r.checked_out_at,r.campus_state FROM attendance_records r
                JOIN attendance_sessions s ON s.id=r.session_id WHERE r.person_id=? AND s.started_at>=? AND s.started_at<?
                ORDER BY s.started_at,s.id""", (parent['id'],*bounds))] if month is not None else None
            selected_records=records+(calendar or [])
            ids=list({record['record_id'] for record in selected_records})
            movement_rows=[]
            for offset in range(0,len(ids),400):
                chunk=ids[offset:offset+400];placeholders=','.join('?' for _ in chunk)
                movement_rows.extend(dict(row) for row in db.execute(f"""SELECT m.id,m.record_id,m.action,m.source,m.occurred_at FROM attendance_movements m JOIN attendance_records r ON r.id=m.record_id
                    WHERE r.person_id=? AND m.record_id IN ({placeholders}) ORDER BY m.id""",(parent['id'],*chunk)))
            movements={record_id:[] for record_id in ids}
            for movement in movement_rows:
                if movement['record_id'] in movements:movements[movement['record_id']].append(movement)
            for record in selected_records:record['movements']=movements.get(record['record_id'],[])
            active = db.execute("SELECT id FROM attendance_sessions WHERE status='active'").fetchone()
            active_record = db.execute('''SELECT s.name AS session_name,s.started_at,s.ended_at,s.status AS session_status,
                r.id AS record_id,r.status,r.first_seen_at,r.last_seen_at,r.method,r.checked_in_at,r.checked_out_at,r.campus_state FROM attendance_records r
                JOIN attendance_sessions s ON s.id=r.session_id WHERE r.person_id=? AND s.status='active' ''', (parent['id'],)).fetchone()
        current=dict(active_record) if active_record else None
        if current:current['movements']=movements.get(current['record_id'],[])
        result = {'parent_name': parent['name'], 'student_name': parent['student_name'],'student_id':parent['student_id'],'class_name':parent['class_name'], 'current': current,
                'active_session': bool(active), 'history': records, 'timezone':attendance_timezone}
        if month is not None: result.update(calendar=calendar,calendar_month=month)
        return result
