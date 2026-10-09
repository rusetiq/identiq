import csv
import io
import uuid

import pytest

from app import create_app
from attendance import Attendance
from biometrics import FaceError, Store
from test_app import setup, image, vector, enroll, FakeEngine


def start(client, headers, name="Morning attendance"):
    return client.post('/api/attendance/sessions',json={"name":name},headers=headers)


def clock(client):
    moment=[100.0]
    client.application.extensions['attendance'].clock=lambda:moment[0]
    return moment


def recognize(client,headers,image,session_id,scanner=None):
    return client.post('/api/recognize',json={"image":image,"attendance_session_id":session_id,
        "scanner_id":scanner or str(uuid.uuid4())},headers=headers)


def test_camera_marks_present_once_and_unseen_absent_only_at_end(setup,image):
    client,headers,engine,database=setup
    enroll(client,headers,image,name='Alex')
    engine.vector=vector(1);enroll(client,headers,image,name='Taylor')
    session=start(client,headers).json['session'];sid=session['id']
    assert session['counts']=={"total":2,"present":0,"pending":2,"absent":0}
    engine.vector=vector();moment=clock(client);scanner=str(uuid.uuid4())
    for count in (1,):
        response=recognize(client,headers,image,sid,scanner).json
        assert response['attendance']['confirmations']==count
        moment[0]+=.7
    assert response['attendance']['newly_present'] is True
    roster=client.get(f'/api/attendance/sessions/{sid}').json['session']
    assert roster['counts']=={"total":2,"present":1,"pending":1,"absent":0}
    first=next(p for p in roster['roster'] if p['name']=='Alex')['first_seen_at']
    assert first
    assert recognize(client,headers,image,sid,scanner).json['attendance']['newly_present'] is False
    final=client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers).json['session']
    assert final['counts']=={"total":2,"present":1,"pending":0,"absent":1}
    assert final['ended_at'] and final['status']=='closed'
    assert next(p for p in final['roster'] if p['name']=='Alex')['first_seen_at']==first
    assert client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers).json['session']==final
    assert recognize(client,headers,image,sid,scanner).json['code']=='session_closed'
    assert Attendance(Store(database)).detail(sid)['counts']==final['counts']


@pytest.mark.parametrize('interruption',['unknown','no_face','different_person','gap'])
def test_interruptions_do_not_undo_first_match_attendance(setup,image,interruption):
    client,headers,engine,_=setup
    enroll(client,headers,image,name='Alex');engine.vector=vector(1);enroll(client,headers,image,name='Taylor');engine.vector=vector()
    sid=start(client,headers).json['session']['id'];scanner=str(uuid.uuid4());moment=clock(client)
    recognize(client,headers,image,sid,scanner);moment[0]+=.7
    if interruption=='unknown': engine.vector=vector(2)
    if interruption=='different_person': engine.vector=vector(1)
    if interruption=='no_face': engine.error=FaceError('no_face','No face')
    if interruption=='gap': moment[0]+=5
    else:
        recognize(client,headers,image,sid,scanner);moment[0]+=.7
    engine.vector=vector();engine.error=None
    result=recognize(client,headers,image,sid,scanner).json['attendance']
    assert result['status']=='present' and result['confirmations']==1
    assert client.get(f'/api/attendance/sessions/{sid}').json['session']['counts']['present']==(2 if interruption=='different_person' else 1)


def test_fast_duplicates_and_separate_scanners_keep_one_present_record(setup,image):
    client,headers,_,_=setup;enroll(client,headers,image)
    sid=start(client,headers).json['session']['id'];moment=clock(client);scanner=str(uuid.uuid4())
    for _ in range(5): assert recognize(client,headers,image,sid,scanner).json['attendance']['confirmations']==1
    moment[0]+=.7
    assert recognize(client,headers,image,sid,str(uuid.uuid4())).json['attendance']['confirmations']==1
    assert client.get(f'/api/attendance/sessions/{sid}').json['session']['counts']['present']==1


def test_roster_is_a_snapshot_and_history_survives_deleted_enrollment(setup,image):
    client,headers,engine,database=setup
    person=enroll(client,headers,image,name='Alex').json['person']
    sid=start(client,headers).json['session']['id']
    engine.vector=vector(1);enroll(client,headers,image,name='New enrollee')
    assert recognize(client,headers,image,sid).json['attendance']['status']=='outside_roster'
    client.delete(f"/api/people/{person['id']}",headers=headers)
    detail=client.get(f'/api/attendance/sessions/{sid}').json['session']
    assert len(detail['roster'])==1 and detail['roster'][0]['name']=='Alex'
    assert detail['roster'][0]['person_id'] is None
    assert Store(database).people()[0]['name']=='New enrollee'


