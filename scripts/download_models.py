"""Download official OpenCV Zoo models and verify their Git LFS SHA-256 hashes."""
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": (
        "face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    target = ROOT / "models"
    target.mkdir(exist_ok=True)
    for name, (folder, checksum) in MODELS.items():
        path = target / name
        if path.exists() and digest(path) == checksum:
            print(f"Verified {name}")
            continue
        url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{folder}/{name}"
        temporary = path.with_suffix(".part")
        print(f"Downloading {name} …", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            if digest(temporary) != checksum:
                raise RuntimeError(f"Checksum mismatch for {name}; download was rejected.")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Verified {name}")


if __name__ == "__main__":
    main()
