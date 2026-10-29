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


@pytest.mark.parametrize('x,attendance,visible,expected', [
    (220, False, 36, 'too_small'),
    (180, True, 76, 'too_small'),
    (140, True, 116, None),
])
def test_face_size_counts_only_pixels_visible_in_frame(x, attendance, visible, expected):
    picture = np.random.default_rng(3).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    detected = face(120)[0].copy()
    detected[0] = x
    result, _ = engine(None)._quality(picture, detected, minimum_fraction=0, attendance=attendance)
    assert result['quality']['face_size_px'] == visible
    if expected:
        assert result['status'] == 'adjust' and result['code'] == expected
    else:
        assert result['status'] == 'ready'


@pytest.mark.parametrize('x,y', [(-200, 64), (64, -200), (300, 64), (64, 300)])
def test_face_box_outside_frame_cannot_use_unrelated_image_pixels(x, y):
    picture = np.random.default_rng(4).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    detected = face(120)[0].copy()
    detected[:2] = x, y
    result, _ = engine(None)._quality(picture, detected, minimum_fraction=0, attendance=True)
    assert result['status'] == 'adjust'


def test_group_detection_uses_the_full_resolution_pass_for_small_faces():
    detector = engine(None)
    picture = np.zeros((720,1280,3), np.uint8)
    calls = []
    small = face(100)
    def detect_at_size(image, side):
        calls.append(side)
        return small.copy()
    detector._detect_at_size = detect_at_size
    found = detector._detect(picture, max_side=960, far=True)
    assert calls == [960, 1280]
    assert len(found) == 1  # The same face in both passes is deduplicated.


@pytest.mark.parametrize('size,minimum_fraction', [(40, 0), (80, .2)])
def test_small_faces_skip_image_processing(monkeypatch, size, minimum_fraction):
    def unexpected_processing(*args, **kwargs):
        raise AssertionError('Undersized faces must skip image processing')
    for operation in ('resize', 'cvtColor', 'Laplacian'):
        monkeypatch.setattr(cv2, operation, unexpected_processing)
    picture = np.zeros((512, 512, 3), np.uint8)
    result, _ = engine(face(size))._quality(picture, face(size)[0], minimum_fraction=minimum_fraction)
    assert result['code'] == 'too_small'
    assert len(result['landmarks']) == 5
    assert result['quality']['sharpness'] == result['quality']['brightness'] == 0


@pytest.mark.parametrize('amplitude,attendance', [(4.5, False), (7.2, True)])
def test_color_crops_preserve_sharpness_cutoffs(amplitude, attendance):
    noise = np.random.default_rng(1).normal(0, amplitude, (300, 300, 3))
    picture = np.uint8(np.clip(128 + cv2.GaussianBlur(noise, (0, 0), 1), 0, 255))
    detected = face(300)[0].copy()
    detected[:2] = 0
    gray = cv2.cvtColor(cv2.resize(picture, (128, 128), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # Both crops pass their cutoff with resize first; grayscale first rejects them.
    assert sharpness >= (12 if attendance else 6)
    result, _ = engine(None)._quality(picture, detected, attendance=attendance)
    assert result['status'] == 'ready'
    assert result['quality']['sharpness'] == round(sharpness, 1)
    assert result['quality']['brightness'] == round(float(gray.mean()), 1)


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


def test_distant_detection_uses_full_camera_detail_but_never_upscales():
    detector = engine(None)
    calls = []
    def detect_at_size(image, side):
        calls.append(side)
        return np.empty((0,15), dtype=np.float32)
    detector._detect_at_size = detect_at_size
    detector._detect(np.zeros((1080,1920,3),np.uint8),max_side=960,far=True)
    assert calls == [960,1920]
    calls.clear()
    detector._detect(np.zeros((480,640,3),np.uint8),max_side=960,far=True)
    assert calls == [960]
