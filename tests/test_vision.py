from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from argus.ai.provider import ToolCall
from argus.config import VisionConfig
from argus.tools import ToolRuntime
from argus.vision import (
    BoundingBox,
    GestureDetection,
    LocalVisionService,
    ObjectDetection,
    VisionResult,
    build_vision_tool_definitions,
)


class VisionResultTests(unittest.TestCase):
    def test_summary_reports_local_capabilities_and_no_identity_recognition(self) -> None:
        result = VisionResult(
            source="test.png",
            width=640,
            height=480,
            objects=(ObjectDetection("person", 0.91, BoundingBox(1, 2, 3, 4)),),
            qr_codes=("https://example.com",),
            face_boxes=(BoundingBox(10, 20, 30, 40),),
            gestures=(GestureDetection("Thumb_Up", 0.88, "Right"),),
        )

        summary = result.summary()
        payload = result.to_payload()

        self.assertIn("person (91%)", summary)
        self.assertIn("Thumb_Up", summary)
        self.assertIn("identity recognition is disabled", summary)
        self.assertFalse(payload["face_identity_recognition"])


class VisionToolTests(unittest.TestCase):
    class Analyzer:
        description = "local test analyzer"

        def __init__(self) -> None:
            self.camera_calls = 0
            self.paths: list[str] = []

        def analyze_image(self, path):
            self.paths.append(str(path))
            return VisionResult(str(path), 100, 100)

        def capture_and_analyze(self):
            self.camera_calls += 1
            return VisionResult("camera:0", 100, 100)

        def close(self):
            pass

    def test_denied_camera_tool_never_opens_camera(self) -> None:
        analyzer = self.Analyzer()
        runtime = ToolRuntime(
            build_vision_tool_definitions(analyzer),
            confirmer=lambda definition, arguments: False,
        )

        result = json.loads(
            runtime.execute(ToolCall("1", "inspect_camera_once", "{}"))
        )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(analyzer.camera_calls, 0)

    def test_confirmed_image_tool_returns_analysis_not_raw_pixels(self) -> None:
        analyzer = self.Analyzer()
        runtime = ToolRuntime(
            build_vision_tool_definitions(analyzer),
            confirmer=lambda definition, arguments: True,
        )

        result = json.loads(
            runtime.execute(
                ToolCall("1", "analyze_local_image", '{"path":"robot.png"}')
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(analyzer.paths, ["robot.png"])
        self.assertNotIn("pixels", json.dumps(result).casefold())


class LocalVisionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "approved"
        self.root.mkdir()
        self.config = VisionConfig(
            enabled=True,
            allowed_image_roots=(self.root.resolve(),),
            object_model_path=Path("object.tflite"),
            gesture_model_path=Path("gesture.task"),
            face_model_path=Path("face.tflite"),
            warmup_frames=2,
        )
        self.service = object.__new__(LocalVisionService)
        self.service._config = self.config

    def test_path_check_accepts_quoted_approved_image(self) -> None:
        image = self.root / "robot photo.png"
        image.write_bytes(b"fake")

        resolved = self.service._approved_image_path(f'"{image}"')

        self.assertEqual(resolved, image.resolve())

    def test_path_check_rejects_image_outside_approved_root(self) -> None:
        outside = Path(self.temporary.name) / "private.png"
        outside.write_bytes(b"fake")

        with self.assertRaises(PermissionError):
            self.service._approved_image_path(outside)

    def test_camera_is_released_after_one_frame_analysis(self) -> None:
        class Frame:
            size = 1

        class Capture:
            def __init__(self) -> None:
                self.released = False
                self.set_calls = 0

            def isOpened(self):
                return True

            def set(self, key, value):
                self.set_calls += 1

            def read(self):
                return True, Frame()

            def release(self):
                self.released = True

        capture = Capture()
        self.service._cv2 = SimpleNamespace(
            VideoCapture=lambda index, *args: capture,
            CAP_PROP_FRAME_WIDTH=1,
            CAP_PROP_FRAME_HEIGHT=2,
        )
        expected = VisionResult("camera:0", 1, 1)
        self.service._analyze_frame = lambda frame, source: expected

        result = self.service.capture_and_analyze()

        self.assertIs(result, expected)
        self.assertTrue(capture.released)
        self.assertEqual(capture.set_calls, 2)

    def test_camera_falls_back_to_another_index(self) -> None:
        class Frame:
            size = 1

        class Capture:
            def __init__(self, opened: bool) -> None:
                self.opened = opened
                self.released = False

            def isOpened(self):
                return self.opened

            def set(self, key, value):
                return True

            def read(self):
                return True, Frame()

            def release(self):
                self.released = True

        captures: list[Capture] = []

        def video_capture(index, *args):
            capture = Capture(opened=index == 1)
            captures.append(capture)
            return capture

        self.service._cv2 = SimpleNamespace(
            VideoCapture=video_capture,
            CAP_PROP_FRAME_WIDTH=1,
            CAP_PROP_FRAME_HEIGHT=2,
        )
        expected = VisionResult("camera:1", 1, 1)
        self.service._analyze_frame = lambda frame, source: VisionResult(source, 1, 1)

        result = self.service.capture_and_analyze()

        self.assertEqual(result, expected)
        self.assertGreaterEqual(len(captures), 2)
        self.assertTrue(all(capture.released for capture in captures))

    def test_qr_decoder_reads_a_synthetic_code(self) -> None:
        import cv2

        code = cv2.QRCodeEncoder_create().encode("ARGUS-TIER-6")
        code = cv2.resize(code, (500, 500), interpolation=cv2.INTER_NEAREST)
        self.service._qr_detector = cv2.QRCodeDetector()

        values = self.service._qr_codes(code)

        self.assertEqual(values, ("ARGUS-TIER-6",))


if __name__ == "__main__":
    unittest.main()
