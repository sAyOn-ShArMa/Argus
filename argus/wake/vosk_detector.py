"""Offline streaming wake-phrase detection using Vosk."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
import time
from typing import Any

from argus.voice.interfaces import NoSpeechDetected, VoiceError
from argus.voice.recording import pcm_rms, pcm_to_wav
from argus.wake.interfaces import WakeError


def _contains_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = f" {' '.join(text.casefold().split())} "
    return any(f" {phrase} " in normalized for phrase in phrases)


class VoskWakeDetector:
    """Listen locally for a constrained list of Argus pronunciations."""

    def __init__(
        self,
        *,
        model_path: Path,
        sample_rate: int,
        phrases: tuple[str, ...],
        sounddevice_module: Any | None = None,
        vosk_module: Any | None = None,
    ) -> None:
        if not model_path.is_dir():
            raise WakeError(
                f"Wake model not found: {model_path}. Run the wake-model setup "
                "command from README.md."
            )
        if sounddevice_module is None:
            try:
                import sounddevice as sounddevice_module
            except ImportError as error:
                raise WakeError("The sounddevice package is not installed.") from error
        if vosk_module is None:
            try:
                import vosk as vosk_module
            except ImportError as error:
                raise WakeError(
                    "The Vosk package is not installed. Run: python -m pip install -e ."
                ) from error

        self._sounddevice = sounddevice_module
        self._vosk = vosk_module
        self._sample_rate = sample_rate
        self._phrases = phrases
        try:
            self._vosk.SetLogLevel(-1)
            self._model = self._vosk.Model(str(model_path))
        except Exception as error:
            raise WakeError(f"Could not load the local wake model: {error}") from error

    def wait(self) -> None:
        audio: Queue[bytes] = Queue(maxsize=32)
        errors: list[str] = []

        def capture(indata: Any, frames: int, timing: Any, status: Any) -> None:
            del frames, timing
            if status:
                errors.append(str(status))
            chunk = bytes(indata)
            try:
                audio.put_nowait(chunk)
            except Full:
                try:
                    audio.get_nowait()
                except Empty:
                    pass
                try:
                    audio.put_nowait(chunk)
                except Full:
                    pass

        grammar = json.dumps([*self._phrases, "[unk]"])
        try:
            recognizer = self._vosk.KaldiRecognizer(
                self._model, self._sample_rate, grammar
            )
            with self._sounddevice.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=capture,
                blocksize=4_000,
            ):
                while True:
                    try:
                        chunk = audio.get(timeout=0.1)
                    except Empty:
                        if errors:
                            raise WakeError(f"The microphone reported: {errors[0]}")
                        continue

                    if recognizer.AcceptWaveform(chunk):
                        result = json.loads(recognizer.Result()).get("text", "")
                    else:
                        result = json.loads(recognizer.PartialResult()).get(
                            "partial", ""
                        )
                    if isinstance(result, str) and _contains_phrase(
                        result, self._phrases
                    ):
                        return
        except WakeError:
            raise
        except Exception as error:
            raise WakeError(f"Wake listening failed: {error}") from error

    def command_recorder(
        self,
        *,
        max_seconds: int,
        silence_seconds: float,
        speech_threshold: int,
    ) -> "VoskCommandRecorder":
        return VoskCommandRecorder(
            model=self._model,
            vosk_module=self._vosk,
            sounddevice_module=self._sounddevice,
            sample_rate=self._sample_rate,
            max_seconds=max_seconds,
            silence_seconds=silence_seconds,
            speech_threshold=speech_threshold,
        )


class VoskCommandRecorder:
    """Use Vosk endpoints plus a level fallback to capture a complete command."""

    def __init__(
        self,
        *,
        model: Any,
        vosk_module: Any,
        sounddevice_module: Any,
        sample_rate: int,
        max_seconds: int,
        silence_seconds: float,
        speech_threshold: int,
        clock: Any = time.monotonic,
    ) -> None:
        self._model = model
        self._vosk = vosk_module
        self._sounddevice = sounddevice_module
        self._sample_rate = sample_rate
        self._max_bytes = sample_rate * max_seconds * 2
        self._silence_seconds = silence_seconds
        self._speech_threshold = speech_threshold
        self._clock = clock

    def record(self) -> bytes:
        audio: Queue[bytes] = Queue(maxsize=64)
        errors: list[str] = []

        def capture(indata: Any, frames: int, timing: Any, status: Any) -> None:
            del frames, timing
            if status:
                errors.append(str(status))
            try:
                audio.put_nowait(bytes(indata))
            except Full:
                try:
                    audio.get_nowait()
                    audio.put_nowait(bytes(indata))
                except (Empty, Full):
                    pass

        pcm = bytearray()
        speech_seen = False
        last_loud_audio: float | None = None
        try:
            recognizer = self._vosk.KaldiRecognizer(self._model, self._sample_rate)
            with self._sounddevice.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=capture,
                blocksize=800,
            ):
                while len(pcm) < self._max_bytes:
                    try:
                        chunk = audio.get(timeout=0.1)
                    except Empty:
                        if errors:
                            raise VoiceError(f"The microphone reported: {errors[0]}")
                        continue

                    remaining = self._max_bytes - len(pcm)
                    chunk = chunk[:remaining]
                    pcm.extend(chunk)
                    now = self._clock()
                    if pcm_rms(chunk) >= self._speech_threshold:
                        speech_seen = True
                        last_loud_audio = now

                    endpoint = recognizer.AcceptWaveform(chunk)
                    if endpoint:
                        final_text = json.loads(recognizer.Result()).get("text", "")
                        if isinstance(final_text, str) and final_text.strip():
                            speech_seen = True
                            break
                    else:
                        partial = json.loads(recognizer.PartialResult()).get(
                            "partial", ""
                        )
                        if isinstance(partial, str) and partial.strip():
                            speech_seen = True

                    if (
                        speech_seen
                        and last_loud_audio is not None
                        and now - last_loud_audio >= self._silence_seconds
                    ):
                        break
        except VoiceError:
            raise
        except Exception as error:
            raise VoiceError(f"Could not capture the voice command: {error}") from error

        if not speech_seen:
            raise NoSpeechDetected(
                "No voice command was detected before the recording limit."
            )
        return pcm_to_wav(bytes(pcm), sample_rate=self._sample_rate)
