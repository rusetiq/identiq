"""Session attendance: immutable roster snapshots and server-confirmed sightings."""
import csv
import io
import time
import uuid
import sqlite3
import math
from datetime import datetime, timezone
from contextlib import closing

from biometrics import FaceError
from iphone import valid_name
from attendance_policy import (MATCH_THRESHOLD, MATCH_MARGIN, CONFIRMATIONS,
    MIN_INTERVAL, MIN_DURATION, MAX_GAP, MAX_FRAME_AGE, MAX_BUFFER_AGE, LAST_SEEN_INTERVAL, quality_issue)


class Attendance:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.sightings = {}
        self.crossings = {}
        self.frames = {}

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
                if 'attendance_movements' in tables: db.execute('DELETE FROM attendance_movements')
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
                with closing(sqlite3.connect(path,timeout=10)) as db: purge(db)
                backups+=1
            with self.store.connect() as db: counts=purge(db)
            self.sightings.clear();self.crossings.clear();self.frames.clear()
        return {'sessions_deleted':counts['attendance_sessions'],'records_deleted':counts['attendance_records'],'backups_cleared':backups}

    def detail(self, session_id):
        with self.store.connect() as db:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            roster = [dict(row) for row in db.execute(
                "SELECT * FROM attendance_records WHERE session_id=? ORDER BY name COLLATE NOCASE, id", (session_id,))]
            events=[dict(row) for row in db.execute("""SELECT m.*,r.name FROM attendance_movements m JOIN attendance_records r ON r.id=m.record_id
                WHERE r.session_id=? ORDER BY m.id DESC LIMIT 200""",(session_id,))]
        result = dict(session)
        result['movements']=events
        # Aggregate movement_counts and counts in a single pass over roster
        # instead of iterating over roster 7 separate times.
        m_counts = {'on_campus': 0, 'checked_out': 0, 'not_arrived': 0, 'unknown': 0}
        counts = {'present': 0, 'absent': 0, 'pending': 0}
        for row in roster:
            state = row['campus_state']
            if state in m_counts:
                m_counts[state] += 1
            status = row['status']
            if status in counts:
                counts[status] += 1
        counts["total"] = len(roster)
        result['movement_counts'] = m_counts
        result["roster"] = roster
        result["counts"] = counts
        return result

    def start(self, name, class_name='', person_type='',academic_year=''):
        name = valid_name(name)
        if not isinstance(class_name,str) or len(class_name)>80:raise FaceError('invalid_class','Choose a valid class.')
        class_name=class_name.strip()
        if person_type not in ('','student','staff') or not isinstance(academic_year,str) or len(academic_year)>80:
            raise FaceError('invalid_roster','Choose a valid school roster.')
        with self.store.lock, self.store.connect() as db:
            if db.execute("SELECT 1 FROM attendance_sessions WHERE status='active'").fetchone():
                raise FaceError("session_active", "Finish the current session before starting another.")
            people = db.execute("""SELECT p.* FROM people p WHERE (?='' OR p.class_name=?)
                AND (?='' OR p.person_type=?) AND (?='' OR p.academic_year=?) ORDER BY p.id""",
                (class_name,class_name,person_type,person_type,academic_year,academic_year)).fetchall()
            if not people:
                raise FaceError("empty_roster", "Add at least one school record matching this roster first.")
            session_id = db.execute("INSERT INTO attendance_sessions(name,class_name) VALUES(?,?)", (name,class_name)).lastrowid
            fields=('name','student_id','class_name','person_type','grade','section','academic_year','employee_code')
            db.executemany('INSERT INTO attendance_records(session_id,person_id,'+','.join(fields)+') VALUES('+','.join('?' for _ in range(10))+')',
                           [(session_id,person['id'],*[person[field] for field in fields]) for person in people])
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
            self.crossings = {key:value for key,value in self.crossings.items() if key[:2] != (session_id,scanner_id)}

    def movement_options(self, action='check_in', axis='x', direction='positive'):
        if action not in ('check_in','check_out','automatic') or axis not in ('x','y') or direction not in ('positive','negative'):
            raise FaceError('invalid_movement','Choose a valid scan mode and crossing direction.')
        return action,axis,direction

    def crossing(self, session_id, scanner_id, recognition, axis, direction, now):
        person=recognition['person']['id'];key=(session_id,scanner_id,person,axis,direction)
        box=recognition.get('box')
        if not box or len(box)!=4:return None
        coordinate=1-(box[0]+box[2]/2) if axis=='x' else box[1]+box[3]/2
        side=-1 if coordinate<.42 else 1 if coordinate>.58 else 0
        previous=self.crossings.get(key)
        self.crossings={k:v for k,v in self.crossings.items() if now-v['seen']<8}
        if previous and now-previous['seen']>1.5:previous=None
        if side and previous and side!=previous['side'] and now-previous['time']>=.15:
            moving_positive=side>previous['side']
            self.crossings[key]={'side':side,'time':now,'seen':now}
            return 'check_in' if moving_positive==(direction=='positive') else 'check_out'
        if side and previous is None:self.crossings[key]={'side':side,'time':now,'seen':now}
        elif previous:self.crossings[key]={**previous,'seen':now}
        if len(self.crossings)>256:self.crossings.pop(next(iter(self.crossings)))
        return None

    def apply_movement(self, db, row, action, source, note=''):
        state=row['campus_state']
        if action=='check_out' and state!='on_campus':
            return {'status':'checked_out' if state=='checked_out' else 'not_checked_in','newly_present':False,'newly_moved':False,'campus_state':state,
                    'message':'Already checked out.' if state=='checked_out' else 'Check-in is required before check-out.'}
        if action=='check_in' and state=='on_campus':
            if source!='staff':
                try:
                    stamp = datetime.fromisoformat(row['last_seen_at'].replace('Z','+00:00'))
                    age = (datetime.now(timezone.utc) - stamp).total_seconds()
                except (ValueError, TypeError, AttributeError):
                    age = LAST_SEEN_INTERVAL
                if not 0 <= age < LAST_SEEN_INTERVAL:
                    db.execute("UPDATE attendance_records SET last_seen_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(row['id'],))
            return {'status':'present','newly_present':False,'newly_moved':False,'campus_state':state,'message':'Already checked in.'}
        db.execute('INSERT INTO attendance_movements(record_id,action,source,note) VALUES(?,?,?,?)',(row['id'],action,source,note))
        stamp=db.execute('SELECT occurred_at FROM attendance_movements WHERE id=last_insert_rowid()').fetchone()[0]
        if action=='check_in':
            db.execute("""UPDATE attendance_records SET status='present',method=CASE WHEN method='manual' THEN method ELSE ? END,
                campus_state='on_campus',checked_in_at=?,checked_out_at=NULL,
                first_seen_at=CASE WHEN ?='staff' THEN first_seen_at ELSE coalesce(first_seen_at,?) END,
                last_seen_at=CASE WHEN ?='staff' THEN last_seen_at ELSE ? END WHERE id=?""",('manual' if source=='staff' else 'camera',stamp,source,stamp,source,stamp,row['id']))
        else:db.execute("UPDATE attendance_records SET campus_state='checked_out',checked_out_at=?,last_seen_at=CASE WHEN ?='staff' THEN last_seen_at ELSE ? END WHERE id=?",(stamp,source,stamp,row['id']))
        return {'status':'present' if action=='check_in' else 'checked_out','newly_present':action=='check_in' and row['status']!='present',
                'newly_moved':True,'action':action,'campus_state':'on_campus' if action=='check_in' else 'checked_out','occurred_at':stamp}

    def manual_movement(self, session_id, record_id, action, note):
        if action not in ('check_in','check_out') or not isinstance(note,str) or not note.strip() or len(note)>160:
            raise FaceError('invalid_movement','Choose check-in or check-out and enter a staff note (up to 160 characters).')
        with self.store.lock,self.store.connect() as db:
            self.ensure_active(session_id,db)
            row=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND id=?',(session_id,record_id)).fetchone()
            if row is None:raise FaceError('record_missing','That student is not in this session.')
            if action=='check_out' and row['campus_state']!='on_campus':raise FaceError('not_checked_in','Only checked-in students can check out.')
            result=self.apply_movement(db,row,action,'staff',note.strip())
            self.sightings = {key:value for key,value in self.sightings.items() if key[0] != session_id}
            self.crossings = {key:value for key,value in self.crossings.items() if key[0] != session_id}
        return result

    @staticmethod
    def outcome(status, count=0, message='No attendance marked.', **fields):
        return {'status':status, 'confirmations':count, 'required_confirmations':CONFIRMATIONS,
                'newly_present':False, 'newly_moved':False, 'message':message,
                'retry_after_ms':math.ceil(MIN_INTERVAL*1000), **fields}

    @staticmethod
    def confirmation_delay(evidence, now):
        delay = MIN_INTERVAL - (now-evidence['time'])
        if evidence['count'] >= CONFIRMATIONS-1:
            delay = max(delay, MIN_DURATION - (now-evidence['started']))
        # One millisecond avoids scheduling exactly on a floating-point boundary.
        return max(40, math.ceil(delay*1000)+1)

    @staticmethod
    def clear_evidence(recognition):
        """Fail closed when an inference does not contain usable match evidence."""
        def finite(value):
            return type(value) in (int, float) and math.isfinite(value)
        score = recognition.get('similarity')
        margin = recognition.get('match_margin')
        candidates = recognition.get('candidate_count')
        if not finite(score) or score < MATCH_THRESHOLD or score > 1.000001:
            return False
        if type(candidates) is not int or candidates < 1:
            return False
        if candidates > 1 and (not finite(margin) or margin < MATCH_MARGIN):
            return False
        if quality_issue(recognition.get('quality')):
            return False
        box = recognition.get('box')
        return (isinstance(box, (list, tuple)) and len(box) == 4 and all(finite(v) for v in box)
                and box[2] > 0 and box[3] > 0 and box[0] >= 0 and box[1] >= 0
                and box[0] + box[2] <= 1.01 and box[1] + box[3] <= 1.01)

    @staticmethod
    def continuous_box(previous, box):
        # A name reappearing on another face elsewhere must start confirmation again.
        old = previous['box']
        ratios = [box[i] / old[i] for i in (2, 3)]
        dx = abs(box[0] + box[2]/2 - old[0] - old[2]/2)
        dy = abs(box[1] + box[3]/2 - old[1] - old[3]/2)
        return (all(.5 <= ratio <= 2 for ratio in ratios)
                and dx <= 1.5 * max(box[2], old[2]) + 1e-8
                and dy <= 1.5 * max(box[3], old[3]) + 1e-8)

    def observe_group(self, session_id, scanner_id, recognitions, action='check_in', axis='x', direction='positive',
                      frame_id=None, received_at=None, capture_age_ms=None):
        self.movement_options(action,axis,direction)
        scanner_id = self.scanner(scanner_id)
        with self.store.lock, self.store.connect() as db:
            self.ensure_active(session_id, db)
            now = self.clock()
            received_at = now if received_at is None else received_at
            frame_key = (session_id, scanner_id)
            if type(received_at) not in (int, float) or not math.isfinite(received_at):
                self.reset(session_id, scanner_id)
                return [self.outcome('stale_frame', message='Invalid camera timing. No attendance saved.') for _ in recognitions]
            if capture_age_ms is not None and (type(capture_age_ms) not in (int,float) or
                    not math.isfinite(capture_age_ms) or not 0 <= capture_age_ms <= MAX_BUFFER_AGE*1000):
                self.reset(session_id, scanner_id)
                return [self.outcome('stale_frame', message='Camera frame is too old. No attendance saved.') for _ in recognitions]
            captured_at = received_at - capture_age_ms/1000 if capture_age_ms is not None else received_at
            # Older clients do not send capture age; retain their existing
            # processing-time confirmation behavior.
            evidence_time = captured_at if capture_age_ms is not None else now
            last = self.frames.get(frame_key)
            # Check before pruning identities: a late frame must not erase newer evidence.
            if last and (received_at < last['received_at'] or captured_at < last.get('captured_at', last['received_at'])):
                return [self.outcome('stale_frame', message='Discarded an out-of-order camera frame.') for _ in recognitions]
            self.frames = {key:value for key,value in self.frames.items() if now-value['time'] < 30}
            if len(self.frames) >= 256 and frame_key not in self.frames:
                self.frames.pop(next(iter(self.frames)))
            if (not isinstance(frame_id,str) or len(frame_id) != 64 or
                    not 0 <= now-received_at <= MAX_FRAME_AGE or
                    not 0 <= now-captured_at <= MAX_FRAME_AGE):
                self.reset(session_id, scanner_id)
                return [self.outcome('stale_frame', message='Waiting for a fresh camera frame. No attendance saved.') for _ in recognitions]
            self.frames[frame_key] = {'received_at':received_at, 'captured_at':captured_at, 'time':now}
            counts = {}
            for item in recognitions:
                if item['status'] == 'matched':
                    person_id = item['person']['id']
                    counts[person_id] = counts.get(person_id, 0) + 1
            present_ids = {person_id for person_id,count in counts.items() if count == 1}
            self.crossings = {key:value for key,value in self.crossings.items()
                              if key[:2] != frame_key or key[2] in present_ids}
            self.sightings = {key:value for key,value in self.sightings.items()
                              if evidence_time-value['time'] <= MAX_GAP and (key[:2] != frame_key or key[2] in present_ids)}
            ids = list(present_ids)
            roster = {row['person_id']:row for row in db.execute(
                'SELECT * FROM attendance_records WHERE session_id=? AND person_id IN (' + ','.join('?' for _ in ids) + ')',
                (session_id, *ids))} if ids else {}
            return [self._observe(db, session_id, scanner_id, item, evidence_time, action, axis, direction, frame_id, roster.get(item['person']['id']))
                    if item['status'] == 'matched' and counts[item['person']['id']] == 1
                    else self.outcome('unrecognized') for item in recognitions]

    def observe(self, session_id, scanner_id, recognition, action='check_in', axis='x', direction='positive',
                frame_id=None, received_at=None, capture_age_ms=None):
        return self.observe_group(session_id, scanner_id, [recognition], action, axis, direction,
                                  frame_id=frame_id, received_at=received_at, capture_age_ms=capture_age_ms)[0]

    def _observe(self, db, session_id, scanner_id, recognition, now, action, axis, direction, frame_id, row):
        person_id = recognition['person']['id']
        key = (session_id, scanner_id, person_id)
        crossing_key = (*key, axis, direction)
        if row is None:
            self.sightings.pop(key, None)
            self.crossings.pop(crossing_key, None)
            return self.outcome('outside_roster', message="Recognized, but not in this session's starting roster.")
        if row['method'] == 'manual' and row['status'] != 'present':
            self.sightings.pop(key, None)
            self.crossings.pop(crossing_key, None)
            return self.outcome('manual_override', record_id=row['id'], attendance_status=row['status'], message='Manual attendance status kept.')
        if not self.clear_evidence(recognition):
            self.sightings.pop(key, None)
            self.crossings.pop(crossing_key, None)
            return self.outcome('insufficient_evidence', message='Move closer and hold still. A clearer, stronger match is required.')
        previous = self.sightings.get(key)
        options = (action, axis, direction)
        if previous and (now-previous['time'] > MAX_GAP or previous['options'] != options
                         or not self.continuous_box(previous, recognition['box'])):
            previous = None
        if previous is None:
            self.crossings.pop(crossing_key, None)
            evidence = {'count':0, 'started':now, 'time':now, 'frames':[], 'options':options,
                        'box':recognition['box'], 'min_similarity':recognition['similarity'], 'movement':None}
        else:
            evidence = previous
        # Retries and bursts are not independent observations. Do not update their
        # timestamps or crossing state, even after an identity was confirmed.
        if frame_id in evidence['frames'] or previous and now-previous['time'] < MIN_INTERVAL:
            if evidence.get('confirmed'):
                status = ('tracking' if action == 'automatic' else
                          'present' if action == 'check_in' and row['campus_state'] == 'on_campus' else
                          'checked_out' if action == 'check_out' and row['campus_state'] == 'checked_out' else 'confirming')
                return self.outcome(status, evidence['count'], campus_state=row['campus_state'],
                                    message='Identity already confirmed. No new movement saved.')
            return self.outcome('confirming', evidence['count'], message='Waiting for the next fresh confirmation frame.',
                                retry_after_ms=self.confirmation_delay(evidence,now))
        evidence = {**evidence, 'count':min(CONFIRMATIONS, evidence['count']+1), 'time':now,
                    'frames':[*evidence['frames'][-7:], frame_id], 'box':recognition['box'],
                    'min_similarity':min(evidence['min_similarity'],recognition['similarity'])}
        if action == 'automatic':
            crossing = self.crossing(session_id, scanner_id, recognition, axis, direction, now)
            if crossing:
                evidence['movement'] = crossing
        if len(self.sightings) >= 256 and key not in self.sightings:
            self.sightings.pop(next(iter(self.sightings)))
        self.sightings[key] = evidence
        if evidence['count'] < CONFIRMATIONS or now-evidence['started'] < MIN_DURATION:
            return self.outcome('confirming', evidence['count'], message='Confirming the same identity across fresh, clear camera frames.',
                                retry_after_ms=self.confirmation_delay(evidence,now))
        evidence['confirmed'] = True
        movement = evidence['movement'] if action == 'automatic' else action
        if movement is None:
            return self.outcome('tracking', evidence['count'], campus_state=row['campus_state'], message='Waiting for a clear crossing of the line.')
        evidence['movement'] = None
        note = f"Camera confirmed across {CONFIRMATIONS} fresh frames; minimum similarity {evidence['min_similarity']:.4f}."
        result = self.apply_movement(db, row, movement, 'automatic' if action == 'automatic' else 'camera', note)
        return {**self.outcome(result['status'], evidence['count']), **result, 'record_id':row['id']}

    def finish(self, session_id):
        with self.store.lock, self.store.connect() as db:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            if session["status"] == "active":
                db.execute("UPDATE attendance_records SET status='absent',method='session_end' WHERE session_id=? AND status='pending'", (session_id,))
                db.execute("UPDATE attendance_sessions SET status='closed',ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (session_id,))
            self.frames = {key:value for key,value in self.frames.items() if key[0] != session_id}
            self.sightings = {key: value for key, value in self.sightings.items() if key[0] != session_id}
            self.crossings = {key:value for key,value in self.crossings.items() if key[0] != session_id}
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
            old=db.execute('SELECT * FROM attendance_records WHERE session_id=? AND id=?',(session_id,record_id)).fetchone()
            if old is None:raise FaceError('record_missing','That student is not in this session.')
            if old['status']!=status:
                db.execute("INSERT INTO attendance_movements(record_id,action,source,note) VALUES(?,'correction','staff',?)",(record_id,f"Attendance: {old['status']} → {status}"))
            if status!='present':db.execute("UPDATE attendance_records SET campus_state='not_arrived',checked_in_at=NULL,checked_out_at=NULL WHERE id=?",(record_id,))
            elif old['campus_state']=='not_arrived':db.execute("UPDATE attendance_records SET campus_state='unknown' WHERE id=?",(record_id,))
            updated = db.execute("UPDATE attendance_records SET status=?,method=? WHERE session_id=? AND id=?",
                                 (status, None if status == "pending" else "manual", session_id, record_id)).rowcount
            if not updated:
                raise FaceError("record_missing", "That person is not in this attendance session.")
            self.frames = {key:value for key,value in self.frames.items() if key[0] != session_id}
            self.sightings = {key: value for key, value in self.sightings.items() if key[0] != session_id}
            self.crossings = {key:value for key,value in self.crossings.items() if key[0] != session_id}
        return self.detail(session_id)

    def export(self, session_id):
        session = self.detail(session_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["Session", "Started (UTC)", "Ended (UTC)", "Name", "Status", "First seen (UTC)", "Last seen (UTC)", "Source", "Check-in (UTC)", "Check-out (UTC)", "Campus status", "Admission number", "Class", "Record type", "Grade", "Section", "Academic year", "Employee code"])
        def safe(value):
            text = str(value or "")
            return "'"+text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for record in session["roster"]:
            writer.writerow([safe(value) for value in (session["name"], session["started_at"], session["ended_at"], record["name"],
                record["status"], record["first_seen_at"], record["last_seen_at"], record["method"],record["checked_in_at"],record["checked_out_at"],record["campus_state"],record["student_id"],record["class_name"],record["person_type"],record["grade"],record["section"],record["academic_year"],record["employee_code"])])
        return stream.getvalue()
