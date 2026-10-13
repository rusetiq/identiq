import base64
import pytest
from biometrics import FaceError
from test_app import setup,image,enroll
from test_attendance import start
from test_parent_portal import link,parent_client


def room_fixture(setup,image):
    client,headers,_,_=setup
    enroll(client,headers,image)
    sid=start(client,headers).json['session']['id']
    room=client.application.extensions['shared_camera'];moment=[100.];room.clock=lambda:moment[0]
    source=room.connect();viewer=room.connect()
    jpeg=base64.b64decode(image.split(',')[1])
    return client,headers,sid,room,moment,source,viewer,jpeg


def test_one_source_latest_frames_controls_and_keys_are_scoped(setup,image):
    _,_,sid,room,clock,source,viewer,jpeg=room_fixture(setup,image)
    with pytest.raises(FaceError):room.command(viewer,{'type':'start'})
    room.command(source,{'type':'claim','action':'check_in'})
    with pytest.raises(FaceError):room.command(viewer,{'type':'claim'})
    assert 'source_key' in room.snapshot(source) and 'source_key' not in room.snapshot(viewer)
    with pytest.raises(FaceError):room.publish(viewer,jpeg)
    room.publish(source,jpeg);version,latest=room.latest();assert latest==jpeg
    room.publish(source,jpeg);assert room.latest()[0]==version
    clock[0]+=.2;room.publish(source,jpeg);assert room.latest()[0]==version+1
    room.command(viewer,{'type':'start'});assert room.snapshot(source)['running'] and room.session_id==sid
    epoch=room.epoch
    room.command(viewer,{'type':'configure','action':'check_out','axis':'y','direction':'negative'})
    assert not room.running and room.action=='check_out' and room.epoch>epoch
    room.command(viewer,{'type':'start'});room.command(viewer,{'type':'pause'})
    assert not room.running
    with pytest.raises(FaceError):room.command(viewer,{'type':'release'})
    room.command(source,{'type':'release'});assert room.latest()[1] is None
    room.command(viewer,{'type':'claim'});assert room.source==viewer


def test_shared_scan_rejects_non_source_and_late_results(setup,image):
    client,headers,sid,room,_,source,viewer,jpeg=room_fixture(setup,image)
    room.command(source,{'type':'claim','action':'check_in'});room.publish(source,jpeg);room.command(viewer,{'type':'start'})
    snapshot=room.snapshot(source)
    payload={'image':image,'attendance_session_id':sid,'scanner_id':snapshot['scanner_id'],
             'shared_key':snapshot['source_key'],'shared_epoch':snapshot['epoch'],'attendance_action':'check_out'}
    assert client.post('/api/recognize',json={k:v for k,v in payload.items() if k!='shared_key'},headers=headers).json['code']=='shared_scan_stopped'
    matched=client.post('/api/recognize',json=payload,headers=headers).json
    assert matched['attendance']['newly_moved'] and matched['attendance']['action']=='check_in' # server configuration wins
    assert len(room.attendance.detail(sid)['movements'])==1
    room.command(viewer,{'type':'pause'})
    assert client.post('/api/recognize',json=payload,headers=headers).json['code']=='shared_scan_stopped'
    room.command(viewer,{'type':'start'})
    assert client.post('/api/recognize',json=payload,headers=headers).json['code']=='shared_scan_stopped'
    room.disconnect(source)
    assert client.post('/api/recognize',json=payload,headers=headers).json['code']=='shared_scan_stopped'
    assert len(room.attendance.detail(sid)['movements'])==1


def test_disconnect_stale_feed_and_day_end_stop_scan(setup,image):
    client,headers,sid,room,clock,source,viewer,jpeg=room_fixture(setup,image)
    room.command(source,{'type':'claim'});room.publish(source,jpeg);room.command(viewer,{'type':'start'})
    clock[0]+=6;assert not room.snapshot(viewer)['ready'] and not room.running and room.latest()[1] is None
    room.publish(source,jpeg);room.command(viewer,{'type':'start'})
    client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers)
    assert not room.running and room.attendance_revision>0
    room.disconnect(source);assert room.source is None and room.latest()[1] is None


def test_malformed_frames_commands_and_websocket_auth(setup,image):
    client,headers,_,room,_,source,viewer,_=room_fixture(setup,image)
    room.command(source,{'type':'claim'})
    for frame in (b'invalid',b'X'*350001):
        with pytest.raises(FaceError):room.publish(source,frame)
    assert room.frame is None
    for message in ({'type':'configure','action':[],'axis':'x','direction':'positive'},{'type':'hijack'}):
        with pytest.raises(FaceError):room.command(viewer,message)
    guest=client.application.test_client()
    assert guest.get('/api/shared/ws',headers={'Origin':'http://localhost'}).status_code==401
    assert client.get('/api/shared/ws',headers={'Origin':'http://evil.example'}).status_code==403
    assert client.get('/api/shared/ws').status_code==403
    person=room.attendance.store.people()[0]
    parent=parent_client(client,link(client,headers,person['id']).json['parent']['login_id'])
    assert parent.get('/api/shared/ws',headers={'Origin':'http://localhost'}).status_code==401
    assert not room.running
