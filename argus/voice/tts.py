"""Local Windows text-to-speech output."""

from __future__ import annotations

import platform
from typing import Any

from argus.voice.interfaces import VoiceError


class WindowsSpeechSynthesizer:
    """Speak through Windows SAPI using the best configured installed voice."""

    def __init__(
        self,
        *,
        rate: int,
        volume: float,
        preferred_keywords: tuple[str, ...],
        engine: Any | None = None,
    ) -> None:
        if platform.system() != "Windows" and engine is None:
            raise VoiceError("Local speech output is currently implemented for Windows.")
        if engine is None:
            try:
                import pyttsx3

                engine = pyttsx3.init("sapi5")
            except Exception as error:
                raise VoiceError(f"Could not initialize Windows speech: {error}") from error

        self._engine = engine
        try:
            voices = list(engine.getProperty("voices") or ())
            selected = self._select_voice(voices, preferred_keywords)
            if selected is not None:
                engine.setProperty("voice", selected.id)
                self._voice_name = str(
                    getattr(selected, "name", None) or selected.id or "Windows voice"
                )
            else:
                self._voice_name = "default Windows voice"
            engine.setProperty("rate", rate)
            engine.setProperty("volume", volume)
        except Exception as error:
            raise VoiceError(f"Could not configure Windows speech: {error}") from error

    @staticmethod
    def _select_voice(voices: list[Any], keywords: tuple[str, ...]) -> Any | None:
        if not voices:
            return None

        def score(voice: Any) -> tuple[int, int]:
            details = " ".join(
                str(value or "")
                for value in (
                    getattr(voice, "name", ""),
                    getattr(voice, "id", ""),
                    getattr(voice, "gender", ""),
                )
            ).casefold()
            keyword_score = sum(
                len(keywords) - index
                for index, keyword in enumerate(keywords)
                if keyword in details
            )
            male_score = 10 if "male" in details else 0
            return male_score + keyword_score, -voices.index(voice)

        return max(voices, key=score)

    @property
    def voice_name(self) -> str:
        return self._voice_name

    def speak(self, text: str) -> None:
        message = text.strip()
        if not message:
            return
        try:
            self._engine.say(message)
            self._engine.runAndWait()
        except Exception as error:
            try:
                self._engine.stop()
            except Exception:
                pass
            raise VoiceError(f"Speech output failed: {error}") from error
