import uuid
import sqlite3
import hashlib
import pytest
from attendance import Attendance
from biometrics import Store
from test_app import setup,image,enroll,vector
from test_attendance import start,recognize
from test_parent_portal import link,parent_client


def movement_fixture(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3');person=store.enroll('Student',[vector()]*5)
    attendance=Attendance(store);sid=attendance.start('School day')['id'];moment=[100.0];attendance.clock=lambda:moment[0]
    scanner=str(uuid.uuid4())
    def scan(screen_x,action='automatic',axis='x',direction='positive',status='matched'):
        box=[1-screen_x-.1,.2,.2,.2] if axis=='x' else [.2,screen_x-.1,.2,.2]
        evidence={'status':status,'person':person,'box':box,'similarity':1.,'match_margin':None,'candidate_count':1,
                  'quality':{'face_size_px':160,'sharpness':20,'detection_score':.99}}
        frame_id=hashlib.sha256(str(moment[0]).encode()).hexdigest()
        result=attendance.observe_group(sid,scanner,[evidence],action,axis,direction,frame_id=frame_id)[0]
        moment[0]+=.35;return result
    return attendance,sid,moment,scan


def test_direction_crossing_arrival_departure_reentry_and_duplicate_sightings(tmp_path):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    assert scan(.2)['status']=='confirming'
    assert scan(.5)['status']=='confirming'
    assert scan(.8)['newly_moved'] is True
    detail=attendance.detail(sid);assert detail['movement_counts']['on_campus']==1
    assert detail['roster'][0]['checked_in_at'] and not detail['roster'][0]['checked_out_at']
    assert scan(.8)['status']=='tracking'
    assert len(attendance.detail(sid)['movements'])==1
    scan(.5)
    assert scan(.2)['status']=='checked_out'
    assert attendance.detail(sid)['roster'][0]['status']=='present'
    scan(.5)
    assert scan(.8)['status']=='present'
    detail=attendance.detail(sid)
    assert [m['action'] for m in reversed(detail['movements'])]==['check_in','check_out','check_in']
    assert all(m['source']=='automatic' for m in detail['movements'])
    assert detail['movement_counts']['on_campus']==1 and not detail['roster'][0]['checked_out_at']


@pytest.mark.parametrize('axis,direction,start_point,end_point',[('x','negative',.8,.2),('y','positive',.2,.8),('y','negative',.8,.2)])
def test_configurable_entry_direction(tmp_path,axis,direction,start_point,end_point):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    scan(start_point,axis=axis,direction=direction)
    scan(.5,axis=axis,direction=direction)
    assert scan(end_point,axis=axis,direction=direction)['action']=='check_in'
    assert attendance.detail(sid)['counts']['present']==1


def test_jitter_gaps_uncertainty_and_missing_faces_do_not_make_movements(tmp_path):
    attendance,sid,moment,scan=movement_fixture(tmp_path)
    for x in [.46,.51,.54,.49]:assert scan(x)['status'] in ('confirming','tracking')
    assert attendance.detail(sid)['movements']==[]
    scan(.2);scan(.8,status='ambiguous');assert scan(.8)['status']=='confirming'
    assert attendance.detail(sid)['movements']==[]
    moment[0]+=2;assert scan(.2)['status']=='confirming'
    assert attendance.detail(sid)['movements']==[]


def test_checkout_requires_arrival_and_finishing_does_not_fabricate_exit(tmp_path):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    scan(.8,'check_out');scan(.8,'check_out')
    assert scan(.8,'check_out')['status']=='not_checked_in'
    assert attendance.detail(sid)['counts']['pending']==1
    scan(.2,'check_in');scan(.2,'check_in')
    assert scan(.2,'check_in')['status']=='present'
    saved=attendance.finish(sid)
    assert saved['roster'][0]['campus_state']=='on_campus'
    assert saved['roster'][0]['checked_out_at'] is None
    with pytest.raises(Exception) as error:scan(.8,'check_out')
    assert error.value.code=='session_closed'


def test_manual_movement_auth_notes_history_parent_scope_and_correction(setup,image):
    client,headers,_,_=setup
    student=enroll(client,headers,image).json['person'];sid=start(client,headers).json['session']['id']
    record=client.get(f'/api/attendance/sessions/{sid}').json['session']['roster'][0]['id']
    parent=parent_client(client,link(client,headers,student['id']).json['parent']['login_id'])
    path=f'/api/attendance/sessions/{sid}/records/{record}/movement'
    assert parent.post(path,json={'action':'check_in','note':'Reception'},headers=headers).status_code==401
    assert client.post(path,json={'action':'check_in','note':'Reception'}).status_code==403
    assert client.post(path,json={'action':'check_in','note':''},headers=headers).status_code==422
    assert client.post(path,json={'action':'check_out','note':'Exit'},headers=headers).status_code==422
    arrival=client.post(path,json={'action':'check_in','note':'Reception verified'},headers=headers)
    assert arrival.json['movement']['newly_moved']
    assert not client.post(path,json={'action':'check_in','note':'Repeat'},headers=headers).json['movement']['newly_moved']
    assert client.post(path,json={'action':'check_out','note':'Release procedure completed'},headers=headers).json['movement']['status']=='checked_out'
    data=parent.get('/api/parent/attendance').json
    assert data['current']['campus_state']=='checked_out'
    assert data['current']['checked_in_at'] and data['current']['checked_out_at']
    assert [m['action'] for m in data['current']['movements']]==['check_in','check_out']
    assert all('note' not in m for m in data['current']['movements'])
    csv=client.get(f'/api/attendance/sessions/{sid}/export.csv').text
    assert 'Check-in (UTC)' in csv and 'Check-out (UTC)' in csv and 'checked_out' in csv
    client.patch(f'/api/attendance/sessions/{sid}/records/{record}',json={'status':'absent'},headers=headers)
    detail=client.get(f'/api/attendance/sessions/{sid}').json['session']
    assert detail['roster'][0]['campus_state']=='not_arrived' and not detail['roster'][0]['checked_in_at']
    assert detail['movements'][0]['action']=='correction'
    assert all(m['note'] for m in detail['movements'])


def test_camera_mode_invalid_options_and_no_crossing_with_forged_manual_data(setup,image):
    client,headers,_,_=setup;enroll(client,headers,image);sid=start(client,headers).json['session']['id']
    for option in ['wrong',None,[]]:
        response=client.post('/api/recognize',json={'image':image,'attendance_session_id':sid,'scanner_id':str(uuid.uuid4()),'attendance_action':option},headers=headers)
        assert response.status_code==422
    data=client.post('/api/recognize',json={'image':image,'attendance_session_id':sid,'scanner_id':str(uuid.uuid4()),'attendance_action':'automatic','box':[.1,.1,.2,.2]},headers=headers).json
    assert data['attendance']['status']=='confirming'


def test_student_profiles_unique_ids_class_rosters_snapshots_and_auth(setup,image):
    client,headers,engine,_=setup
    first=enroll(client,headers,image).json['person']
    engine.vector=vector(1)
    second=enroll(client,headers,image,name='Second student').json['person']
    path=f"/api/people/{first['id']}/profile"
    assert client.patch(path,json={'student_id':'S-1','class_name':'7A'}).status_code==403
    assert client.patch(path,json={'student_id':' S-1 ','class_name':'7A'},headers=headers).json['person']['student_id']=='S-1'
    assert client.patch(f"/api/people/{second['id']}/profile",json={'student_id':'s-1','class_name':'7B'},headers=headers).status_code==422
    assert client.patch(path,json={'student_id':None,'class_name':'7A'},headers=headers).status_code==422
    parent=parent_client(client,link(client,headers,first['id']).json['parent']['login_id'])
    assert parent.patch(path,json={'student_id':'tamper','class_name':'7B'},headers=headers).status_code==401
    session=client.post('/api/attendance/sessions',json={'name':'7A day','class_name':'7A'},headers=headers).json['session']
    assert len(session['roster'])==1 and session['roster'][0]['student_id']=='S-1'
    assert session['class_name']=='7A'
    client.patch(path,json={'student_id':'S-2','class_name':'8A'},headers=headers)
    snapshot=client.get(f"/api/attendance/sessions/{session['id']}").json['session']['roster'][0]
    assert snapshot['student_id']=='S-1' and snapshot['class_name']=='7A'
    assert parent.get('/api/parent/attendance').json['class_name']=='8A'
    client.post(f"/api/attendance/sessions/{session['id']}/finish",json={},headers=headers)
    assert client.post('/api/attendance/sessions',json={'name':'Empty class','class_name':'Missing'},headers=headers).status_code==422


def test_clear_attendance_removes_movement_ledger_but_keeps_profiles(tmp_path):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    person=attendance.store.people()[0]
    attendance.store.update_student_profile(person['id'],'S-1','7A')
    for _ in range(3):scan(.2,'check_in')
    for _ in range(3):scan(.8,'check_out')
    attendance.clear_all()
    with attendance.store.connect() as db:
        assert db.execute('SELECT count(*) FROM attendance_movements').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM attendance_records').fetchone()[0]==0
    assert attendance.store.people()[0]['student_id']=='S-1'


def test_legacy_present_migration_does_not_invent_arrival(tmp_path):
    path=tmp_path/'private'/'legacy.sqlite3';path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE attendance_records(id INTEGER PRIMARY KEY,session_id INTEGER,person_id INTEGER,name TEXT,status TEXT,first_seen_at TEXT,last_seen_at TEXT,method TEXT,UNIQUE(session_id,person_id));
            INSERT INTO attendance_records VALUES(1,1,NULL,'Legacy student','present','2026-10-06T08:00:00Z','2026-10-06T09:00:00Z','camera');''')
    store=Store(path)
    with store.connect() as db:
        row=db.execute('SELECT * FROM attendance_records').fetchone()
        assert row['status']=='present' and row['campus_state']=='unknown'
        assert row['checked_in_at'] is None and row['checked_out_at'] is None
        assert db.execute('SELECT count(*) FROM attendance_movements').fetchone()[0]==0
    Store(path)
