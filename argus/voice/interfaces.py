"""Provider-independent interfaces for the voice pipeline."""

from __future__ import annotations

from typing import Protocol


class VoiceError(RuntimeError):
    """A voice input or output operation could not be completed."""


class NoSpeechDetected(VoiceError):
    """A voice capture completed without a usable spoken command."""


class AudioRecorder(Protocol):
    def record(self) -> bytes:
        """Return one in-memory WAV recording."""


class SpeechTranscriber(Protocol):
    def transcribe(self, wav_audio: bytes) -> str:
        """Return text for one WAV recording."""


class SpeechSynthesizer(Protocol):
    @property
    def voice_name(self) -> str:
        """Return the selected local voice name."""

    def speak(self, text: str) -> None:
        """Speak one response locally."""


class VoiceSession:
    """Compose recording, transcription, and optional local speech output."""

    def __init__(
        self,
        recorder: AudioRecorder,
        transcriber: SpeechTranscriber,
        synthesizer: SpeechSynthesizer | None,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._synthesizer = synthesizer

    @property
    def output_description(self) -> str:
        if self._synthesizer is None:
            return "text only"
        return self._synthesizer.voice_name

    def listen(self) -> str:
        return self._transcriber.transcribe(self._recorder.record())

    def speak(self, text: str) -> None:
        if self._synthesizer is not None:
            self._synthesizer.speak(text)
