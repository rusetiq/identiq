from test_app import setup,image,enroll,vector
from test_shared_camera import room_fixture
import pytest
from biometrics import FaceError

STUDENT={'name':'Test Student','person_type':'student','admission_number':'ADM-01','grade':'Grade 7','section':'A','academic_year':'2026-2027'}
STAFF={'name':'Test Employee','person_type':'staff','employee_code':'EMP-01'}

def register(client,headers,**data):
    return client.post('/api/people',json=data,headers=headers)

def test_register_then_enroll_selected_record_and_keep_identity(setup,image):
    client,headers,engine,_=setup
    person=register(client,headers,**STUDENT).json['person']
    assert person['samples']==0 and person['student_id']=='ADM-01'
    assert client.post('/api/recognize',json={'image':image},headers=headers).json['status']=='empty'
    saved=enroll(client,headers,image,person_id=person['id'],name='Ignored capture name')
    assert saved.status_code==201 and saved.json['person']['name']=='Test Student'
    assert len(client.get('/api/people').json['people'])==1
    assert client.get('/api/people').json['people'][0]['samples']==5
    other=register(client,headers,**{**STUDENT,'admission_number':'ADM-02'}).json['person']
    assert enroll(client,headers,image,person_id=other['id']).json['code']=='duplicate_face'
    engine.vector=vector(1)
    assert enroll(client,headers,image,person_id=other['id']).status_code==201

def test_school_fields_unique_ids_and_role_boundaries(setup):
    client,headers,_,_=setup
    assert register(client,headers,**{**STUDENT,'section':''}).status_code==422
    assert register(client,headers,**STUDENT).status_code==201
    assert register(client,headers,**{**STUDENT,'admission_number':'adm-01'}).json['code']=='duplicate_school_id'
    staff=register(client,headers,**STAFF).json['person']
    assert staff['student_id']=='' and staff['employee_code']=='EMP-01'
    assert register(client,headers,**{**STAFF,'employee_code':'emp-01'}).status_code==422
    assert client.post('/api/parents',headers=headers,json={'name':'Guardian','person_id':staff['id'],'login_id':'guardian.test','password':'example-password-123'}).status_code==422
    assert client.application.test_client().post('/api/people',headers=headers,json=STUDENT).status_code==401
    assert client.post('/api/people',json=STUDENT).status_code==403

def test_roster_includes_unenrolled_and_keeps_school_snapshot(setup):
    client,headers,_,_=setup
    student=register(client,headers,**STUDENT).json['person']
    register(client,headers,**STAFF)
    session=client.post('/api/attendance/sessions',headers=headers,json={'name':'School day','person_type':'student','academic_year':'2026-2027'}).json['session']
    assert len(session['roster'])==1 and session['roster'][0]['grade']=='Grade 7'
    assert client.patch(f"/api/people/{student['id']}/profile",headers=headers,json={**STUDENT,'grade':'Grade 8'}).status_code==200
    detail=client.get(f"/api/attendance/sessions/{session['id']}").json['session']
    assert detail['roster'][0]['grade']=='Grade 7'
    csv=client.get(f"/api/attendance/sessions/{session['id']}/export.csv").text
    assert 'Academic year' in csv and '2026-2027' in csv
    context=client.get('/api/client-context')
    assert context.json['token']==headers['X-Local-Token']
    assert client.application.test_client().get('/api/client-context').status_code==401

def test_disconnected_source_recovers_fresh_scanner_but_old_results_fail(setup,image):
    _,_,sid,room,clock,source,viewer,jpeg=room_fixture(setup,image)
    room.command(source,{'type':'claim','action':'check_in'});room.publish(source,jpeg);room.command(viewer,{'type':'start'})
    old=room.snapshot(source);room.disconnect(source)
    assert room.snapshot(viewer)['recovering'] and not room.running
    replacement=room.connect()
    with pytest.raises(FaceError):room.command(replacement,{'type':'claim'})
    room.command(replacement,{'type':'claim','resume_key':old['source_key']})
    assert not room.running
    clock[0]+=1;room.publish(replacement,jpeg)
    fresh=room.snapshot(replacement)
    assert fresh['running'] and fresh['scanner_id']!=old['scanner_id'] and fresh['session_id']==sid
    with pytest.raises(FaceError):room.guard({'shared_key':old['source_key'],'shared_epoch':old['epoch'],'attendance_session_id':sid,'scanner_id':old['scanner_id']})

def test_manual_pause_and_end_are_honored_during_recovery(setup,image):
    client,headers,sid,room,clock,source,viewer,jpeg=room_fixture(setup,image)
    room.command(source,{'type':'claim'});room.publish(source,jpeg);room.command(viewer,{'type':'start'})
    old=room.snapshot(source);room.disconnect(source);room.command(viewer,{'type':'pause'})
    clock[0]+=40;replacement=room.connect()
    room.command(replacement,{'type':'claim','resume_key':old['source_key']});room.publish(replacement,jpeg)
    assert not room.running and not room.armed
    room.command(viewer,{'type':'start'});clock[0]+=6;room.snapshot(viewer)
    assert room.armed and not room.running
    client.post(f'/api/attendance/sessions/{sid}/finish',headers=headers,json={})
    room.publish(replacement,jpeg)
    assert not room.running and not room.armed
    with pytest.raises(FaceError):room.command(viewer,{'type':'start','session_id':sid})
