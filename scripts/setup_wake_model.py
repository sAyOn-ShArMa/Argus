"""Download and safely extract Argus's pinned offline Vosk wake model."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    models_root = project_root / "models"
    target = models_root / MODEL_NAME
    expected_file = target / "am" / "final.mdl"
    if expected_file.is_file():
        print(f"Wake model is already installed: {target}")
        return 0
    if target.exists():
        raise RuntimeError(
            f"An incomplete model folder already exists: {target}. "
            "Move it aside before retrying."
        )

    models_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="argus-wake-") as temporary:
        archive_path = Path(temporary) / f"{MODEL_NAME}.zip"
        digest = hashlib.sha256()
        print(f"Downloading {MODEL_URL}")
        with urlopen(MODEL_URL, timeout=60) as response, archive_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError("Wake-model download failed its SHA-256 check.")

        extraction_root = models_root.resolve()
        with ZipFile(archive_path) as archive:
            for member in archive.infolist():
                destination = (models_root / member.filename).resolve()
                if extraction_root != destination and extraction_root not in destination.parents:
                    raise RuntimeError(f"Unsafe path in model archive: {member.filename}")
            archive.extractall(models_root)

    if not expected_file.is_file():
        raise RuntimeError("The extracted wake model is incomplete.")
    print(f"Wake model installed: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
