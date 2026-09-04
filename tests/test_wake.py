from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import wave

from argus.voice import NoSpeechDetected
from argus.wake.interfaces import WakeError, WakeModeSession
from argus.wake.vosk_detector import (
    VoskCommandRecorder,
    VoskWakeDetector,
    _contains_phrase,
)


class FakeInputStream:
    def __init__(self, callback, **kwargs) -> None:
        self.callback = callback
        self.kwargs = kwargs

    def __enter__(self):
        self.callback(b"audio", 5, None, None)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeSoundDevice:
    def RawInputStream(self, **kwargs):
        return FakeInputStream(**kwargs)


class FakeRecognizer:
    def AcceptWaveform(self, data: bytes) -> bool:
        return False

    def PartialResult(self) -> str:
        return json.dumps({"partial": "argus"})


class FakeVosk:
    def __init__(self) -> None:
        self.log_level = None
        self.grammar = None

    def SetLogLevel(self, level: int) -> None:
        self.log_level = level

    def Model(self, path: str):
        return SimpleNamespace(path=path)

    def KaldiRecognizer(self, model, sample_rate, grammar):
        self.grammar = json.loads(grammar)
        return FakeRecognizer()


class WakeDetectorTests(unittest.TestCase):
    def test_phrase_matching_uses_word_boundaries(self) -> None:
        self.assertTrue(_contains_phrase("hello argus", ("argus",)))
        self.assertFalse(_contains_phrase("argusian", ("argus",)))
        self.assertTrue(
            _contains_phrase(
                "wake up argus i am back",
                ("wake up argus i am back",),
            )
        )
        self.assertFalse(
            _contains_phrase("wake up argus", ("wake up argus i am back",))
        )

    def test_detects_argus_locally_with_restricted_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            vosk = FakeVosk()
            detector = VoskWakeDetector(
                model_path=Path(folder),
                sample_rate=16_000,
                phrases=("argus", "august"),
                sounddevice_module=FakeSoundDevice(),
                vosk_module=vosk,
            )

            detector.wait()

        self.assertEqual(vosk.log_level, -1)
        self.assertEqual(vosk.grammar, ["argus", "august", "[unk]"])

    def test_missing_local_model_fails_before_opening_microphone(self) -> None:
        with self.assertRaisesRegex(WakeError, "Wake model not found"):
            VoskWakeDetector(
                model_path=Path("definitely-missing-model"),
                sample_rate=16_000,
                phrases=("argus",),
                sounddevice_module=FakeSoundDevice(),
                vosk_module=FakeVosk(),
            )


class WakeSessionTests(unittest.TestCase):
    def test_composes_detection_acknowledgement_and_command_capture(self) -> None:
        detector = Mock(wait=Mock(return_value=None))
        recorder = Mock(record=Mock(return_value=b"command"))
        transcriber = Mock(transcribe=Mock(return_value="open notepad"))
        synthesizer = Mock(speak=Mock(return_value=None))
        session = WakeModeSession(
            detector,
            recorder,
            transcriber,
            synthesizer,
            phrase="argus",
            acknowledgement="Yes, sir?",
        )

        session.wait()
        session.acknowledge()
        transcript = session.listen_for_command()
        session.speak("Opening it.")

        detector.wait.assert_called_once()
        self.assertEqual(transcript, "open notepad")
        self.assertEqual(
            [call.args[0] for call in synthesizer.speak.call_args_list],
            ["Yes, sir?", "Opening it."],
        )


class CommandRecognizer:
    def __init__(self, *, endpoint: bool, text: str) -> None:
        self.endpoint = endpoint
        self.text = text

    def AcceptWaveform(self, data: bytes) -> bool:
        return self.endpoint

    def Result(self) -> str:
        return json.dumps({"text": self.text})

    def PartialResult(self) -> str:
        return json.dumps({"partial": self.text})


class CommandVosk:
    def __init__(self, recognizer: CommandRecognizer) -> None:
        self.recognizer = recognizer

    def KaldiRecognizer(self, model, sample_rate):
        return self.recognizer


class CommandInputStream:
    def __init__(self, callback, chunk: bytes, **kwargs) -> None:
        self.callback = callback
        self.chunk = chunk

    def __enter__(self):
        self.callback(self.chunk, len(self.chunk) // 2, None, None)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class CommandSoundDevice:
    def __init__(self, chunk: bytes) -> None:
        self.chunk = chunk

    def RawInputStream(self, **kwargs):
        return CommandInputStream(chunk=self.chunk, **kwargs)


class VoskCommandRecorderTests(unittest.TestCase):
    def test_uses_vosk_endpoint_to_capture_complete_command(self) -> None:
        recorder = VoskCommandRecorder(
            model=object(),
            vosk_module=CommandVosk(
                CommandRecognizer(endpoint=True, text="open notepad")
            ),
            sounddevice_module=CommandSoundDevice(b"\xff\x7f" * 1_000),
            sample_rate=16_000,
            max_seconds=10,
            silence_seconds=1.5,
            speech_threshold=350,
        )

        audio = recorder.record()

        with wave.open(BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16_000)
            self.assertGreater(wav_file.getnframes(), 0)

    def test_rejects_max_length_silence_for_automatic_retry(self) -> None:
        recorder = VoskCommandRecorder(
            model=object(),
            vosk_module=CommandVosk(CommandRecognizer(endpoint=False, text="")),
            sounddevice_module=CommandSoundDevice(b"\0" * 16_000),
            sample_rate=8_000,
            max_seconds=1,
            silence_seconds=0.5,
            speech_threshold=350,
        )

        with self.assertRaises(NoSpeechDetected):
            recorder.record()


if __name__ == "__main__":
    unittest.main()
