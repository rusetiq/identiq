"""Download the local detector and research-only ArcFace recognition model."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
}
ARCFACE_MODEL = "arcface_w600k_r50.onnx"
ARCFACE_ARCHIVE = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"


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

    target_model = target / ARCFACE_MODEL
    if target_model.exists():
        print(f"Found {ARCFACE_MODEL}")
        return
    archive = target / "buffalo_l.zip.part"
    try:
        if archive.exists():
            print("Using the already downloaded buffalo_l archive …", flush=True)
        else:
            print("Downloading buffalo_l ArcFace weights for non-commercial academic research …", flush=True)
            with urllib.request.urlopen(ARCFACE_ARCHIVE, timeout=120) as source, archive.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
        with zipfile.ZipFile(archive) as contents:
            member = next((entry for entry in contents.infolist()
                           if Path(entry.filename).name == "w600k_r50.onnx" and not entry.is_dir()), None)
            if member is None:
                raise RuntimeError("The official buffalo_l archive did not contain w600k_r50.onnx.")
            if member.file_size < 10_000_000:
                raise RuntimeError("The ArcFace model file was unexpectedly small.")
            temporary = target_model.with_suffix(".part")
            with contents.open(member) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(target_model)
        print(f"Installed {ARCFACE_MODEL} ({target_model.stat().st_size // 1_000_000} MB)")
    finally:
        archive.unlink(missing_ok=True)
        target_model.with_suffix(".part").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
