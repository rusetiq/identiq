from contextlib import contextmanager
import uuid
import numpy as np
import pytest
from biometrics import Store, identify
from attendance import Attendance
from test_app import setup, vector, image, enroll
from test_attendance import clock, recognize, start


def test_single_profile_lookup_matches_directory_and_profile_writes(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    first=store.enroll('A',[vector()]*5)
    second=store.enroll('B',[vector(2)]*5)
    with store.connect() as db:
        db.executemany('INSERT INTO embeddings(person_id,vector,source) VALUES(?,?,?)',[
            (first['id'],vector().tobytes(),'iphone_truedepth'),
            (first['id'],bytes(128*4),'laptop'),
        ])
    directory={person['id']:person for person in store.people()}
    assert store.person(first['id'])==directory[first['id']]
    assert store.person(second['id'])==directory[second['id']]
    assert store.person(first['id'])['samples']==7
    assert store.person(first['id'])['compatible_samples']==6
    assert store.person(first['id'])['iphone_samples']==1
    assert store.person(99999) is None
    def unexpected_directory_scan():
        pytest.fail('Single profile updates must not fetch the full directory')
    store.people=unexpected_directory_scan
    updated=store.update_student_profile(first['id'],'ST-001','7A')
    assert updated['student_id']=='ST-001' and updated['compatible_samples']==6
    staff=store.save_profile({'name':'New Staff','person_type':'staff','employee_code':'EMP-01'})
    assert staff['name']=='New Staff' and staff['samples']==staff['compatible_samples']==0


def test_batch_matches_average_sample_scores_and_reuses_snapshot(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    a=store.enroll('A',[vector(),vector(0,1),vector()])
    b=store.enroll('B',[vector(2)]*5)
    queries=[vector(),vector(2),vector(7)]
    batch=store.candidates_batch(queries)
    assert batch[0][0]['id']==a['id'] and batch[1][0]['id']==b['id']
    assert batch[0][0]['similarity']==pytest.approx(np.mean([1,1/np.sqrt(2),1]),abs=1e-7)
    snapshot=store._identity_cache
    assert store.candidates_batch(queries)==batch
    assert store._identity_cache is snapshot
    assert store.candidates_batch([])==[]


@pytest.mark.parametrize('size',[0,1,2,8])
def test_top_two_match_full_ranking_including_ties_and_unknowns(tmp_path,size):
    store=Store(tmp_path/'private'/'test.sqlite3')
    people=[store.enroll(str(i),[vector(i)]*5) for i in range(size)]
    queries=[vector(),vector(0,1),vector(3),vector(20)]
    full=store.candidates_batch(queries)
    assert store.candidates_batch(queries,top_two=True)==[row[:2] for row in full]
    if size>=3:
        # A real claim outside the top two must be a mismatch, not a missing enrollment.
        assert identify(store,vector(),claimed_id=people[-1]['id'])['status']=='mismatch'
        with store.connect() as db:db.execute('UPDATE people SET name=? WHERE id=?',('Updated',people[0]['id']))
        assert store.candidates_batch([vector()],top_two=True)[0][0]['name']=='Updated'


def test_identity_cache_refreshes_across_connections(tmp_path):
    path=tmp_path/'private'/'test.sqlite3';store=Store(path)
    person=store.enroll('A',[vector()]*5)
    store.candidates(vector())
    other=Store(path);other.enroll('B',[vector(2)]*5)
    assert len(store.candidates(vector()))==2
    with other.connect() as db:db.execute('UPDATE people SET name=? WHERE id=?',('Renamed',person['id']))
    assert store.candidates(vector())[0]['name']=='Renamed'
    with other.connect() as db:db.execute('DELETE FROM people WHERE id=?',(person['id'],))
    assert [p['name'] for p in store.candidates(vector())]==['B']


def test_group_attendance_uses_one_connection_and_retains_confirmed_status(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    people=[store.enroll(str(i),[vector(i)]*5) for i in range(8)]
    attendance=Attendance(store);sid=attendance.start('Group')['id'];scanner=str(uuid.uuid4());now=[100.0]
    attendance.clock=lambda:now[0]
    connections=[];statements=[];original=store.connect
    @contextmanager
    def tracked():
        connections.append(1)
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db
    store.connect=tracked
    matches=[{'status':'matched','person':person,'similarity':1.,'match_margin':1.,'candidate_count':8,
              'box':[.1,.1,.3,.3],'quality':{'face_size_px':120,'sharpness':20,'detection_score':.99}} for person in people]
    for count in (1,2,3):
        connections.clear();statements.clear();result=attendance.observe_group(sid,scanner,matches,frame_id=str(count).zfill(64))
        assert len(connections)==1
        assert len([sql for sql in statements if sql.startswith('SELECT') and 'attendance_records' in sql])==1
        assert all(r['confirmations']==count for r in result)
        assert all(r['status']==('present' if count==3 else 'confirming') and r['newly_present']==(count==3) for r in result)
        now[0]+=.7
    assert all(r['status']=='present' for r in result)
    now[0]-=.6
    burst=attendance.observe_group(sid,scanner,matches,frame_id='4'.zfill(64))
    assert all(r['status']=='present' and not r['newly_moved'] for r in burst)
    assert attendance.detail(sid)['counts']['present']==8


def test_overview_default_history_and_auth(setup,image):
    client,headers,_,_=setup
    assert client.get('/api/attendance/overview').json=={'sessions':[],'session':None}
    enroll(client,headers,image)
    first=client.post('/api/attendance/sessions',json={'name':'First'},headers=headers).json['session']['id']
    assert client.get('/api/attendance/overview').json['session']['id']==first
    client.post(f'/api/attendance/sessions/{first}/finish',json={},headers=headers)
    second=client.post('/api/attendance/sessions',json={'name':'Second'},headers=headers).json['session']['id']
    assert client.get('/api/attendance/overview').json['session']['id']==second
    assert client.get(f'/api/attendance/overview?session_id={first}').json['session']['status']=='closed'
    for invalid in ['0','-1','abc','9'*5000]:
        assert client.get('/api/attendance/overview',query_string={'session_id':invalid}).status_code==422
    assert client.application.test_client().get('/api/attendance/overview').status_code==401


def test_repeated_confirmed_sightings_do_not_rewrite_the_database(setup,image):
    client,headers,_,_=setup
    enroll(client,headers,image)
    sid=start(client,headers).json['session']['id'];moment=clock(client);scanner=str(uuid.uuid4())
    attendance=client.application.extensions['attendance']
    for _ in range(3):recognize(client,headers,image,sid,scanner);moment[0]+=.7
    statements=[];original=attendance.store.connect
    @contextmanager
    def tracked():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db
    attendance.store.connect=tracked
    for _ in range(5):
        assert recognize(client,headers,image,sid,scanner).json['attendance']['status']=='present'
        moment[0]+=.7
    assert not any(sql.startswith('UPDATE attendance_records') for sql in statements)
    assert len(attendance.detail(sid)['movements'])==1
    # Last-seen still refreshes after the reporting interval, without another arrival.
    with original() as db:
        db.execute("UPDATE attendance_records SET last_seen_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','-10 seconds') WHERE session_id=?",(sid,))
    statements.clear()
    assert recognize(client,headers,image,sid,scanner).json['attendance']['status']=='present'
    assert len([sql for sql in statements if sql.startswith('UPDATE attendance_records')])==1
    assert len(attendance.detail(sid)['movements'])==1


def test_active_session_id_lookup(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    attendance=Attendance(store)
    store.save_profile({'name':'Student 1','student_id':'S1','grade':'10','section':'A','academic_year':'2025'})

    assert attendance.active_session_id() is None

    session_1=attendance.start('Session 1',class_name='10 A',academic_year='2025')
    assert attendance.active_session_id()==session_1['id']

    attendance.finish(session_1['id'])
    assert attendance.active_session_id() is None

    session_2=attendance.start('Session 2',class_name='10 A',academic_year='2025')
    assert attendance.active_session_id()==session_2['id']


def test_shared_camera_uses_active_session_id_without_full_directory_scan(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    attendance=Attendance(store)
    from shared_camera import SharedCamera
    shared=SharedCamera(attendance)

    store.save_profile({'name':'Student 1','student_id':'S1','grade':'10','section':'A','academic_year':'2025'})
    session=attendance.start('Session 1',class_name='10 A',academic_year='2025')

    def unexpected_sessions():
        pytest.fail('SharedCamera must not scan full attendance sessions history')

    attendance.sessions=unexpected_sessions

    client=shared.connect()
    shared.command(client,{'type':'claim'})
    jpeg=bytes.fromhex('ffd8ffe000104a46494600010101006000600000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffda0008010100003f00d2cf2000ffd9')
    shared.publish(client,jpeg)
    shared.command(client,{'type':'start'})
    assert shared.running and shared.session_id==session['id']

    shared.attendance_changed()
