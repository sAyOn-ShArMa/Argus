from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import wave

from argus.voice.groq_stt import GroqTranscriber
from argus.voice.interfaces import VoiceError, VoiceSession
from argus.voice.recording import PushToTalkRecorder, SilenceStoppingRecorder
from argus.voice.tts import WindowsSpeechSynthesizer


class FakeRawInputStream:
    def __init__(self, *, audio: bytes, callback, **kwargs) -> None:
        self.audio = audio
        self.callback = callback
        self.kwargs = kwargs

    def __enter__(self):
        self.callback(self.audio, len(self.audio) // 2, None, None)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeSoundDevice:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.requests: list[dict[str, object]] = []

    def RawInputStream(self, **kwargs):
        self.requests.append(kwargs)
        return FakeRawInputStream(audio=self.audio, **kwargs)


class RecorderTests(unittest.TestCase):
    def test_records_mono_pcm_to_an_in_memory_wav(self) -> None:
        sounddevice = FakeSoundDevice(b"\0\1" * 2_000)
        recorder = PushToTalkRecorder(
            sample_rate=16_000,
            max_seconds=2,
            minimum_seconds=0.1,
            sounddevice_module=sounddevice,
            stop_requested=lambda: True,
            sleep=lambda seconds: None,
        )

        audio = recorder.record()

        with wave.open(BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 16_000)
            self.assertEqual(wav_file.readframes(2_000), b"\0\1" * 2_000)
        self.assertEqual(sounddevice.requests[0]["dtype"], "int16")

    def test_rejects_accidental_tap_without_enough_audio(self) -> None:
        recorder = PushToTalkRecorder(
            sample_rate=16_000,
            max_seconds=2,
            minimum_seconds=0.4,
            sounddevice_module=FakeSoundDevice(b"\0" * 100),
            stop_requested=lambda: True,
            sleep=lambda seconds: None,
        )

        with self.assertRaisesRegex(VoiceError, "too short"):
            recorder.record()

    def test_never_records_beyond_configured_maximum(self) -> None:
        recorder = PushToTalkRecorder(
            sample_rate=8_000,
            max_seconds=1,
            minimum_seconds=0.1,
            sounddevice_module=FakeSoundDevice(b"\0" * 50_000),
            stop_requested=lambda: False,
            sleep=lambda seconds: None,
        )

        audio = recorder.record()

        with wave.open(BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getnframes(), 8_000)

    def test_hands_free_recording_stops_after_speech_then_silence(self) -> None:
        moments = iter([0.0, 2.0])
        recorder = SilenceStoppingRecorder(
            sample_rate=8_000,
            max_seconds=5,
            silence_seconds=1.0,
            speech_threshold=500,
            sounddevice_module=FakeSoundDevice(b"\xff\x7f" * 1_000),
            clock=lambda: next(moments),
            sleep=lambda seconds: None,
        )

        audio = recorder.record()

        with wave.open(BytesIO(audio), "rb") as wav_file:
            self.assertGreater(wav_file.getnframes(), 0)

    def test_hands_free_recording_rejects_silence(self) -> None:
        recorder = SilenceStoppingRecorder(
            sample_rate=8_000,
            max_seconds=1,
            silence_seconds=0.5,
            speech_threshold=500,
            sounddevice_module=FakeSoundDevice(b"\0" * 16_000),
            clock=lambda: 0.0,
            sleep=lambda seconds: None,
        )

        with self.assertRaisesRegex(VoiceError, "No voice command"):
            recorder.record()

    def test_hands_free_recording_can_be_cancelled_by_the_gui(self) -> None:
        recorder = SilenceStoppingRecorder(
            sample_rate=8_000,
            max_seconds=5,
            silence_seconds=1.0,
            speech_threshold=500,
            sounddevice_module=FakeSoundDevice(b"\0" * 1_000),
            stop_requested=lambda: True,
            sleep=lambda seconds: None,
        )

        with self.assertRaisesRegex(VoiceError, "cancelled"):
            recorder.record()


class FakeTranscriptions:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.request: dict[str, object] | None = None

    def create(self, **kwargs):
        self.request = kwargs
        if self.error:
            raise self.error
        return self.response


class TranscriberTests(unittest.TestCase):
    def test_sends_wav_to_configured_groq_whisper_model(self) -> None:
        transcriptions = FakeTranscriptions(SimpleNamespace(text="  Open VS Code. "))
        client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=transcriptions)
        )
        transcriber = GroqTranscriber(
            api_key="test-secret",
            model="whisper-large-v3-turbo",
            language="en",
            client=client,
        )

        text = transcriber.transcribe(b"RIFF-test")

        self.assertEqual(text, "Open VS Code.")
        self.assertEqual(
            transcriptions.request["file"], ("argus-voice.wav", b"RIFF-test")
        )
        self.assertEqual(
            transcriptions.request["model"], "whisper-large-v3-turbo"
        )
        self.assertEqual(transcriptions.request["language"], "en")

    def test_redacts_api_key_from_transcription_error(self) -> None:
        transcriptions = FakeTranscriptions(error=RuntimeError("bad test-secret"))
        client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=transcriptions)
        )
        transcriber = GroqTranscriber(
            api_key="test-secret",
            model="whisper-large-v3-turbo",
            language=None,
            client=client,
        )

        with self.assertRaisesRegex(VoiceError, r"bad \[redacted\]"):
            transcriber.transcribe(b"RIFF-test")


class FakeEngine:
    def __init__(self) -> None:
        self.voices = [
            SimpleNamespace(id="zira", name="Microsoft Zira", gender="female"),
            SimpleNamespace(id="david", name="Microsoft David", gender="male"),
        ]
        self.properties: list[tuple[str, object]] = []
        self.spoken: list[str] = []
        self.ran = False

    def getProperty(self, name: str):
        return self.voices if name == "voices" else None

    def setProperty(self, name: str, value: object) -> None:
        self.properties.append((name, value))

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def runAndWait(self) -> None:
        self.ran = True


class SynthesizerTests(unittest.TestCase):
    def test_prefers_installed_male_voice_and_speaks_locally(self) -> None:
        engine = FakeEngine()
        synthesizer = WindowsSpeechSynthesizer(
            rate=175,
            volume=0.9,
            preferred_keywords=("david", "male"),
            engine=engine,
        )

        synthesizer.speak("Ready, sir.")

        self.assertEqual(synthesizer.voice_name, "Microsoft David")
        self.assertIn(("voice", "david"), engine.properties)
        self.assertIn(("rate", 175), engine.properties)
        self.assertEqual(engine.spoken, ["Ready, sir."])
        self.assertTrue(engine.ran)


class VoiceSessionTests(unittest.TestCase):
    def test_composes_voice_components_without_storing_audio(self) -> None:
        recorder = Mock(record=Mock(return_value=b"audio"))
        transcriber = Mock(transcribe=Mock(return_value="Hello Argus"))
        synthesizer = Mock(
            voice_name="Test voice", speak=Mock(return_value=None)
        )
        session = VoiceSession(recorder, transcriber, synthesizer)

        transcript = session.listen()
        session.speak("Hello.")

        self.assertEqual(transcript, "Hello Argus")
        transcriber.transcribe.assert_called_once_with(b"audio")
        synthesizer.speak.assert_called_once_with("Hello.")


if __name__ == "__main__":
    unittest.main()
