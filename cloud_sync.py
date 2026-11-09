"""Durable outbound cloud sync; camera inference never waits on cloud HTTP."""
import json
import os
import threading
import time
import uuid
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from biometrics import FaceError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class CloudSync:
    def __init__(self, store, attendance, changed):
        self.store, self.attendance, self.changed = store, attendance, changed
        config_path = store.path.parent / 'cloud-api.json'
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        self.url = os.environ.get('IDENTIQ_CLOUD_URL', config.get('url', '')).rstrip('/')
        self.admin_token = os.environ.get('IDENTIQ_CLOUD_ADMIN_TOKEN', config.get('admin_token', ''))
        self.key_path = store.path.parent / 'cloud-scanner-key.json'
        self.key = os.environ.get('IDENTIQ_CLOUD_SYNC_KEY', '')
        if not self.key and self.key_path.exists():
            saved = json.loads(self.key_path.read_text())
            if saved.get('url') == self.url:
                self.key = saved.get('key', '')
        self.last_error = None
        self.last_sync = None
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS cloud_outbox (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity TEXT NOT NULL, data TEXT NOT NULL,
                created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_commands (id TEXT PRIMARY KEY, result TEXT NOT NULL);
            ''')
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError('IDENTIQ_CLOUD_URL must be an HTTPS origin.')
            threading.Thread(target=self.loop, daemon=True, name='cloud-sync').start()

    def request(self, path, method='GET', data=None, admin=False):
        key = self.admin_token if admin else self.key
        if not self.url or not key:
            raise FaceError('cloud_not_configured', 'Configure the cloud URL and API credentials on this server first.')
        req = Request(self.url+path, method=method,
            headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json', 'User-Agent': 'Identiq-School-Server/1.0'},
            data=json.dumps(data).encode() if data is not None else None)
        with build_opener(NoRedirect()).open(req, timeout=8) as response:
            return json.load(response)

    def enqueue(self, kind, entity, data):
        if not self.url:
            return
        with self.store.lock, self.store.connect() as db:
            db.execute('INSERT INTO cloud_outbox VALUES(?,?,?,?,?)',
                       (str(uuid.uuid4()), kind, str(entity), json.dumps(data), time.time()))

    def snapshot(self):
        # Coalesce snapshots in the background, outside recognition requests.
        if not self.url:
            return
        with self.store.lock, self.store.connect() as db:
            if not db.execute("SELECT 1 FROM cloud_outbox WHERE kind='refresh'").fetchone():
                db.execute('INSERT INTO cloud_outbox VALUES(?,?,?,?,?)', (str(uuid.uuid4()), 'refresh', 'all', '{}', time.time()))

    def status(self):
        with self.store.connect() as db:
            count = db.execute('SELECT count(*) FROM cloud_outbox').fetchone()[0]
        return {'configured': bool(self.url), 'url': self.url, 'scanner_ready': bool(self.key),
                'pending': count, 'last_sync': self.last_sync, 'error': self.last_error}

    def create_key(self, data):
        scope = data.get('scope')
        if scope not in ('read', 'write', 'scanner'):
            raise FaceError('invalid_scope', 'Choose read, write or scanner access.')
        result = self.request('/admin/keys', 'POST', data, admin=True)
        if scope == 'scanner':
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.chmod(self.key_path, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump({'url': self.url, 'key': result['key']}, file)
            self.key = result['key']
            self.snapshot()
        return result

    def process_command(self, command):
        with self.store.lock, self.store.connect() as db:
            saved = db.execute('SELECT result FROM cloud_commands WHERE id=?', (command['id'],)).fetchone()
            if saved:
                return json.loads(saved[0])
            data = command['data']
            try:
                if command['kind'] == 'correction':
                    self.attendance.correct(data['session_id'], data['record_id'], data['status'])
                elif command['kind'] == 'movement':
                    note = data['note'][:70]+' [cloud:'+command['id']+']'
                    applied = db.execute('SELECT 1 FROM attendance_movements WHERE record_id=? AND note=?', (data['record_id'], note)).fetchone()
                    if not applied:
                        self.attendance.manual_movement(data['session_id'], data['record_id'], data['action'], note)
                elif command['kind'] == 'profile':
                    # A command retry after a process crash must find the same profile.
                    existing = db.execute('SELECT id FROM people WHERE '+
                        ('employee_code=?' if data.get('person_type') == 'staff' else 'student_id=?'),
                        (data.get('employee_code') if data.get('person_type') == 'staff' else data.get('admission_number'),)).fetchone()
                    person = self.store.save_profile(data, existing['id'] if existing else None)
                else:
                    raise FaceError('invalid_command', 'Unsupported cloud command.')
                result = {'status': 'applied', 'result': {'person_id': person['id']} if command['kind'] == 'profile' else {}}
            except FaceError as error:
                result = {'status': 'rejected', 'result': {'code': error.code, 'error': str(error)}}
            db.execute('INSERT INTO cloud_commands VALUES(?,?)', (command['id'], json.dumps(result)))
        self.changed()
        self.snapshot()
        return result

    def tick(self):
        if not self.key:
            return
        with self.store.connect() as db:
            refresh = db.execute("SELECT id FROM cloud_outbox WHERE kind='refresh'").fetchall()
        if refresh:
            with self.store.connect() as db:
                db.executemany('DELETE FROM cloud_outbox WHERE id=?', [(row['id'],) for row in refresh])
            for person in self.store.people():
                self.enqueue('people', person['id'], person)
            for session in self.attendance.sessions():
                self.enqueue('session', session['id'], self.attendance.detail(session['id']))
        with self.store.connect() as db:
            pending = db.execute('SELECT * FROM cloud_outbox ORDER BY created_at,id LIMIT 20').fetchall()
        for row in pending:
            self.request('/v1/sync', 'POST', {'id': row['id'], 'kind': row['kind'], 'entity': row['entity'], 'data': json.loads(row['data'])})
            with self.store.connect() as db:
                db.execute('DELETE FROM cloud_outbox WHERE id=?', (row['id'],))
        for command in self.request('/v1/commands')['commands']:
            result = self.process_command(command)
            self.request('/v1/commands/'+command['id'], 'POST', result)
        self.last_sync = time.time()

    def loop(self):
        while True:
            try:
                self.tick()
                self.last_error = None
            except Exception:
                # Never log request bodies, credentials, or student records.
                self.last_error = 'Cloud sync unavailable. Queued changes will retry.'
            time.sleep(10)
