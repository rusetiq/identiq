import re
import base64
import hashlib
from app import create_app
from build_info import installed_build
from test_app import setup


def test_public_check_shows_frozen_running_build_and_changed_disk_build(setup,monkeypatch):
    client,_,_,_=setup
    guest=client.application.test_client()
    first=guest.get('/connection-check?format=json')
    assert first.status_code==200 and first.json['ok']
    assert first.json['running_build']==first.json['installed_build']
    assert first.json['needs_restart'] is False
    assert set(first.json)=={'ok','running_build','installed_build','needs_restart'}
    assert first.headers['Cache-Control']=='no-store'
    monkeypatch.setattr('app.installed_build',lambda:'new-files')
    changed=guest.get('/connection-check?format=json')
    assert changed.json['needs_restart'] and changed.json['running_build']==first.json['running_build']
    assert changed.json['installed_build']=='new-files'
    assert changed.headers['X-Identiq-Build']==first.json['running_build']
    assert b'connection OK' in guest.get('/connection-check').data
    assert guest.get('/connection-check?format=json',base_url='http://unconfigured.example').status_code==403
    assert guest.get('/api/people').status_code==401


def test_all_pages_load_network_helper_first_and_version_every_asset(setup):
    client,_,_,_=setup
    guest=client.application.test_client()
    for page in (client.get('/'),guest.get('/'),guest.get('/parents')):
        html=page.text
        version=re.search(r'<meta name="app-build" content="([^"]+)"',html).group(1)
        scripts=re.findall(r'<script[^>]+src="([^"]+)"',html)
        assert scripts[0]==f'/static/network.js?v={version}'
        assert all(f'v={version}' in path for path in scripts)
        style=re.search(r'<style>(.*?)</style>',html,re.S).group(1)
        digest=base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        assert "'sha256-"+digest+"'" in page.headers['Content-Security-Policy']
        assert 'stylesheet' not in html
        assert 'Connection check' in html


def test_build_fingerprint_ignores_private_data_and_normalizes_windows_line_endings(tmp_path):
    (tmp_path/'app.py').write_bytes(b'code\n')
    before=installed_build(tmp_path)
    (tmp_path/'data').mkdir();(tmp_path/'data'/'private.sqlite3').write_bytes(b'private data')
    assert installed_build(tmp_path)==before
    (tmp_path/'app.py').write_bytes(b'code\r\n')
    assert installed_build(tmp_path)==before
    (tmp_path/'app.py').write_bytes(b'new code\n')
    assert installed_build(tmp_path)!=before
