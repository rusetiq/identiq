"""Versioned iPhone capture validation. Summary data is untrusted, not attestation."""
from datetime import datetime
import math

from biometrics import FaceError, SAMPLES

POSES = {"front": 0.0, "left": -0.15, "right": 0.15, "left_more": -0.30, "right_more": 0.30}


def valid_name(name):
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80) or any(ord(c) < 32 for c in name):
        raise FaceError("invalid_name", "Enter a name between 1 and 80 characters.")
    return name.strip()


def number(value, minimum, maximum, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise FaceError("invalid_capture", f"Invalid {label} in the phone capture.")
    return value


def validate_capture(bundle):
    if not isinstance(bundle, dict) or type(bundle.get("version")) is not int or bundle.get("version") != 1 or bundle.get("format") != "identiq.capture":
        raise FaceError("invalid_capture", "Choose an Identiq iPhone capture file (version 1).")
    if bundle.get("sensor") != "truedepth" or bundle.get("consent") is not True:
        raise FaceError("invalid_capture", "The capture must use TrueDepth and include permission to enroll.")
    name = valid_name(bundle.get("name"))
    device = bundle.get("device")
    if not isinstance(device, str) or not 1 <= len(device) <= 100 or any(ord(c) < 32 for c in device):
        raise FaceError("invalid_capture", "The capture has invalid device information.")
    timestamp = bundle.get("captured_at")
    try:
        if not isinstance(timestamp, str) or len(timestamp) > 40:
            raise ValueError()
        date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise FaceError("invalid_capture", "The capture needs a valid timestamp with a timezone.") from exc
    samples = bundle.get("samples")
    if not isinstance(samples, list) or len(samples) != SAMPLES:
        raise FaceError("invalid_capture", "The phone capture must include exactly five face samples.")
    summaries, images, seen = [], [], set()
    for sample in samples:
        if not isinstance(sample, dict):
            raise FaceError("invalid_capture", "Invalid phone sample.")
        pose = sample.get("pose")
        if not isinstance(pose, str) or pose not in POSES or pose in seen:
            raise FaceError("invalid_capture", "Capture all five different guided poses.")
        seen.add(pose)
        yaw = number(sample.get("yaw_radians"), -0.6, 0.6, "head angle")
        if abs(yaw - POSES[pose]) > 0.14:
            raise FaceError("invalid_capture", "A phone sample does not match its guided pose.")
        depth = sample.get("depth")
        if not isinstance(depth, dict):
            raise FaceError("invalid_capture", "A phone sample is missing depth measurements.")
        cleaned = {
            "valid_fraction": number(depth.get("valid_fraction"), 0.65, 1, "valid depth fraction"),
            "median_m": number(depth.get("median_m"), 0.2, 0.85, "face distance"),
            "spread_m": number(depth.get("spread_m"), 0.004, 0.18, "depth spread"),
        }
        images.append(sample.get("image"))
        summaries.append({"pose": pose, "yaw_radians": yaw, "depth": cleaned})
    return name, images, {"version": 1, "sensor": "truedepth", "device": device, "captured_at": timestamp, "samples": summaries}
