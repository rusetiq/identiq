"""Portable SQLite backup and merge for the authenticated school admin UI."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import uuid

from biometrics import FaceError


REQUIRED_TABLES = {
    'people', 'embeddings', 'captures', 'attendance_sessions',
    'attendance_records', 'attendance_movements', 'parent_access',
}
PROFILE_FIELDS = ('name', 'person_type', 'student_id', 'class_name', 'grade', 'section',
                  'academic_year', 'employee_code')
RECORD_FIELDS = ('name', 'status', 'first_seen_at', 'last_seen_at', 'method', 'student_id',
                 'class_name', 'person_type', 'grade', 'section', 'academic_year',
                 'employee_code', 'checked_in_at', 'checked_out_at', 'campus_state')


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def ensure_transfer_tables(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS identiq_transfer_metadata (
            id INTEGER PRIMARY KEY CHECK(id=1), source_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_sources (
            source_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_people (
            source_id TEXT NOT NULL, source_person_id INTEGER NOT NULL,
            target_person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            PRIMARY KEY(source_id, source_person_id)
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_sessions (
            source_id TEXT NOT NULL, source_session_id INTEGER NOT NULL,
            target_session_id INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
            PRIMARY KEY(source_id, source_session_id)
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_records (
            source_id TEXT NOT NULL, source_record_id INTEGER NOT NULL,
            target_record_id INTEGER NOT NULL REFERENCES attendance_records(id) ON DELETE CASCADE,
            PRIMARY KEY(source_id, source_record_id)
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_movements (
            source_id TEXT NOT NULL, source_movement_id INTEGER NOT NULL,
            PRIMARY KEY(source_id, source_movement_id)
        );
        CREATE TABLE IF NOT EXISTS identiq_transfer_parents (
            source_id TEXT NOT NULL, source_parent_id INTEGER NOT NULL,
            target_parent_id INTEGER NOT NULL REFERENCES parent_access(id) ON DELETE CASCADE,
            PRIMARY KEY(source_id, source_parent_id)
        );
    ''')


def create_backup(store, destination: Path):
    """Create a consistent SQLite snapshot and stamp it with a stable origin id."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with store.lock:
        with store.connect() as source:
            ensure_transfer_tables(source)
            row = source.execute('SELECT source_id FROM identiq_transfer_metadata WHERE id=1').fetchone()
            source_id = row[0] if row else str(uuid.uuid4())
            source.execute('INSERT OR IGNORE INTO identiq_transfer_metadata(id,source_id) VALUES(1,?)',
                           (source_id,))
            source.commit()
            destination_db = sqlite3.connect(destination)
            try:
                source.backup(destination_db)
            finally:
                destination_db.close()
    destination.chmod(0o600)
    return destination


def _validate_backup(path: Path):
    try:
        db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        check = db.execute('PRAGMA quick_check').fetchone()
        if not check or check[0] != 'ok':
            raise ValueError('The database file failed its SQLite integrity check.')
        tables = _tables(db)
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError('This file is not a complete Identiq database backup.')
        metadata = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='identiq_transfer_metadata'").fetchone()
        if not metadata or 'source_id' not in _columns(db, 'identiq_transfer_metadata'):
            raise ValueError('Download a fresh backup from the Identiq app before importing it.')
        source_row = db.execute('SELECT source_id FROM identiq_transfer_metadata WHERE id=1').fetchone()
        if not source_row or not isinstance(source_row[0], str) or len(source_row[0]) > 80:
            raise ValueError('The backup is missing its transfer identity.')
        db.execute('PRAGMA foreign_key_check')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('The backup contains broken record links.')
        return db, source_row[0]
    except (sqlite3.Error, OSError) as error:
        raise ValueError('Could not read this SQLite backup file.') from error


def _matching_person(target, person):
    person_type = person.get('person_type') or 'student'
    if person_type == 'staff' and person.get('employee_code'):
        return target.execute("SELECT id FROM people WHERE person_type='staff' AND employee_code=? COLLATE NOCASE",
                              (person['employee_code'],)).fetchone()
    if person_type != 'staff' and person.get('student_id'):
        return target.execute("SELECT id FROM people WHERE person_type!='staff' AND student_id=? COLLATE NOCASE",
                              (person['student_id'],)).fetchone()
    return None


def _insert_fields(db, table, values):
    present = _columns(db, table)
    fields = [field for field in values if field in present]
    marks = ','.join('?' for _ in fields)
    return db.execute(f'INSERT INTO "{table}" ({",".join(fields)}) VALUES ({marks})',
                      [values[field] for field in fields]).lastrowid


