"""Conservative recognition and automatic attendance defaults, enforced by the server.

These are similarity/quality cutoffs, not calibrated accuracy probabilities or
liveness checks. Change them only after evaluating the deployment's cameras.
"""
import math

MATCH_THRESHOLD = 0.65
MATCH_MARGIN = 0.12
CONFIRMATIONS = 3
MIN_INTERVAL = 0.25
MIN_DURATION = 0.60
MAX_GAP = 1.5
MAX_FRAME_AGE = 3.0
MAX_BUFFER_AGE = 1.5
MIN_FACE_PIXELS = 96
MIN_SHARPNESS = 12
MIN_DETECTION_SCORE = 0.85
LAST_SEEN_INTERVAL = 5.0


def quality_issue(quality):
    """Share the attendance cutoffs between inference and the write boundary."""
    checks = (
        ('face_size_px', MIN_FACE_PIXELS, 'too_small', 'Move closer so facial details are clear enough for attendance.'),
        ('sharpness', MIN_SHARPNESS, 'blurry', 'Hold still and check camera focus before attendance can be recorded.'),
        ('detection_score', MIN_DETECTION_SCORE, 'uncertain_detection', 'Look toward the camera for a clearer face detection.'),
    )
    if not isinstance(quality, dict):
        return {'code':'quality', 'message':'A clear camera capture is required for attendance.'}
    for key, minimum, code, message in checks:
        value = quality.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            return {'code':'quality', 'message':'A clear camera capture is required for attendance.'}
        if value < minimum:
            return {'code':code, 'message':message}
    return None
