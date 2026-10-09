import threading

import cv2
import numpy as np
import pytest

from biometrics import FaceEngine, FaceError
from test_app import setup, image


class Detector:
    def __init__(self, faces): self.faces = faces
    def setInputSize(self, size): self.size = size
    def detect(self, image): return None, self.faces


def engine(faces):
    result = FaceEngine.__new__(FaceEngine)
    result.lock = threading.Lock()
    result.detector = Detector(faces)
    return result


def face(size=256):
    return np.array([[64,64,size,size,128,128,230,128,180,180,140,230,220,230,.9]], np.float32)


def smooth_image():
    # A low-frequency face-region fixture: raw variance is below the old limit,
    # but normalized features are sharp enough. It contains no person's face.
    x,y=np.meshgrid(np.arange(512),np.arange(512))
    gray=np.uint8(128+40*np.sin(x*.2)+30*np.sin(y*.2))
    return cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)


def test_clear_smooth_frames_are_not_rejected_by_old_raw_threshold():
    image = smooth_image()
    crop = image[64:320,64:320]
    old_score = cv2.Laplacian(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()
    assert old_score < 35
    result = engine(face()).analyze(image)
    assert result["status"] == "ready"
    assert result["quality"]["sharpness"] >= 6
    assert len(result["landmarks"]) == 5


def test_detected_but_blurred_face_keeps_landmarks_and_exact_reason():
    image = np.full((512,512,3),128,np.uint8)
    tracker = engine(face())
    result = tracker.analyze(image)
    assert result["code"] == "blurry"
    assert result["face_count"] == 1 and len(result["landmarks"]) == 5
    assert result["quality"]["sharpness"] == 0
    with pytest.raises(FaceError) as error:
        tracker.extract(image)
    assert error.value.code == "blurry"


def test_small_face_reports_position_instead_of_lighting():
    result = engine(face(40)).analyze(smooth_image())
    assert result["code"] == "too_small"
    assert "Move closer" in result["message"]
    assert "box" in result and len(result["landmarks"]) == 5


def test_multiple_faces_never_become_capture_ready():
    result = engine(np.concatenate([face(),face()])).analyze(smooth_image())
    assert result["code"] == "multiple_faces"
    assert result["face_count"] == 2 and len(result["boxes"]) == 2


def test_no_face_gives_no_false_landmarks():
    result = engine(None).analyze(smooth_image())
    assert result["code"] == "no_face"
    assert "landmarks" not in result


def test_analyze_route_exposes_quality_and_landmarks(setup,image):
    client,headers,_,_=setup
    result=client.post('/api/analyze',json={"image":image},headers=headers)
    assert result.status_code == 200
    assert result.json["status"] == "ready"
    assert len(result.json["landmarks"]) == 5
    assert "sharpness" in result.json["quality"]


@pytest.mark.parametrize('code',['no_face','multiple_faces','blurry','too_small'])
def test_analyze_quality_warnings_are_trackable_responses(setup,image,code):
    client,headers,detector,_=setup
    detector.error=FaceError(code,'Specific issue')
    response=client.post('/api/analyze',json={"image":image},headers=headers)
    assert response.status_code == 200
    assert response.json["status"] == "adjust" and response.json["code"] == code
