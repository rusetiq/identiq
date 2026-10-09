import hashlib
import re
import stat

from app import create_app
from test_app import setup, image, enroll, vector
from test_attendance import start, clock, recognize

PORTAL_HEADERS = {'X-Portal-Request':'1'}


def link(client, headers, person_id, name='Alex guardian'):
    return client.post('/api/parents', json={'name':name,'person_id':person_id}, headers=headers)


def parent_client(staff, code):
    client=staff.application.test_client()
    response=client.post('/api/parent/login',json={'code':code},headers=PORTAL_HEADERS)
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['Set-Cookie'] and 'SameSite=Strict' in response.headers['Set-Cookie']
    return client


def test_staff_sign_in_protects_every_roster_route(setup):
    staff,headers,_,database=setup
    guest=staff.application.test_client()
    html=guest.get('/').text
    assert 'Staff sign-in' in html and 'local-token' not in html
    for route in ['/api/status','/api/people','/api/parents','/api/attendance/sessions','/api/attendance/sessions/1','/api/attendance/sessions/1/export.csv']:
        assert guest.get(route).status_code==401
    assert guest.post('/api/enroll',json={},headers=headers).status_code==401
    assert guest.get('/parents').status_code==200
    assert 'local-token' not in guest.get('/parents').text
    assert stat.S_IMODE((database.parent/'staff-access-code.txt').stat().st_mode)==0o600
    assert guest.get('/data/staff-access-code.txt').status_code==401


def test_parent_sees_only_linked_student_and_camera_updates(setup,image):
    staff,headers,engine,database=setup
    person=enroll(staff,headers,image,name='Alex').json['person']
    engine.vector=vector(1);enroll(staff,headers,image,name='Private other student')
    created=link(staff,headers,person['id']).json['parent']
    parent=parent_client(staff,created['access_code'])
    empty=parent.get('/api/parent/attendance').json
    assert empty['student_name']=='Alex' and empty['history']==[] and empty['current'] is None
    session=start(staff,headers).json['session'];sid=session['id']
    pending=parent.get('/api/parent/attendance').json
    assert pending['current']['status']=='pending'
    engine.vector=vector();moment=clock(staff)
    import uuid
    scanner=str(uuid.uuid4())
    for _ in range(3): recognize(staff,headers,image,sid,scanner);moment[0]+=.7
    result=parent.get('/api/parent/attendance').json
    assert result['current']['status']=='present' and result['current']['first_seen_at']
    assert result['history'][0]['method']=='camera'
    assert 'Private other student' not in str(result)
    assert 'vector' not in str(result) and 'code_hash' not in str(result) and 'person_id' not in str(result)
    # Query parameters cannot expand the scoped student view.
    assert parent.get('/api/parent/attendance?person_id=2').json==result
    staff.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers)
    saved=parent.get('/api/parent/attendance').json
    assert saved['current'] is None and saved['history'][0]['status']=='present'
    assert saved['history'][0]['session_status']=='closed'
    with staff.application.extensions['attendance'].store.connect() as db:
        row=db.execute('SELECT code_hash FROM parent_access').fetchone()
        assert row['code_hash']==hashlib.sha256(created['access_code'].encode()).hexdigest()
    assert created['access_code'] not in staff.get('/api/parents').text


