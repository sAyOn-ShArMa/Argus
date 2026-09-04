"""Download Argus's pinned official MediaPipe vision models safely."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen


MODELS = (
    (
        "blaze_face_short_range.tflite",
        "https://storage.googleapis.com/mediapipe-models/face_detector/"
        "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
        "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f",
    ),
    (
        "efficientdet_lite0.tflite",
        "https://storage.googleapis.com/mediapipe-models/object_detector/"
        "efficientdet_lite0/int8/latest/efficientdet_lite0.tflite",
        "0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb",
    ),
    (
        "gesture_recognizer.task",
        "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
        "gesture_recognizer/float16/latest/gesture_recognizer.task",
        "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    models_root = project_root / "models" / "vision"
    models_root.mkdir(parents=True, exist_ok=True)

    for filename, url, expected_hash in MODELS:
        target = models_root / filename
        if target.is_file():
            if _sha256(target) != expected_hash:
                raise RuntimeError(
                    f"Existing vision model failed its SHA-256 check: {target}"
                )
            print(f"Vision model is already installed: {target}")
            continue
        if target.exists():
            raise RuntimeError(f"Vision model target is not a normal file: {target}")

        with tempfile.TemporaryDirectory(prefix="argus-vision-") as temporary:
            download = Path(temporary) / filename
            digest = hashlib.sha256()
            print(f"Downloading {url}")
            with urlopen(url, timeout=60) as response, download.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected_hash:
                raise RuntimeError(
                    f"Vision-model download failed its SHA-256 check: {filename}"
                )
            download.replace(target)
        print(f"Vision model installed: {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
