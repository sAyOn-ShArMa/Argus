"""Bounded, explicit microphone recording for Windows terminals."""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
import math
import platform
import time
from typing import Any
import wave

from argus.voice.interfaces import NoSpeechDetected, VoiceError


def _enter_pressed() -> bool:
    if platform.system() != "Windows":
        raise VoiceError("Tier 3 push-to-talk currently requires Windows.")
    import msvcrt

    if not msvcrt.kbhit():
        return False
    character = msvcrt.getwch()
    return character in {"\r", "\n"}


def pcm_to_wav(pcm: bytes, *, sample_rate: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def pcm_rms(pcm: bytes) -> int:
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    if not usable:
        return 0
    samples = memoryview(usable).cast("h")
    return int(math.sqrt(sum(sample * sample for sample in samples) / len(samples)))


class PushToTalkRecorder:
    """Record mono PCM only while an explicit one-turn voice capture is active."""

    def __init__(
        self,
        *,
        sample_rate: int,
        max_seconds: int,
        minimum_seconds: float,
        sounddevice_module: Any | None = None,
        stop_requested: Callable[[], bool] = _enter_pressed,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if sounddevice_module is None:
            try:
                import sounddevice as sounddevice_module
            except ImportError as error:
                raise VoiceError(
                    "Microphone support is not installed. Run: "
                    "python -m pip install -e ."
                ) from error
        self._sounddevice = sounddevice_module
        self._sample_rate = sample_rate
        self._max_bytes = sample_rate * max_seconds * 2
        self._minimum_bytes = int(sample_rate * minimum_seconds * 2)
        self._stop_requested = stop_requested
        self._sleep = sleep

    def record(self) -> bytes:
        pcm = bytearray()
        status_messages: list[str] = []

        def capture(indata: Any, frames: int, timing: Any, status: Any) -> None:
            del frames, timing
            if status:
                status_messages.append(str(status))
            remaining = self._max_bytes - len(pcm)
            if remaining > 0:
                pcm.extend(bytes(indata)[:remaining])

        try:
            with self._sounddevice.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=capture,
            ):
                while len(pcm) < self._max_bytes:
                    if self._stop_requested():
                        break
                    self._sleep(0.02)
        except VoiceError:
            raise
        except Exception as error:
            raise VoiceError(f"Could not record from the microphone: {error}") from error

        if status_messages and not pcm:
            raise VoiceError(f"The microphone reported: {status_messages[0]}")
        if len(pcm) < self._minimum_bytes:
            raise VoiceError("Recording was too short. Speak, then press Enter to stop.")
        return pcm_to_wav(bytes(pcm), sample_rate=self._sample_rate)


class SilenceStoppingRecorder:
    """Capture a hands-free command until speech is followed by silence."""

    def __init__(
        self,
        *,
        sample_rate: int,
        max_seconds: int,
        silence_seconds: float,
        speech_threshold: int,
        sounddevice_module: Any | None = None,
        stop_requested: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if sounddevice_module is None:
            try:
                import sounddevice as sounddevice_module
            except ImportError as error:
                raise VoiceError(
                    "Microphone support is not installed. Run: "
                    "python -m pip install -e ."
                ) from error
        self._sounddevice = sounddevice_module
        self._sample_rate = sample_rate
        self._max_bytes = sample_rate * max_seconds * 2
        self._silence_seconds = silence_seconds
        self._speech_threshold = speech_threshold
        self._stop_requested = stop_requested or (lambda: False)
        self._clock = clock
        self._sleep = sleep

    def record(self) -> bytes:
        pcm = bytearray()
        speech_started = [False]
        last_speech_at: list[float | None] = [None]
        status_messages: list[str] = []

        def capture(indata: Any, frames: int, timing: Any, status: Any) -> None:
            del frames, timing
            if status:
                status_messages.append(str(status))
            remaining = self._max_bytes - len(pcm)
            if remaining <= 0:
                return
            chunk = bytes(indata)[:remaining]
            pcm.extend(chunk)
            if pcm_rms(chunk) >= self._speech_threshold:
                speech_started[0] = True
                last_speech_at[0] = self._clock()

        try:
            with self._sounddevice.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=capture,
            ):
                while len(pcm) < self._max_bytes:
                    if self._stop_requested():
                        raise VoiceError("Voice capture cancelled.")
                    last_speech = last_speech_at[0]
                    if (
                        speech_started[0]
                        and last_speech is not None
                        and self._clock() - last_speech >= self._silence_seconds
                    ):
                        break
                    self._sleep(0.02)
        except Exception as error:
            raise VoiceError(f"Could not record the voice command: {error}") from error

        if status_messages and not pcm:
            raise VoiceError(f"The microphone reported: {status_messages[0]}")
        if not speech_started[0]:
            raise NoSpeechDetected(
                "No voice command was detected before the recording limit."
            )
        return pcm_to_wav(bytes(pcm), sample_rate=self._sample_rate)
