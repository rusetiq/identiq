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


def test_saved_static_address_overrides_discovery_and_can_be_reset(tmp_path,monkeypatch):
    import scripts.run_network as network
    settings=tmp_path/'private'/'network.json'
    network.remember_address('10.10.40.40',settings)
    original=network.lan_address
    monkeypatch.setattr(network,'lan_address',lambda value=None:original(value) if value is not None else '10.30.31.200')
    assert network.configured_address(settings_path=settings)=='10.10.40.40'
    assert network.configured_address(auto=True,settings_path=settings)=='10.30.31.200'
    assert network.configured_address('192.168.1.20',settings_path=settings)=='192.168.1.20'
    assert network.configured_address(pinned='10.10.40.40',auto=True,settings_path=settings)=='10.10.40.40'
    settings.write_text('{broken')
    with pytest.raises(ValueError,match='--address'):network.configured_address(settings_path=settings)
    assert network.configured_address('10.10.40.40',settings_path=settings)=='10.10.40.40'


def test_listener_group_detects_dead_https_and_closes_other_listeners():
    import threading
    from scripts.run_network import NetworkListeners
    class Listener:
        def __init__(self):self.stop=threading.Event();self.closed=False
        def serve_forever(self,poll_interval):self.stop.wait()
        def shutdown(self):self.stop.set()
        def server_close(self):self.closed=True;self.stop.set()
    group=NetworkListeners();http=Listener();https=Listener()
    group.add('HTTP',http);group.add('HTTPS',https);group.start()
    try:
        https.stop.set();group.threads[1][1].join(timeout=1)
        with pytest.raises(RuntimeError,match='HTTPS listener stopped'):group.check()
    finally:group.close()
    assert http.closed and https.closed
    assert all(not thread.is_alive() for _,thread in group.threads)


def test_network_worker_binds_both_protocols_to_the_explicit_static_ip(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    import scripts.run_network as network
    bound=[];saved=[]
    class Listener:
        def __init__(self):self.closed=False
        def server_close(self):self.closed=True
    def bind(host,port,*args,**kwargs):bound.append((host,port));return Listener()
    monkeypatch.setattr(network,'create_app',lambda **kwargs:SimpleNamespace(config={'IDENTIQ_BUILD':'test'}))
    monkeypatch.setattr(network,'make_server',bind);monkeypatch.setattr(network,'CameraHTTPSServer',bind)
    monkeypatch.setattr(network,'certificate_for',lambda address:('public.crt','private.key'))
    monkeypatch.setattr(network,'remember_address',lambda address:saved.append(address))
    monkeypatch.setattr(network.NetworkListeners,'start',lambda self:None)
    def stopped(self):raise KeyboardInterrupt()
    monkeypatch.setattr(network.NetworkListeners,'wait',stopped)
    monkeypatch.setattr(sys,'argv',['run_network.py','--worker','--address','10.10.40.40','--http','--port','8080','--https-port','8443'])
    network.main()
    assert bound==[('127.0.0.1',8080),('10.10.40.40',8080),('10.10.40.40',8443)]
    assert saved==['10.10.40.40']


@pytest.mark.parametrize('data',['{"address":null}','{"address":false}','[]'])
def test_malformed_saved_address_never_silently_changes_interface(tmp_path,data):
    from scripts.run_network import configured_address
    settings=tmp_path/'network.json';settings.write_text(data)
    with pytest.raises(ValueError,match='Saved network address'):configured_address(settings_path=settings)
