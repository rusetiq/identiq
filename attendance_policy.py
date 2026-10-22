"""Conservative recognition and automatic attendance defaults, enforced by the server.

These are similarity/quality cutoffs, not calibrated accuracy probabilities or
liveness checks. Change them only after evaluating the deployment's cameras.
"""
MATCH_THRESHOLD = 0.60
MATCH_MARGIN = 0.12
CONFIRMATIONS = 3
MIN_INTERVAL = 0.25
MIN_DURATION = 0.60
MAX_GAP = 1.5
MAX_FRAME_AGE = 3.0
MIN_FACE_PIXELS = 96
MIN_SHARPNESS = 12
MIN_DETECTION_SCORE = 0.85
