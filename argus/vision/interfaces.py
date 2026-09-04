"""Provider-independent result types for Argus vision."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ObjectDetection:
    label: str
    confidence: float
    box: BoundingBox


@dataclass(frozen=True, slots=True)
class GestureDetection:
    label: str
    confidence: float
    handedness: str | None


@dataclass(frozen=True, slots=True)
class VisionResult:
    source: str
    width: int
    height: int
    objects: tuple[ObjectDetection, ...] = ()
    qr_codes: tuple[str, ...] = ()
    face_boxes: tuple[BoundingBox, ...] = ()
    gestures: tuple[GestureDetection, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["face_count"] = len(self.face_boxes)
        payload["face_identity_recognition"] = False
        return payload

    def summary(self) -> str:
        parts = [f"Local analysis of {self.width}x{self.height} image."]
        if self.objects:
            objects = ", ".join(
                f"{item.label} ({item.confidence:.0%})" for item in self.objects
            )
            parts.append(f"Objects: {objects}.")
        else:
            parts.append("No supported objects detected above the confidence threshold.")
        if self.qr_codes:
            values = ", ".join(
                repr(" ".join(value.split())[:300]) for value in self.qr_codes
            )
            parts.append(f"QR content: {values}.")
        else:
            parts.append("No readable QR code detected.")
        face_count = len(self.face_boxes)
        parts.append(
            f"Anonymous faces detected: {face_count}; identity recognition is disabled."
        )
        if self.gestures:
            gestures = ", ".join(
                f"{item.label} ({item.confidence:.0%}"
                f"{', ' + item.handedness if item.handedness else ''})"
                for item in self.gestures
            )
            parts.append(f"Gestures: {gestures}.")
        else:
            parts.append("No supported hand gesture detected.")
        return " ".join(parts)


class VisionAnalyzer(Protocol):
    @property
    def description(self) -> str:
        """Describe the local backend and privacy behavior."""

    def analyze_image(self, path: str | Path) -> VisionResult:
        """Analyze one approved local image without uploading it."""

    def capture_and_analyze(self) -> VisionResult:
        """Capture and locally analyze exactly one webcam frame."""

    def close(self) -> None:
        """Release local model resources."""