def test_manual_correction_is_preserved_and_can_be_reset(setup,image):
    client,headers,_,_=setup;enroll(client,headers,image)
    session=start(client,headers).json['session'];sid=session['id'];rid=session['roster'][0]['id']
    route=f'/api/attendance/sessions/{sid}/records/{rid}'
    response=client.patch(route,json={"status":"absent"},headers=headers)
    assert response.json['session']['roster'][0]['method']=='manual'
    moment=clock(client);scanner=str(uuid.uuid4())
    for _ in range(4):
        assert recognize(client,headers,image,sid,scanner).json['attendance']['status']=='manual_override'
        moment[0]+=.7
    assert client.get(f'/api/attendance/sessions/{sid}').json['session']['counts']['absent']==1
    client.patch(route,json={"status":"pending"},headers=headers)
    assert client.get(f'/api/attendance/sessions/{sid}').json['session']['roster'][0]['method'] is None
    client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers)
    assert client.patch(route,json={"status":"pending"},headers=headers).status_code==422
    changed=client.patch(route,json={"status":"present"},headers=headers).json['session']
    assert changed['counts']['present']==1 and changed['roster'][0]['method']=='manual'
    assert changed['roster'][0]['first_seen_at'] is None


def test_empty_roster_single_active_session_and_new_session(setup,image):
    client,headers,_,_=setup
    assert start(client,headers).json['code']=='empty_roster'
    enroll(client,headers,image)
    first=start(client,headers).json['session']['id']
    assert start(client,headers).json['code']=='session_active'
    client.post(f'/api/attendance/sessions/{first}/finish',json={},headers=headers)
    second=start(client,headers).json['session']
    assert second['id']!=first and second['counts']['pending']==1
    assert len(client.get('/api/attendance/sessions').json['sessions'])==2


def test_csv_export_and_formula_escaping(setup,image):
    client,headers,_,_=setup
    enroll(client,headers,image,name='=SUM(1,2)')
    sid=start(client,headers,name='@Morning').json['session']['id']
    response=client.get(f'/api/attendance/sessions/{sid}/export.csv')
    assert response.mimetype=='text/csv' and 'attachment' in response.headers['Content-Disposition']
    rows=list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows[1][0]=="'@Morning" and rows[1][3]=="'=SUM(1,2)"
    assert rows[1][4]=='pending'


@pytest.mark.parametrize('session_id',[True,0,-1,'1'])
def test_invalid_session_id_is_not_accepted(setup,image,session_id):
    client,headers,_,_=setup
    assert recognize(client,headers,image,session_id).json['code']=='invalid_session'


def test_invalid_requests_and_local_protection(setup,image):
    client,headers,_,_=setup;enroll(client,headers,image)
    assert start(client,{},name='Class').status_code==403
    assert start(client,headers,name=' ').json['code']=='invalid_name'
    session=start(client,headers).json['session'];sid=session['id'];rid=session['roster'][0]['id']
    assert recognize(client,headers,image,sid,scanner='not-a-uuid').json['code']=='invalid_scanner'
    assert client.post(f'/api/attendance/sessions/{sid}/finish',json={}).status_code==403
    assert client.patch(f'/api/attendance/sessions/{sid}/records/{rid}',json={"status":True},headers=headers).json['code']=='invalid_attendance'
    assert client.patch(f'/api/attendance/sessions/{sid}/records/999',json={"status":"present"},headers=headers).json['code']=='record_missing'
    assert client.get('/api/attendance/sessions/999').json['code']=='session_missing'


def test_closed_records_load_after_app_restart(setup,image):
    client,headers,engine,database=setup;enroll(client,headers,image)
    sid=start(client,headers).json['session']['id']
    client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers)
    restarted=create_app(database,FakeEngine()).test_client()
    code=(database.parent/'staff-access-code.txt').read_text().strip()
    restarted.post('/api/staff/login',json={'code':code},headers={'X-Portal-Request':'1'})
    result=restarted.get(f'/api/attendance/sessions/{sid}').json['session']
    assert result['status']=='closed' and result['counts']['absent']==1
