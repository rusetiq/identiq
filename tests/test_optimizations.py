from contextlib import contextmanager
import uuid
import numpy as np
import pytest
from biometrics import Store
from attendance import Attendance
from test_app import setup, vector, image, enroll


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
    connections=[];original=store.connect
    @contextmanager
    def tracked():
        connections.append(1)
        with original() as db:yield db
    store.connect=tracked
    matches=[{'status':'matched','person':person,'similarity':1.,'match_margin':1.,'candidate_count':8,
              'box':[.1,.1,.3,.3],'quality':{'face_size_px':120,'sharpness':20,'detection_score':.99}} for person in people]
    for count in (1,2,3):
        connections.clear();result=attendance.observe_group(sid,scanner,matches,frame_id=str(count).zfill(64))
        assert len(connections)==1
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
