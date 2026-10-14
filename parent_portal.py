"""Scoped parent attendance views and local staff/parent access sessions."""
import hashlib
import json
import sqlite3

from werkzeug.security import generate_password_hash, check_password_hash
import os
import secrets
import time
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from biometrics import FaceError
from iphone import valid_name

DUMMY_PASSWORD_HASH=generate_password_hash(secrets.token_urlsafe(32))


class PortalAccess:
    SESSION_SECONDS = 12 * 60 * 60

    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        self.failures = {}
        self.staff_login_path = store.path.parent / 'staff-login.json'
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

            columns={row['name'] for row in db.execute('PRAGMA table_info(parent_access)')}
            migrating='login_id' not in columns
            if migrating:
                db.execute("ALTER TABLE parent_access ADD COLUMN login_id TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE parent_access ADD COLUMN password_hash TEXT")
                db.execute("UPDATE parent_access SET login_id='parent-' || id")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS parent_login_id ON parent_access(login_id COLLATE NOCASE)")
            db.execute("""CREATE TABLE IF NOT EXISTS staff_credentials (
                id INTEGER PRIMARY KEY CHECK(id=1),login_id TEXT NOT NULL,password_hash TEXT NOT NULL)""")
            if not db.execute('SELECT 1 FROM staff_credentials').fetchone():
                try:
                    descriptor=os.open(self.staff_login_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                except FileExistsError:
                    credentials=json.loads(self.staff_login_path.read_text())
                else:
                    credentials={'login_id':'admin','password':secrets.token_urlsafe(18)}
                    with os.fdopen(descriptor,'w') as file:json.dump(credentials,file,indent=2)
                self.staff_login_path.chmod(0o600)
                db.execute('INSERT INTO staff_credentials VALUES(1,?,?)',
                    (self.valid_login_id(credentials['login_id']),generate_password_hash(self.valid_password(credentials['password']))))
                migrating=True
            if migrating:db.execute('DELETE FROM portal_sessions')

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def valid_login_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{2,63}',value.strip()):
            raise FaceError('invalid_login_id','Use a login ID of 3–64 letters, numbers, dots, underscores, @ or hyphens.')
        return value.strip().lower()

    @staticmethod
    def valid_password(value):
        if not isinstance(value,str) or not 10<=len(value)<=128:
            raise FaceError('invalid_password','Use a password of 10–128 characters.')
        return value

    def login(self, role, login_id, password, remote):
        now=self.clock();key=(role,remote)
        with self.store.lock:
            self.failures={k:[t for t in times if now-t<60] for k,times in self.failures.items() if any(now-t<60 for t in times)}
            if len(self.failures.get(key,[]))>=10:
                raise FaceError('login_limited','Too many attempts. Wait one minute before trying again.')
            candidate=login_id.strip().lower() if isinstance(login_id,str) and len(login_id)<=64 else ''
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM staff_credentials WHERE login_id=? COLLATE NOCASE',(candidate,)).fetchone() if role=='staff' else db.execute(
                    'SELECT * FROM parent_access WHERE login_id=? COLLATE NOCASE AND revoked_at IS NULL',(candidate,)).fetchone()
            encoded=row['password_hash'] if row and row['password_hash'] else DUMMY_PASSWORD_HASH
            valid=check_password_hash(encoded,password if isinstance(password,str) and len(password)<=128 else '')
            if not row or not row['password_hash'] or not valid:
                self.failures.setdefault(key,[]).append(now)
                raise FaceError('invalid_access','Login ID or password is incorrect.')
            self.failures.pop(key,None);session_token=secrets.token_urlsafe(32)
            with self.store.connect() as db:
                db.execute('DELETE FROM portal_sessions WHERE expires_at<=?',(now,))
                db.execute('INSERT INTO portal_sessions VALUES(?,?,?,?)',(self.digest(session_token),role,row['id'] if role=='parent' else None,now+self.SESSION_SECONDS))
            return session_token

    def staff_account(self):
        with self.store.connect() as db:return {'login_id':db.execute('SELECT login_id FROM staff_credentials WHERE id=1').fetchone()[0]}

    def update_staff_credentials(self, current_password, login_id, password):
        login_id=self.valid_login_id(login_id);password=self.valid_password(password)
        with self.store.lock,self.store.connect() as db:
            row=db.execute('SELECT password_hash FROM staff_credentials WHERE id=1').fetchone()
            if not isinstance(current_password,str) or len(current_password)>128 or not check_password_hash(row[0],current_password):
                raise FaceError('invalid_access','Current password is incorrect.')
            db.execute('UPDATE staff_credentials SET login_id=?,password_hash=? WHERE id=1',(login_id,generate_password_hash(password)))
            db.execute("DELETE FROM portal_sessions WHERE role='staff'")
        self.staff_login_path.unlink(missing_ok=True)
        return {'login_id':login_id}

    def reset_parent_credentials(self, parent_id, login_id, password):
        login_id=self.valid_login_id(login_id);password=self.valid_password(password)
        with self.store.lock,self.store.connect() as db:
            row=db.execute('SELECT id FROM parent_access WHERE id=? AND revoked_at IS NULL',(parent_id,)).fetchone()
            if row is None:raise FaceError('parent_missing','That parent account was not found.')
            try:db.execute('UPDATE parent_access SET login_id=?,password_hash=? WHERE id=?',(login_id,generate_password_hash(password),parent_id))
            except sqlite3.IntegrityError as error:raise FaceError('duplicate_login_id','That parent login ID is already assigned.') from error
            db.execute('DELETE FROM portal_sessions WHERE parent_id=?',(parent_id,))
        return {'login_id':login_id}

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
            return [dict(row) for row in db.execute('''SELECT a.id,a.name,a.person_id,p.name AS student_name,a.created_at,a.login_id,(a.password_hash IS NULL) AS needs_password
                FROM parent_access a JOIN people p ON p.id=a.person_id WHERE a.revoked_at IS NULL ORDER BY a.id DESC''')]

    def create_link(self, name, person_id, login_id, password):
        name = valid_name(name)
        if type(person_id) is not int or person_id < 1:
            raise FaceError('invalid_person', 'Choose an enrolled student.')
        login_id=self.valid_login_id(login_id);password=self.valid_password(password)
        with self.store.lock, self.store.connect() as db:
            person = db.execute("SELECT name FROM people WHERE id=? AND person_type='student'", (person_id,)).fetchone()
            if person is None:
                raise FaceError('person_missing', 'That student is no longer enrolled.')
            try:parent_id=db.execute('INSERT INTO parent_access(name,person_id,code_hash,login_id,password_hash) VALUES(?,?,?,?,?)',
                (name,person_id,self.digest(secrets.token_urlsafe(32)),login_id,generate_password_hash(password))).lastrowid
            except sqlite3.IntegrityError as error:raise FaceError('duplicate_login_id','That parent login ID is already assigned.') from error
        return {'id': parent_id, 'name': name, 'student_name': person['name'], 'person_id': person_id, 'login_id':login_id}

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