def merge_backup(store, path: Path):
    """Merge a validated snapshot, preserving target credentials and being repeat-safe."""
    source, source_id = _validate_backup(path)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    counts = {'people_added': 0, 'people_matched': 0, 'face_samples_added': 0,
              'sessions_added': 0, 'attendance_records_added': 0,
              'parent_accounts_added': 0, 'parent_accounts_skipped': 0, 'already_imported': False}
    try:
        with store.lock, store.connect() as target:
            ensure_transfer_tables(target)
            previously_imported = target.execute(
                'SELECT 1 FROM identiq_transfer_sources WHERE source_id=?', (source_id,)).fetchone() is not None

            people_map = {}
            for raw in source.execute('SELECT * FROM people ORDER BY id'):
                person = dict(raw)
                prior = target.execute('SELECT target_person_id FROM identiq_transfer_people WHERE source_id=? AND source_person_id=?',
                                       (source_id, person['id'])).fetchone()
                match = prior or _matching_person(target, person)
                if match:
                    target_id = int(match[0])
                    counts['people_matched'] += 1
                else:
                    profile = {field: person.get(field, '') for field in PROFILE_FIELDS}
                    profile['person_type'] = profile.get('person_type') or 'student'
                    profile['name'] = profile.get('name') or 'Imported person'
                    target_id = _insert_fields(target, 'people', profile)
                    counts['people_added'] += 1
                people_map[person['id']] = target_id
                target.execute('INSERT OR REPLACE INTO identiq_transfer_people VALUES(?,?,?)',
                               (source_id, person['id'], target_id))
                for embedding in source.execute('SELECT vector,source FROM embeddings WHERE person_id=?', (person['id'],)):
                    if not target.execute('SELECT 1 FROM embeddings WHERE person_id=? AND vector=? AND source=?',
                                          (target_id, embedding['vector'], embedding['source'])).fetchone():
                        target.execute('INSERT INTO embeddings(person_id,vector,source) VALUES(?,?,?)',
                                       (target_id, embedding['vector'], embedding['source']))
                        counts['face_samples_added'] += 1
                for capture in source.execute('SELECT source,metadata FROM captures WHERE person_id=?', (person['id'],)):
                    target.execute('INSERT INTO captures(person_id,source,metadata) SELECT ?,?,? WHERE NOT EXISTS '
                                   '(SELECT 1 FROM captures WHERE person_id=? AND source=? AND metadata=?)',
                                   (target_id, capture['source'], capture['metadata'], target_id, capture['source'], capture['metadata']))

            session_map = {}
            for raw in source.execute('SELECT * FROM attendance_sessions ORDER BY id'):
                session = dict(raw)
                mapped = target.execute('SELECT target_session_id FROM identiq_transfer_sessions WHERE source_id=? AND source_session_id=?',
                                        (source_id, session['id'])).fetchone()
                if mapped:
                    session_map[session['id']] = mapped[0]
                    continue
                status = 'closed' if session.get('status') == 'active' else session.get('status', 'closed')
                session_id = _insert_fields(target, 'attendance_sessions', {
                    'name': session.get('name', 'Imported session'), 'started_at': session.get('started_at'),
                    'ended_at': session.get('ended_at') or (now if status == 'closed' else None),
                    'status': status, 'class_name': session.get('class_name', '')})
                session_map[session['id']] = session_id
                target.execute('INSERT INTO identiq_transfer_sessions VALUES(?,?,?)', (source_id, session['id'], session_id))
                counts['sessions_added'] += 1

            record_map = {}
            for raw in source.execute('SELECT * FROM attendance_records ORDER BY id'):
                record = dict(raw)
                mapped = target.execute('SELECT target_record_id FROM identiq_transfer_records WHERE source_id=? AND source_record_id=?',
                                        (source_id, record['id'])).fetchone()
                if mapped:
                    record_map[record['id']] = mapped[0]
                    continue
                record_values = {field: record.get(field) for field in RECORD_FIELDS if field in _columns(source, 'attendance_records')}
                record_values.update(session_id=session_map[record['session_id']],
                                     person_id=people_map.get(record.get('person_id')))
                record_id = _insert_fields(target, 'attendance_records', record_values)
                record_map[record['id']] = record_id
                target.execute('INSERT INTO identiq_transfer_records VALUES(?,?,?)', (source_id, record['id'], record_id))
                counts['attendance_records_added'] += 1

            for raw in source.execute('SELECT * FROM attendance_movements ORDER BY id'):
                movement = dict(raw)
                if target.execute('SELECT 1 FROM identiq_transfer_movements WHERE source_id=? AND source_movement_id=?',
                                  (source_id, movement['id'])).fetchone():
                    continue
                record_id = record_map.get(movement['record_id'])
                if record_id:
                    _insert_fields(target, 'attendance_movements', {
                        'record_id': record_id, 'action': movement['action'], 'source': movement['source'],
                        'occurred_at': movement['occurred_at'], 'note': movement.get('note', '')})
                    target.execute('INSERT INTO identiq_transfer_movements VALUES(?,?)', (source_id, movement['id']))

            for raw in source.execute('SELECT * FROM parent_access ORDER BY id'):
                parent = dict(raw)
                if target.execute('SELECT 1 FROM identiq_transfer_parents WHERE source_id=? AND source_parent_id=?',
                                  (source_id, parent['id'])).fetchone():
                    continue
                if not parent.get('person_id') or parent['person_id'] not in people_map or not parent.get('login_id'):
                    counts['parent_accounts_skipped'] += 1
                    continue
                if target.execute('SELECT 1 FROM parent_access WHERE login_id=? COLLATE NOCASE', (parent['login_id'],)).fetchone():
                    counts['parent_accounts_skipped'] += 1
                    continue
                parent_id = _insert_fields(target, 'parent_access', {
                    'name': parent['name'], 'person_id': people_map[parent['person_id']],
                    'code_hash': parent['code_hash'], 'created_at': parent.get('created_at'),
                    'revoked_at': parent.get('revoked_at'), 'login_id': parent['login_id'],
                    'password_hash': parent.get('password_hash')})
                target.execute('INSERT INTO identiq_transfer_parents VALUES(?,?,?)', (source_id, parent['id'], parent_id))
                counts['parent_accounts_added'] += 1

            target.execute('INSERT OR IGNORE INTO identiq_transfer_sources(source_id,imported_at) VALUES(?,?)', (source_id, now))
            counts['already_imported'] = previously_imported and not any((
                counts['people_added'], counts['face_samples_added'], counts['sessions_added'],
                counts['attendance_records_added'], counts['parent_accounts_added']))
        return counts
    except sqlite3.Error as error:
        raise ValueError('The backup could not be merged. The server database was left unchanged.') from error
    finally:
        source.close()


