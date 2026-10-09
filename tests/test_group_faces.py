import uuid

import numpy as np
import pytest

from biometrics import FaceError, MAX_FACES
from test_app import setup, image, vector, enroll
from test_attendance import start, clock
from test_tracking import engine, face, smooth_image


class Recognizer:
    def alignCrop(self, image, detected):
        return int(detected[0])
    def feature(self, x):
        return vector(0 if x < 150 else 1)


def group_engine():
    second=face().copy();second[:,[0,4,6,8,10,12]]+=180
    result=engine(np.concatenate([face(),second]));result.recognizer=Recognizer()
    return result


def test_all_faces_receive_independent_quality_and_embeddings():
    detector=group_engine();result=detector.extract_group(smooth_image())
    assert result['face_count']==2 and result['ready_count']==2
    assert result['faces'][0]['box']!=result['faces'][1]['box']
    assert len(result['faces'][0]['landmarks'])==5
    assert np.dot(result['faces'][0]['embedding'],result['faces'][1]['embedding'])==0
    assert 'embedding' not in str(detector.analyze_group(smooth_image()))
    with pytest.raises(FaceError) as error:detector.extract(smooth_image())
    assert error.value.code=='multiple_faces'


def test_one_bad_face_does_not_block_a_good_face():
    detector=group_engine();detector.detector.faces[1,2:4]=40
    result=detector.extract_group(smooth_image())
    assert result['status']=='ready' and result['ready_count']==1
    assert result['faces'][0]['status']=='ready' and result['faces'][1]['code']=='too_small'
    assert 'embedding' not in result['faces'][1]


def test_empty_and_over_capacity_groups_do_not_extract():
    assert engine(None).extract_group(smooth_image())['faces']==[]
    result=engine(np.repeat(face(),MAX_FACES+1,axis=0)).extract_group(smooth_image())
    assert result['code']=='too_many_faces' and result['faces']==[]


def response_faces(vectors):
    return {'status':'ready' if vectors else 'adjust','face_count':len(vectors),'ready_count':len(vectors),
            'message':'Test group','faces':[{'status':'ready','embedding':v,'box':[i*.4,.1,.2,.4],
            'landmarks':[[.1,.1]]*5,'quality':{'face_size_px':100}} for i,v in enumerate(vectors)]}


def scan_group(client,headers,image,sid,scanner):
    return client.post('/api/recognize/group',json={'image':image,'attendance_session_id':sid,'scanner_id':scanner},headers=headers)


def prepare(setup,image):
    client,headers,detector,_=setup
    enroll(client,headers,image,name='Alex');detector.vector=vector(1);enroll(client,headers,image,name='Taylor')
    sid=start(client,headers).json['session']['id'];moment=clock(client);scanner=str(uuid.uuid4())
    return client,headers,detector,sid,moment,scanner


