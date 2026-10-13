import hashlib
import json
import sqlite3
import stat

from werkzeug.security import check_password_hash
from app import create_app
from biometrics import Store
from parent_portal import PortalAccess
from test_app import setup,image,enroll
from test_parent_portal import link,parent_client,PORTAL_HEADERS,TEST_PARENT_PASSWORD


def test_password_storage_validation_and_code_login_removed(setup,image):
    client,headers,_,database=setup
    credentials=json.loads((database.parent/'staff-login.json').read_text())
    guest=client.application.test_client()
    assert guest.post('/api/staff/login',json={'code':credentials['password']},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    assert guest.post('/api/staff/login',json={**credentials,'login_id':'ADMIN'},headers=PORTAL_HEADERS).status_code==200
    student=enroll(client,headers,image).json['person']
    body={'name':'Guardian','person_id':student['id'],'login_id':'Parent.one','password':' strong password '}
    saved=client.post('/api/parents',json=body,headers=headers).json['parent']
    assert saved['login_id']=='parent.one' and 'password' not in saved and 'access_code' not in saved
    assert client.post('/api/parents',json={**body,'login_id':'PARENT.ONE'},headers=headers).json['code']=='duplicate_login_id'
    for overrides in [{'login_id':'a b'},{'login_id':[]},{'password':'short'},{'password':None},{'password':'x'*129}]:
        assert client.post('/api/parents',json={**body,**overrides},headers=headers).status_code==422
    parent=client.application.test_client()
    assert parent.post('/api/parent/login',json={'login_id':'parent.one','password':'strong password'},headers=PORTAL_HEADERS).json['code']=='invalid_access'
    assert parent.post('/api/parent/login',json={'login_id':'PARENT.ONE','password':body['password']},headers=PORTAL_HEADERS).status_code==200
    with sqlite3.connect(database) as db:
        staff_hash=db.execute('SELECT password_hash FROM staff_credentials').fetchone()[0]
        parent_hash=db.execute('SELECT password_hash FROM parent_access').fetchone()[0]
        assert staff_hash.startswith('scrypt:') and parent_hash.startswith('scrypt:')
        assert check_password_hash(parent_hash,body['password']) and parent_hash!=body['password']
    assert 'password_hash' not in client.get('/api/parents').text
    assert body['password'] not in client.get('/api/parents').text


def test_staff_rotation_invalidates_all_sessions_and_survives_restart(setup):
    client,headers,engine,database=setup
    credentials=json.loads((database.parent/'staff-login.json').read_text())
    other=client.application.test_client()
    other.post('/api/staff/login',json=credentials,headers=PORTAL_HEADERS)
    new={'login_id':'school.admin','password':'new-staff-password-2026','current_password':credentials['password']}
    assert client.patch('/api/staff/account',json=new).status_code==403
    assert client.patch('/api/staff/account',json={**new,'current_password':'wrong'},headers=headers).status_code==422
    assert client.get('/api/people').status_code==200
    response=client.patch('/api/staff/account',json=new,headers=headers)
    assert response.json['signed_out'] and not (database.parent/'staff-login.json').exists()
    assert client.get('/api/people').status_code==401 and other.get('/api/people').status_code==401
    restarted=create_app(database,engine).test_client()
    assert not (database.parent/'staff-login.json').exists()
    assert restarted.post('/api/staff/login',json=credentials,headers=PORTAL_HEADERS).status_code==422
    assert restarted.post('/api/staff/login',json=new,headers=PORTAL_HEADERS).status_code==200
    assert restarted.get('/api/staff/account').json=={'login_id':'school.admin'}


def test_parent_reset_scoped_unique_and_old_sessions_revoked(setup,image):
    client,headers,_,_=setup;student=enroll(client,headers,image).json['person']
    one=link(client,headers,student['id']).json['parent'];two=link(client,headers,student['id']).json['parent']
    parent=parent_client(client,one['login_id']);other=parent_client(client,two['login_id'])
    path=f"/api/parents/{one['id']}/credentials";body={'login_id':'guardian.updated','password':'new-parent-password-2026'}
    assert parent.patch(path,json=body,headers=headers).status_code==401
    assert client.patch(path,json=body).status_code==403
    assert client.patch(path,json={**body,'login_id':two['login_id']},headers=headers).json['code']=='duplicate_login_id'
    assert parent.get('/api/parent/attendance').status_code==200
    assert client.patch(path,json=body,headers=headers).status_code==200
    assert parent.get('/api/parent/attendance').status_code==401 and other.get('/api/parent/attendance').status_code==200
    assert parent.post('/api/parent/login',json={'login_id':one['login_id'],'password':TEST_PARENT_PASSWORD},headers=PORTAL_HEADERS).status_code==422
    assert parent.post('/api/parent/login',json=body,headers=PORTAL_HEADERS).status_code==200
    assert parent.get('/api/parent/attendance').json['student_name']==student['name']
    client.post(f"/api/parents/{one['id']}/revoke",json={},headers=headers)
    assert client.patch(path,json=body,headers=headers).json['code']=='parent_missing'


def test_legacy_codes_migrate_to_disabled_password_accounts_without_losing_links(tmp_path):
    store=Store(tmp_path/'private'/'test.sqlite3')
    with store.connect() as db:
        db.execute("INSERT INTO people(name) VALUES('Existing student')")
        db.executescript('''CREATE TABLE parent_access(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,person_id INTEGER,code_hash TEXT UNIQUE,created_at TEXT,revoked_at TEXT);
            CREATE TABLE portal_sessions(token_hash TEXT PRIMARY KEY,role TEXT,parent_id INTEGER,expires_at REAL);''')
        db.execute("INSERT INTO parent_access(name,person_id,code_hash) VALUES('Existing guardian',1,?)",(hashlib.sha256(b'old-code').hexdigest(),))
        db.execute("INSERT INTO portal_sessions VALUES(?,'parent',1,9999999999)",(hashlib.sha256(b'old-session').hexdigest(),))
    access=PortalAccess(store)
    assert access.links()[0]['needs_password']==1 and access.links()[0]['person_id']==1
    assert access.authenticate('old-session','parent') is None
    credentials=json.loads(access.staff_login_path.read_text())
    assert credentials['login_id']=='admin' and len(credentials['password'])>=10
    assert stat.S_IMODE(access.staff_login_path.stat().st_mode)==0o600
    access.reset_parent_credentials(1,'existing.guardian','chosen-parent-password')
    assert access.links()[0]['needs_password']==0
    access.login('parent','existing.guardian','chosen-parent-password','test')
    PortalAccess(store)
    assert json.loads(access.staff_login_path.read_text())==credentials
