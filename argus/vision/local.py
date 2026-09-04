"""OpenCV and MediaPipe implementation of private, local vision analysis."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from argus.config import VisionConfig
from argus.vision.interfaces import (
    BoundingBox,
    GestureDetection,
    ObjectDetection,
    VisionResult,
)


class VisionError(RuntimeError):
    """A local image or camera operation could not be completed."""


class VisionUnavailable(VisionError):
    """The configured local vision backend is not ready."""


@contextmanager
def _quiet_native_stderr():
    """Hide known MediaPipe C++ startup chatter while preserving exceptions."""

    saved_descriptor: int | None = None
    try:
        sys.stderr.flush()
        saved_descriptor = os.dup(2)
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), 2)
            yield
    finally:
        if saved_descriptor is not None:
            os.dup2(saved_descriptor, 2)
            os.close(saved_descriptor)


class LocalVisionService:
    """Analyze one in-memory frame at a time with no cloud image upload."""

    _ALLOWED_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}
    _MAX_IMAGE_BYTES = 25 * 1024 * 1024

    def __init__(
        self,
        config: VisionConfig,
        *,
        cv2_module: Any | None = None,
        mediapipe_module: Any | None = None,
    ) -> None:
        if not config.enabled:
            raise VisionUnavailable("Local vision is disabled in the configuration.")
        if config.object_model_path is None or config.gesture_model_path is None:
            raise VisionUnavailable("Vision model paths are not configured.")
        required_paths = [config.object_model_path, config.gesture_model_path]
        if config.detect_faces:
            if config.face_model_path is None:
                raise VisionUnavailable("Anonymous face model path is not configured.")
            required_paths.append(config.face_model_path)
        for path in required_paths:
            if not path.is_file():
                raise VisionUnavailable(
                    f"Vision model is missing: {path}. Run scripts/setup_vision_models.py."
                )

        try:
            if cv2_module is None:
                import cv2 as cv2_module
            if mediapipe_module is None:
                cache = Path(tempfile.gettempdir()) / "argus-matplotlib-cache"
                cache.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("MPLCONFIGDIR", str(cache))
                os.environ.setdefault("GLOG_minloglevel", "2")
                os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
                import mediapipe as mediapipe_module
        except ImportError as error:
            raise VisionUnavailable(
                "OpenCV or MediaPipe is not installed. Run: python -m pip install -e ."
            ) from error

        self._config = config
        self._cv2 = cv2_module
        self._mp = mediapipe_module
        self._closed = False
        try:
            with _quiet_native_stderr():
                self._qr_detector = self._cv2.QRCodeDetector()
                object_options = self._mp.tasks.vision.ObjectDetectorOptions(
                    base_options=self._mp.tasks.BaseOptions(
                        model_asset_path=str(config.object_model_path)
                    ),
                    running_mode=self._mp.tasks.vision.RunningMode.IMAGE,
                    max_results=config.max_results,
                    score_threshold=config.object_score_threshold,
                )
                self._object_detector = (
                    self._mp.tasks.vision.ObjectDetector.create_from_options(
                        object_options
                    )
                )
                gesture_options = self._mp.tasks.vision.GestureRecognizerOptions(
                    base_options=self._mp.tasks.BaseOptions(
                        model_asset_path=str(config.gesture_model_path)
                    ),
                    running_mode=self._mp.tasks.vision.RunningMode.IMAGE,
                    num_hands=2,
                )
                self._gesture_detector = (
                    self._mp.tasks.vision.GestureRecognizer.create_from_options(
                        gesture_options
                    )
                )
                self._face_detector = None
                if config.detect_faces:
                    assert config.face_model_path is not None
                    face_options = self._mp.tasks.vision.FaceDetectorOptions(
                        base_options=self._mp.tasks.BaseOptions(
                            model_asset_path=str(config.face_model_path)
                        ),
                        running_mode=self._mp.tasks.vision.RunningMode.IMAGE,
                        min_detection_confidence=0.5,
                    )
                    self._face_detector = (
                        self._mp.tasks.vision.FaceDetector.create_from_options(
                            face_options
                        )
                    )
        except VisionError:
            raise
        except Exception as error:
            raise VisionUnavailable(
                f"Could not initialize local vision models: {error}"
            ) from error
        atexit.register(self.close)

    @property
    def description(self) -> str:
        return (
            "OpenCV + MediaPipe, local one-frame analysis; frames are not saved "
            "and face identity recognition is disabled"
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for detector_name in (
            "_object_detector",
            "_gesture_detector",
            "_face_detector",
        ):
            detector = getattr(self, detector_name, None)
            if detector is not None:
                try:
                    detector.close()
                except Exception:
                    pass

    def _approved_image_path(self, value: str | Path) -> Path:
        text = str(value).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1]
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise VisionError(f"Image file was not found: {candidate}") from error
        if not resolved.is_file():
            raise VisionError(f"Image path is not a file: {resolved}")
        if not any(
            resolved == root or root in resolved.parents
            for root in self._config.allowed_image_roots
        ):
            raise PermissionError(
                "Image is outside the folders approved in vision.allowed_image_roots."
            )
        if resolved.suffix.casefold() not in self._ALLOWED_EXTENSIONS:
            raise VisionError("Supported image formats are BMP, JPEG, PNG, and WebP.")
        try:
            size = resolved.stat().st_size
        except OSError as error:
            raise VisionError(f"Could not inspect image file: {error}") from error
        if size > self._MAX_IMAGE_BYTES:
            raise VisionError("Image exceeds the 25 MB local-analysis limit.")
        return resolved

    def analyze_image(self, path: str | Path) -> VisionResult:
        resolved = self._approved_image_path(path)
        frame = self._cv2.imread(str(resolved), self._cv2.IMREAD_COLOR)
        if frame is None or getattr(frame, "size", 0) == 0:
            raise VisionError("OpenCV could not decode that image.")
        return self._analyze_frame(frame, source=resolved.name)

    def capture_and_analyze(self) -> VisionResult:
        configured_index = self._config.camera_index
        indexes = tuple(dict.fromkeys((configured_index, 0, 1, 2, 3)))
        backends: list[int | None] = [None]
        for name in ("CAP_MSMF", "CAP_DSHOW"):
            backend = getattr(self._cv2, name, None)
            if backend is not None and backend not in backends:
                backends.append(backend)

        for index in indexes:
            for backend in backends:
                if backend is None:
                    capture = self._cv2.VideoCapture(index)
                else:
                    capture = self._cv2.VideoCapture(index, backend)
                try:
                    if not capture.isOpened():
                        continue
                    capture.set(
                        self._cv2.CAP_PROP_FRAME_WIDTH,
                        self._config.capture_width,
                    )
                    capture.set(
                        self._cv2.CAP_PROP_FRAME_HEIGHT,
                        self._config.capture_height,
                    )
                    frame = None
                    for _ in range(self._config.warmup_frames):
                        success, candidate = capture.read()
                        if (
                            success
                            and candidate is not None
                            and getattr(candidate, "size", 0)
                        ):
                            frame = candidate
                    if frame is not None:
                        return self._analyze_frame(frame, source=f"camera:{index}")
                finally:
                    capture.release()

        raise VisionError(
            "No usable camera frame was available. Close other camera apps; in "
            "Windows Settings > Privacy & security > Camera, enable Camera access "
            "and Let desktop apps access your camera; then check the laptop's "
            "camera shutter or camera-disable key."
        )

    def _qr_codes(self, frame: Any) -> tuple[str, ...]:
        codes: list[str] = []
        try:
            detected, decoded, _, _ = self._qr_detector.detectAndDecodeMulti(frame)
            if detected:
                codes.extend(value for value in decoded if value)
        except (AttributeError, ValueError):
            pass
        if not codes:
            value, _, _ = self._qr_detector.detectAndDecode(frame)
            if value:
                codes.append(value)
        return tuple(dict.fromkeys(codes))

    def _faces(self, image: Any) -> tuple[BoundingBox, ...]:
        if not self._config.detect_faces or self._face_detector is None:
            return ()
        result = self._face_detector.detect(image)
        boxes: list[BoundingBox] = []
        for detection in result.detections:
            box = detection.bounding_box
            boxes.append(
                BoundingBox(
                    int(box.origin_x),
                    int(box.origin_y),
                    int(box.width),
                    int(box.height),
                )
            )
        return tuple(boxes)

    def _media_image(self, frame: Any) -> Any:
        rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

    def _objects(self, image: Any) -> tuple[ObjectDetection, ...]:
        result = self._object_detector.detect(image)
        values: list[ObjectDetection] = []
        for detection in result.detections:
            if not detection.categories:
                continue
            category = max(detection.categories, key=lambda item: item.score or 0.0)
            label = category.category_name or category.display_name or "unknown"
            score = float(category.score or 0.0)
            if score < self._config.object_score_threshold:
                continue
            box = detection.bounding_box
            values.append(
                ObjectDetection(
                    label=label,
                    confidence=score,
                    box=BoundingBox(
                        int(box.origin_x),
                        int(box.origin_y),
                        int(box.width),
                        int(box.height),
                    ),
                )
            )
        values.sort(key=lambda item: item.confidence, reverse=True)
        return tuple(values[: self._config.max_results])

    def _gestures(self, image: Any) -> tuple[GestureDetection, ...]:
        result = self._gesture_detector.recognize(image)
        values: list[GestureDetection] = []
        for index, categories in enumerate(result.gestures):
            if not categories:
                continue
            category = max(categories, key=lambda item: item.score or 0.0)
            label = category.category_name or category.display_name or "Unknown"
            score = float(category.score or 0.0)
            if label in {"None", "Unknown"} or score < self._config.gesture_score_threshold:
                continue
            handedness = None
            if index < len(result.handedness) and result.handedness[index]:
                hand = result.handedness[index][0]
                handedness = hand.category_name or hand.display_name or None
            values.append(GestureDetection(label, score, handedness))
        return tuple(values)

    def _analyze_frame(self, frame: Any, *, source: str) -> VisionResult:
        try:
            height, width = frame.shape[:2]
            media_image = self._media_image(frame)
            with _quiet_native_stderr():
                objects = self._objects(media_image)
                faces = self._faces(media_image)
                gestures = self._gestures(media_image)
            return VisionResult(
                source=source,
                width=int(width),
                height=int(height),
                objects=objects,
                qr_codes=self._qr_codes(frame),
                face_boxes=faces,
                gestures=gestures,
            )
        except VisionError:
            raise
        except Exception as error:
            raise VisionError(f"Local image analysis failed: {error}") from error
