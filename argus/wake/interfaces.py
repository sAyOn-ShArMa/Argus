"""Interfaces and composition for wake-word mode."""

from __future__ import annotations

from typing import Protocol

from argus.voice.interfaces import AudioRecorder, SpeechSynthesizer, SpeechTranscriber


class WakeError(RuntimeError):
    """Local wake-word detection could not be started or completed."""


class WakeDetector(Protocol):
    def wait(self) -> None:
        """Block until the configured local wake phrase is detected."""


class WakeModeSession:
    """Compose local wake detection with hands-free command capture."""

    def __init__(
        self,
        detector: WakeDetector,
        command_recorder: AudioRecorder,
        transcriber: SpeechTranscriber,
        synthesizer: SpeechSynthesizer | None,
        *,
        phrase: str,
        acknowledgement: str,
        command_attempts: int = 2,
    ) -> None:
        self._detector = detector
        self._command_recorder = command_recorder
        self._transcriber = transcriber
        self._synthesizer = synthesizer
        self.phrase = phrase
        self.acknowledgement = acknowledgement
        self.command_attempts = command_attempts

    def wait(self) -> None:
        self._detector.wait()

    def acknowledge(self) -> None:
        if self._synthesizer is not None:
            self._synthesizer.speak(self.acknowledgement)

    def listen_for_command(self) -> str:
        return self._transcriber.transcribe(self._command_recorder.record())

    def speak(self, text: str) -> None:
        if self._synthesizer is not None:
            self._synthesizer.speak(text)
