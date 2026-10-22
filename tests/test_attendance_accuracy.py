"""Regression checks for false presence, using controlled impostor similarities."""
import hashlib
import uuid

import numpy as np
import pytest

from biometrics import normalize
from test_app import setup, image, enroll, vector, fresh_image
from test_attendance import start, clock, recognize
from test_group_faces import response_faces, scan_group
from test_school_workflow import movement_fixture


def prepare(setup, image):
    client, headers, engine, _ = setup
    enroll(client, headers, image)
    sid = start(client, headers).json['session']['id']
    return client, headers, engine, sid, clock(client), str(uuid.uuid4())


def no_attendance(client, sid):
    session = client.application.extensions['attendance'].detail(sid)
    assert session['counts']['present'] == 0
    assert session['movements'] == []
    assert all(row['first_seen_at'] is None and row['checked_in_at'] is None for row in session['roster'])


@pytest.mark.parametrize('group', [False, True])
def test_old_threshold_impostor_cannot_write_attendance(setup, image, group):
    client, headers, engine, sid, moment, scanner = prepare(setup, image)
    # This query scored .55 against the enrolled person and passed the old .45 rule.
    engine.vector = normalize(.55*vector() + np.sqrt(1-.55**2)*vector(2))
    engine.extract_group = lambda frame:response_faces([engine.vector])
    for _ in range(5):
        result = (scan_group if group else recognize)(client, headers, image, sid, scanner).json
        face = result['faces'][0] if group else result
        assert face['status'] == 'unknown' and 'person' not in face
        assert face['attendance']['status'] == 'unrecognized'
        moment[0] += .35
    no_attendance(client, sid)


@pytest.mark.parametrize('group', [False, True])
def test_close_high_scoring_candidates_need_review_instead_of_attendance(setup, image, group):
    client, headers, engine, _ = setup
    enroll(client, headers, image)
    engine.vector=vector(1);enroll(client, headers, image, name='Other student')
    sid=start(client, headers).json['session']['id'];moment=clock(client);scanner=str(uuid.uuid4())
    # Both candidates score well; the .08 gap passed the former .06 ambiguity rule.
    engine.vector=normalize(.75*vector() + .67*vector(1))
    engine.extract_group=lambda frame:response_faces([engine.vector])
    for _ in range(4):
        result=(scan_group if group else recognize)(client, headers, image, sid, scanner).json
        face=result['faces'][0] if group else result
        assert face['status']=='ambiguous' and 'person' not in face
        moment[0]+=.35
    no_attendance(client, sid)


@pytest.mark.parametrize('group', [False, True])
def test_replaying_identical_pixels_cannot_build_confirmation(setup, image, group):
    client, headers, engine, sid, moment, scanner=prepare(setup, image)
    engine.extract_group=lambda frame:response_faces([engine.vector])
    payload={'image':image, 'attendance_session_id':sid, 'scanner_id':scanner}
    for _ in range(6):
        result=client.post('/api/recognize/group' if group else '/api/recognize', json=payload, headers=headers).json
        face=result['faces'][0] if group else result
        assert face['attendance']['confirmations']==1
        assert not face['attendance']['newly_moved']
        moment[0]+=.35
    no_attendance(client, sid)


@pytest.mark.parametrize('group', [False, True])
def test_slow_inference_cannot_turn_a_late_name_into_attendance(setup, image, group):
    client, headers, engine, sid, moment, scanner=prepare(setup, image)
    original=engine.extract
    def slow(frame):
        moment[0]+=3.1
        return response_faces([engine.vector]) if group else original(frame)
    if group:engine.extract_group=slow
    else:engine.extract=slow
    for _ in range(3):
        result=(scan_group if group else recognize)(client, headers, image, sid, scanner).json
        face=result['faces'][0] if group else result
        assert face['attendance']['status']=='stale_frame'
    no_attendance(client, sid)