def test_group_first_match_checks_in_each_identity_when_order_changes(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    for count in (1,2,3):
        order=[vector(),vector(1)] if count%2 else [vector(1),vector()]
        detector.extract_group=lambda frame:response_faces(order)
        result=scan_group(client,headers,image,sid,scanner).json
        assert all(face['attendance']['confirmations']==1 for face in result['faces'])
        assert all('embedding' not in face for face in result['faces'])
        assert all(face['attendance']['newly_present']==(count==1) for face in result['faces'])
        moment[0]+=.7
    detail=client.get(f'/api/attendance/sessions/{sid}').json['session']
    assert detail['counts']['present']==2 and all(p['first_seen_at'] for p in detail['roster'])
    repeated=scan_group(client,headers,image,sid,scanner).json
    assert all(not face['attendance']['newly_present'] for face in repeated['faces'])


def test_missing_face_keeps_first_match_attendance(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    detector.extract_group=lambda frame:response_faces([vector(),vector(1)])
    scan_group(client,headers,image,sid,scanner);moment[0]+=.7
    detector.extract_group=lambda frame:response_faces([vector()])
    scan_group(client,headers,image,sid,scanner);moment[0]+=.7
    detector.extract_group=lambda frame:response_faces([vector(),vector(1)])
    result=scan_group(client,headers,image,sid,scanner).json['faces']
    assert result[0]['attendance']['status']=='present'
    assert result[1]['attendance']['confirmations']==1


def test_duplicate_identity_faces_mark_neither_and_reset_previous_streak(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    detector.extract_group=lambda frame:response_faces([vector()])
    detector.extract_group=lambda frame:response_faces([vector(),vector(),vector(1)])
    for _ in range(3):
        result=scan_group(client,headers,image,sid,scanner).json['faces'];moment[0]+=.7
        assert all(face['status']=='ambiguous' and face['attendance']['status']=='unrecognized' for face in result[:2])
    detail=client.get(f'/api/attendance/sessions/{sid}').json['session']
    assert detail['counts']['present']==1
    detector.extract_group=lambda frame:response_faces([vector()])
    assert scan_group(client,headers,image,sid,scanner).json['faces'][0]['attendance']['confirmations']==1


def test_unknown_face_does_not_block_matching_person(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    detector.extract_group=lambda frame:response_faces([vector(),vector(3)])
    for _ in range(3):result=scan_group(client,headers,image,sid,scanner).json;moment[0]+=.7
    assert result['faces'][0]['attendance']['status']=='present'
    assert result['faces'][1]['status']=='unknown' and result['faces'][1]['attendance']['status']=='unrecognized'


def test_group_quality_analysis_and_selected_person_guard(setup,image):
    client,headers,detector,_,_,_=prepare(setup,image)
    detector.analyze_group=lambda frame:{'status':'ready','face_count':2,'faces':[]}
    assert client.post('/api/analyze',json={'image':image,'group':True},headers=headers).json['face_count']==2
    assert client.post('/api/analyze',json={'image':image,'group':'yes'},headers=headers).status_code==422
    assert client.post('/api/recognize/group',json={'image':image,'person_id':1},headers=headers).status_code==422
    assert client.application.test_client().post('/api/recognize/group',json={},headers=headers).status_code==401


def test_group_empty_invalid_and_separate_scanners_do_not_accumulate(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    detector.extract_group=lambda frame:response_faces([vector()])
    scan_group(client,headers,image,sid,scanner);moment[0]+=.7
    other=str(uuid.uuid4());scan_group(client,headers,image,sid,other);moment[0]+=.7
    assert scan_group(client,headers,image,sid,scanner).json['faces'][0]['attendance']['confirmations']==1
    moment[0]+=.7
    invalid=client.post('/api/recognize/group',json={'image':'invalid','attendance_session_id':sid,'scanner_id':scanner},headers=headers)
    assert invalid.status_code==422
    assert scan_group(client,headers,image,sid,scanner).json['faces'][0]['attendance']['confirmations']==1
    detector.extract_group=lambda frame:response_faces([])
    assert scan_group(client,headers,image,sid,scanner).json['faces']==[]
    moment[0]+=.7;detector.extract_group=lambda frame:response_faces([vector()])
    assert scan_group(client,headers,image,sid,scanner).json['faces'][0]['attendance']['confirmations']==1


def test_group_cannot_overwrite_manual_correction_or_reopen_session(setup,image):
    client,headers,detector,sid,moment,scanner=prepare(setup,image)
    roster=client.get(f'/api/attendance/sessions/{sid}').json['session']['roster']
    client.patch(f"/api/attendance/sessions/{sid}/records/{roster[0]['id']}",json={'status':'absent'},headers=headers)
    detector.extract_group=lambda frame:response_faces([vector(),vector(1)])
    for _ in range(3):result=scan_group(client,headers,image,sid,scanner).json;moment[0]+=.7
    assert result['faces'][0]['attendance']['status']=='manual_override'
    saved=client.post(f'/api/attendance/sessions/{sid}/finish',json={},headers=headers).json['session']
    assert saved['counts']['present']==1 and saved['counts']['absent']==1
    assert scan_group(client,headers,image,sid,scanner).json['code']=='session_closed'
