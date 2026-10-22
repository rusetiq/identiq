import base64
import gzip
import hashlib
import re

from flask import Flask

from test_app import setup
from web_assets import WebAssets


def test_all_pages_include_styles_and_support_small_compressed_html(setup):
    staff,_,_,_=setup
    guest=staff.application.test_client()
    for client,path in ((guest,'/'),(guest,'/parents'),(staff,'/')):
        plain=client.get(path)
        packed=client.get(path,headers={'Accept-Encoding':'gzip'})
        assert packed.headers['Content-Encoding']=='gzip'
        assert packed.headers['Cache-Control']=='no-store'
        assert len(packed.data)<len(plain.data)/2
        html=gzip.decompress(packed.data).decode()
        css=re.search(r'<style>(.*?)</style>',html,re.S).group(1)
        digest=base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in packed.headers['Content-Security-Policy']
        assert "'unsafe-inline'" not in packed.headers['Content-Security-Policy']
        assert 'rel="stylesheet"' not in html
        assert '[hidden]{display:none!important}' in css


def test_versioned_public_files_are_cached_compressed_and_revalidated(setup):
    client=setup[0].application.test_client()
    build=client.application.config['IDENTIQ_BUILD']
    for filename in ('network.js','app.js','style.css'):
        path=f'/static/{filename}?v={build}'
        plain=client.get(path)
        assert plain.status_code==200 and plain.cache_control.max_age==31536000
        assert plain.cache_control.public and 'immutable' in plain.headers['Cache-Control']
        packed=client.get(path,headers={'Accept-Encoding':'gzip'})
        assert gzip.decompress(packed.data)==plain.data
        assert packed.headers['ETag']!=plain.headers['ETag']
        assert 'Accept-Encoding' in packed.headers['Vary']
        again=client.get(path,headers={'Accept-Encoding':'gzip','If-None-Match':packed.headers['ETag']})
        assert again.status_code==304 and not again.data
        assert 'immutable' in again.headers['Cache-Control']
        # Changing accepted representation cannot reuse a compressed ETag.
        assert client.get(path,headers={'If-None-Match':packed.headers['ETag']}).status_code==200
        head=client.head(path,headers={'Accept-Encoding':'gzip'})
        assert head.data==b'' and head.content_length==packed.content_length


def test_wrong_versions_private_data_errors_and_hosts_are_not_immutably_cached(setup):
    client=setup[0]
    for path in ('/static/app.js','/static/app.js?v=older-build'):
        response=client.get(path,headers={'Accept-Encoding':'gzip;q=0'})
        assert response.cache_control.no_cache and 'immutable' not in response.headers['Cache-Control']
        assert 'Content-Encoding' not in response.headers
    for path in ('/','/parents','/api/people','/api/status','/connection-check?format=json','/static/missing.js'):
        assert client.get(path).headers['Cache-Control']=='no-store'
    blocked=client.get('/static/app.js',base_url='http://unconfigured.example')
    assert blocked.status_code==403 and blocked.headers['Cache-Control']=='no-store'
    assert client.get('/api/people',headers={'Accept-Encoding':'gzip'}).headers.get('Content-Encoding') is None


def test_asset_bytes_are_frozen_until_restart_even_if_files_change(tmp_path):
    (tmp_path/'style.css').write_text('body{color:red}\n')
    (tmp_path/'app.js').write_text('old contents\n')
    assets=WebAssets(tmp_path,'first')
    app=Flask(__name__)
    (tmp_path/'app.js').write_text('updated contents\n')
    with app.test_request_context('/static/app.js?v=first'):
        old=assets.serve('app.js')
        assert old.data==b'old contents\n'
        assert old.cache_control.max_age==31536000
    updated=WebAssets(tmp_path,'second')
    with app.test_request_context('/static/app.js?v=second'):
        assert updated.serve('app.js').data==b'updated contents\n'