def test_parent_cannot_open_staff_or_modify_attendance(setup,image):
    staff,headers,_,_=setup
    person=enroll(staff,headers,image).json['person']
    created=link(staff,headers,person['id']).json['parent']
    parent=parent_client(staff,created['access_code'])
    assert 'local-token' not in parent.get('/').text
    for route in ['/api/people','/api/parents','/api/attendance/sessions']:
        assert parent.get(route).status_code==401
    assert parent.post('/api/attendance/sessions',json={'name':'Class'},headers=headers).status_code==401
    assert parent.delete(f"/api/people/{person['id']}",headers=headers).status_code==401
    assert parent.post('/api/staff/login',json={'code':created['access_code']},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    staff_code=staff.application.extensions['portal_access'].staff_code_path.read_text().strip()
    assert parent.post('/api/parent/login',json={'code':staff_code},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    assert staff.get('/api/parent/attendance').status_code==401


def test_absence_and_staff_correction_visible_to_parent(setup,image):
    staff,headers,_,_=setup
    person=enroll(staff,headers,image).json['person'];parent=parent_client(staff,link(staff,headers,person['id']).json['parent']['access_code'])
    session=start(staff,headers).json['session'];sid=session['id'];rid=session['roster'][0]['id']
    staff.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers)
    assert parent.get('/api/parent/attendance').json['history'][0]['status']=='absent'
    staff.patch(f'/api/attendance/sessions/{sid}/records/{rid}',json={'status':'present'},headers=headers)
    record=parent.get('/api/parent/attendance').json['history'][0]
    assert record['status']=='present' and record['method']=='manual' and record['first_seen_at'] is None


def test_revoke_and_delete_invalidate_existing_parent_sessions(setup,image):
    staff,headers,_,_=setup;person=enroll(staff,headers,image).json['person']
    first=link(staff,headers,person['id']).json['parent'];parent=parent_client(staff,first['access_code'])
    assert staff.post(f"/api/parents/{first['id']}/revoke",json={},headers=headers).json['revoked']
    assert parent.get('/api/parent/attendance').status_code==401
    assert parent.post('/api/parent/login',json={'code':first['access_code']},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    assert staff.get('/api/parents').json['parents']==[]
    second=link(staff,headers,person['id']).json['parent'];parent=parent_client(staff,second['access_code'])
    staff.delete(f"/api/people/{person['id']}",headers=headers)
    assert parent.get('/api/parent/attendance').status_code==401


def test_logout_expiry_and_restart(setup,image):
    staff,headers,engine,database=setup;person=enroll(staff,headers,image).json['person'];created=link(staff,headers,person['id']).json['parent']
    parent=parent_client(staff,created['access_code']);cookie=parent.get_cookie('identiq_parent',path='/api/parent')
    restarted=create_app(database,engine).test_client();restarted.set_cookie('identiq_parent',cookie.value,path='/api/parent')
    assert restarted.get('/api/parent/attendance').status_code==200
    parent.post('/api/parent/logout',json={},headers=PORTAL_HEADERS)
    assert parent.get('/api/parent/attendance').status_code==401
    assert restarted.get('/api/parent/attendance').status_code==401
    parent=parent_client(staff,created['access_code']);access=staff.application.extensions['portal_access'];now=access.clock();access.clock=lambda:now+access.SESSION_SECONDS+1
    assert parent.get('/api/parent/attendance').status_code==401
    assert staff.get('/api/people').status_code==401


def test_login_validation_rate_limit_and_cross_origin(setup):
    staff,headers,_,_=setup;guest=staff.application.test_client()
    assert guest.post('/api/parent/login',json={'code':'invalid'}).status_code==403
    assert guest.post('/api/parent/login',json={'code':'invalid'},headers={**PORTAL_HEADERS,'Origin':'https://evil.example'}).status_code==403
    assert guest.get('/parents',headers={'Host':'evil.example'}).status_code==403
    for _ in range(10):
        assert guest.post('/api/parent/login',json={'code':[]},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    assert guest.post('/api/parent/login',json={'code':'bad'},headers=PORTAL_HEADERS).json['code']=='login_limited'
    assert link(staff,headers,True).json['code']=='invalid_person'
    assert link(staff,headers,999).json['code']=='person_missing'
    assert link(staff,headers,1,name=' ').json['code']=='invalid_name'


def test_student_enrolled_after_active_roster_and_separate_parent_links(setup,image):
    staff,headers,engine,_=setup;enroll(staff,headers,image);start(staff,headers)
    engine.vector=vector(1);person=enroll(staff,headers,image,name='Late enrollment').json['person']
    one=link(staff,headers,person['id'],name='First guardian').json['parent'];two=link(staff,headers,person['id'],name='Second guardian').json['parent']
    parent=parent_client(staff,one['access_code']);other=parent_client(staff,two['access_code'])
    view=parent.get('/api/parent/attendance').json
    assert view['active_session'] and view['current'] is None and view['history']==[]
    staff.post(f"/api/parents/{one['id']}/revoke",json={},headers=headers)
    assert parent.get('/api/parent/attendance').status_code==401
    assert other.get('/api/parent/attendance').status_code==200


def test_staff_logout_and_public_pages_never_expose_code(setup):
    staff,headers,_,_=setup
    code=staff.application.extensions['portal_access'].staff_code_path.read_text().strip()
    assert code not in staff.get('/parents').text
    assert staff.post('/api/staff/logout',json={},headers=headers).status_code==200
    assert staff.get('/api/people').status_code==401
    assert code not in staff.get('/').text


def test_parent_calendar_uses_dubai_month_boundaries_and_full_month(setup,image):
    staff,headers,engine,_=setup
    person=enroll(staff,headers,image,name='Calendar student').json['person']
    engine.vector=vector(1);other=enroll(staff,headers,image,name='Other student').json['person']
    access=link(staff,headers,person['id']).json
    parent=parent_client(staff,access['parent']['access_code'])
    store=staff.application.extensions['portal_access'].store
    with store.connect() as db:
        for index,when in enumerate(['2026-09-30T19:59:59.000Z','2026-09-30T20:00:00.000Z','2026-10-31T20:00:00.000Z']+['2026-10-15T08:00:00.000Z']*101):
            sid=db.execute("INSERT INTO attendance_sessions(name,started_at,status) VALUES(?,?,'closed')",(f'Session {index}',when)).lastrowid
            db.execute("INSERT INTO attendance_records(session_id,person_id,name,status,method) VALUES(?,?,?,'present','manual')",(sid,person['id'],'Calendar student'))
            db.execute("INSERT INTO attendance_records(session_id,person_id,name,status,method) VALUES(?,?,?,'absent','session_end')",(sid,other['id'],'Other student'))
    data=parent.get('/api/parent/attendance?month=2026-10').json
    assert data['timezone']=='Asia/Dubai' and data['calendar_month']=='2026-10'
    assert len(data['history'])==100 and len(data['calendar'])==102
    names={record['session_name'] for record in data['calendar']}
    assert 'Session 1' in names and 'Session 0' not in names and 'Session 2' not in names
    assert all(record['status']=='present' for record in data['calendar'])
    assert parent.get('/api/parent/attendance?month=2026-11').json['calendar'][0]['session_name']=='Session 2'
    assert parent.get('/api/parent/attendance?month=2025-02').json['calendar']==[]
    for invalid in ['2026-13','2026-1','abc','2026-00','1899-12','2101-01']:
        assert parent.get('/api/parent/attendance',query_string={'month':invalid}).status_code==422
    assert staff.application.test_client().get('/api/parent/attendance?month=2026-10').status_code==401