def test_identity_flicker_does_not_mark_either_student(setup, image):
    client, headers, engine, _=setup
    enroll(client, headers, image)
    engine.vector=vector(1);enroll(client, headers, image, name='Other student')
    sid=start(client,headers).json['session']['id'];moment=clock(client);scanner=str(uuid.uuid4())
    for i in range(8):
        engine.vector=vector(i%2)
        assert recognize(client,headers,image,sid,scanner).json['attendance']['confirmations']==1
        moment[0]+=.35
    no_attendance(client, sid)


@pytest.mark.parametrize('key,value', [('face_size_px',80), ('sharpness',8), ('detection_score',.8)])
def test_small_soft_or_uncertain_detections_cannot_mark_present(setup, image, key, value):
    client, headers, engine, sid, moment, scanner=prepare(setup, image)
    original=engine.analyze
    def analyze(frame):
        result=original(frame);result['quality'][key]=value;return result
    engine.analyze=analyze
    for _ in range(4):
        result=recognize(client,headers,image,sid,scanner).json
        assert result['attendance']['status']=='insufficient_evidence'
        moment[0]+=.35
    no_attendance(client, sid)


def test_same_name_jumping_to_a_different_face_restarts_confirmation(setup, image):
    client, headers, engine, sid, moment, scanner=prepare(setup, image)
    box=[.05,.1,.12,.2]
    def group(frame):
        result=response_faces([engine.vector]);result['faces'][0]['box']=box.copy();return result
    engine.extract_group=group
    for _ in range(2):scan_group(client,headers,image,sid,scanner);moment[0]+=.35
    box[0]=.8
    result=scan_group(client,headers,image,sid,scanner).json
    assert result['faces'][0]['attendance']['confirmations']==1
    no_attendance(client, sid)


def test_out_of_order_group_does_not_erase_or_complete_newer_confirmation(setup, image):
    client, headers, engine, sid, moment, scanner=prepare(setup, image)
    attendance=client.application.extensions['attendance']
    person=attendance.store.people()[0]
    evidence={'status':'matched','person':person,'similarity':1.,'candidate_count':1,'match_margin':None,
              'box':[.1,.1,.2,.2],'quality':{'face_size_px':100,'sharpness':20,'detection_score':.99}}
    def observe(frame, received, faces):
        return attendance.observe_group(sid, scanner, faces, frame_id=hashlib.sha256(frame.encode()).hexdigest(), received_at=received)
    assert observe('new',100,[evidence])[0]['confirmations']==1
    moment[0]+=.35
    # An older empty result must not reset the current identity's confirmation.
    assert observe('old',99,[])==[]
    assert observe('newer',100.35,[evidence])[0]['confirmations']==2
    assert observe('old matched',100,[evidence])[0]['status']=='stale_frame'
    no_attendance(client, sid)
    moment[0]+=.35
    assert observe('newest',moment[0],[evidence])[0]['newly_present']


def test_crossing_before_identity_confirmation_is_not_a_checkin(tmp_path):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    scan(.35)
    scan(.65)
    assert attendance.detail(sid)['movements']==[]
    assert scan(.7)['newly_moved']
    assert len(attendance.detail(sid)['movements'])==1


def test_mode_change_and_manual_departure_discard_camera_confirmation(tmp_path):
    attendance,sid,_,scan=movement_fixture(tmp_path)
    scan(.2,'check_in');scan(.2,'check_in');scan(.2,'check_in')
    rid=attendance.detail(sid)['roster'][0]['id']
    attendance.manual_movement(sid,rid,'check_out','Verified by reception')
    assert scan(.2,'check_in')['status']=='confirming'
    assert attendance.detail(sid)['movement_counts']['checked_out']==1
    assert scan(.2,'check_out')['confirmations']==1


def test_match_without_server_evidence_cannot_bypass_confirmation(setup, image):
    client, headers, _, sid, _, scanner=prepare(setup, image)
    attendance=client.application.extensions['attendance']
    person=attendance.store.people()[0]
    result=attendance.observe(sid, scanner, {'status':'matched','person':person}, frame_id='a'*64)
    assert result['status']=='insufficient_evidence'
    no_attendance(client, sid)
