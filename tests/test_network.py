import pytest
from app import create_app
from scripts.run_network import lan_address
from test_app import FakeEngine


def test_network_address_is_explicit_and_auth_and_origin_checks_remain(tmp_path):
    app=create_app(tmp_path/'private'/'test.sqlite3',FakeEngine(),allowed_hosts={'10.30.31.186'})
    client=app.test_client();base='https://10.30.31.186:8443'
    assert 'Staff sign-in' in client.get('/',base_url=base).text
    assert client.get('/parents',base_url=base).status_code==200
    assert client.get('/api/people',base_url=base).status_code==401
    assert client.get('/',base_url='https://10.30.31.187:8443').status_code==403
    assert client.post('/api/staff/login',base_url=base,json={},headers={'X-Portal-Request':'1','Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/staff/login',base_url=base,json={}).status_code==403
    assert client.get('/data/staff-login.json',base_url=base).status_code==401
    import json
    credentials=json.loads(app.extensions['portal_access'].staff_login_path.read_text())
    login=client.post('/api/staff/login',base_url=base,json=credentials,headers={'X-Portal-Request':'1','Origin':base})
    assert login.status_code==200 and 'Secure;' in login.headers['Set-Cookie']
    assert client.get('/api/people',base_url=base).status_code==200
    assert client.post('/api/attendance/sessions',base_url=base,json={'name':'Network day'}).status_code==403


@pytest.mark.parametrize('value',['8.8.8.8','0.0.0.0','127.0.0.1','169.254.1.1','invalid','::1'])
def test_network_runner_rejects_non_lan_addresses(value):
    with pytest.raises(ValueError):lan_address(value)


def test_private_network_address_is_valid():
    assert lan_address('10.30.31.186')=='10.30.31.186'
