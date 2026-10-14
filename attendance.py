"""Session attendance: immutable roster snapshots and server-confirmed sightings."""
import csv
import io
import time
import uuid
import sqlite3
from contextlib import closing

from biometrics import FaceError
from iphone import valid_name


class Attendance:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.sightings = {}
        self.crossings = {}

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
            self.sightings.clear();self.crossings.clear()
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
        result['movement_counts']={state:sum(row['campus_state']==state for row in roster) for state in ('on_campus','checked_out','not_arrived','unknown')}
        result["roster"] = roster
        result["counts"] = {status: sum(row["status"] == status for row in roster) for status in ("present", "absent", "pending")}
        result["counts"]["total"] = len(roster)
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
            if source!='staff':db.execute("UPDATE attendance_records SET last_seen_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(row['id'],))
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
        return result

    def observe_group(self, session_id, scanner_id, recognitions, action='check_in', axis='x', direction='positive'):
        self.movement_options(action,axis,direction)
        scanner_id = self.scanner(scanner_id)
        with self.store.lock, self.store.connect() as db:
            self.ensure_active(session_id, db)
            now = self.clock()
            present_ids = {item['person']['id'] for item in recognitions if item['status'] == 'matched'}
            self.crossings={key:value for key,value in self.crossings.items() if key[:2]!=(session_id,scanner_id) or key[2] in present_ids}
            # Observation state belongs to an identity, never a face-array position.
            # Drop missing identities from the short-lived duplicate-request cache.
            self.sightings = {key: value for key, value in self.sightings.items()
                             if now-value['time'] < 30 and not (len(key) == 3 and key[:2] == (session_id, scanner_id) and key[2] not in present_ids)}
            return [self.observe(session_id, scanner_id, item, group=True, _db=db, _now=now, _checked=True, action=action, axis=axis, direction=direction) if item['status'] == 'matched'
                    else {'status':'unrecognized','confirmations':0,'message':'No attendance marked.'} for item in recognitions]

    def observe(self, session_id, scanner_id, recognition, group=False, _db=None, _now=None, _checked=False, action='check_in', axis='x', direction='positive'):
        self.movement_options(action,axis,direction)
        if _db is None:
            with self.store.lock, self.store.connect() as db:
                return self.observe(session_id, scanner_id, recognition, group, db, action=action,axis=axis,direction=direction)
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
                if action!='automatic' and previous.get("action")==action and now-previous["time"] < 0.25:
                    status = 'manual_override' if row['method']=='manual' and row['status']!='present' else 'checked_out' if row['campus_state']=='checked_out' else 'present' if row['status']=='present' else 'confirming'
                    return {"status":status,"confirmations":previous['count'],"record_id":row['id'],"newly_present":False,
                            "campus_state":row['campus_state'],"attendance_status":row['status'],"message":"Manual attendance status kept."}
            if len(self.sightings) >= 256 and key not in self.sightings:
                self.sightings.pop(next(iter(self.sightings)))
            self.sightings[key] = {"person": person_id, "count": count, "time": now, "action":action}
            if row["method"] == "manual" and row["status"] != "present":
                return {"status": "manual_override", "confirmations": count, "record_id": row["id"],
                        "attendance_status":row["status"], "message": "Manual attendance status kept."}
            movement=action
            if action=='automatic':
                movement=self.crossing(session_id,scanner_id,recognition,axis,direction,now)
                if movement is None:return {'status':'tracking','confirmations':1,'newly_present':False,'newly_moved':False,'campus_state':row['campus_state'],'message':'Waiting for a clear crossing of the line.'}
            result=self.apply_movement(_db,row,movement,'automatic' if action=='automatic' else 'camera')
            return {**result,'confirmations':1,'record_id':row['id']}

    def finish(self, session_id):
        with self.store.lock, self.store.connect() as db:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise FaceError("session_missing", "That attendance session was not found.")
            if session["status"] == "active":
                db.execute("UPDATE attendance_records SET status='absent',method='session_end' WHERE session_id=? AND status='pending'", (session_id,))
                db.execute("UPDATE attendance_sessions SET status='closed',ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (session_id,))
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
