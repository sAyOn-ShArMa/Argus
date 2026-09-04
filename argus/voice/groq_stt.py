"""Groq Whisper speech-to-text adapter."""

from __future__ import annotations

from typing import Any

from argus.voice.interfaces import NoSpeechDetected, VoiceError


class GroqTranscriber:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        language: str | None,
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise VoiceError("A Groq API key is required for speech recognition.")
        self._secret = api_key.strip()
        self._model = model
        self._language = language

        if client is None:
            try:
                from groq import Groq
            except ImportError as error:
                raise VoiceError(
                    "The Groq SDK is not installed. Run: python -m pip install -e ."
                ) from error
            client = Groq(api_key=self._secret, max_retries=2, timeout=60.0)
        self._client = client

    def transcribe(self, wav_audio: bytes) -> str:
        if not wav_audio:
            raise VoiceError("The recording was empty.")
        request: dict[str, Any] = {
            "file": ("argus-voice.wav", wav_audio),
            "model": self._model,
            "response_format": "json",
            "temperature": 0.0,
        }
        if self._language:
            request["language"] = self._language

        try:
            transcription = self._client.audio.transcriptions.create(**request)
            text = getattr(transcription, "text", None)
            if text is None and isinstance(transcription, dict):
                text = transcription.get("text")
            if not isinstance(text, str) or not text.strip():
                raise NoSpeechDetected("No speech was recognized. Please try again.")
            return text.strip()
        except VoiceError:
            raise
        except Exception as error:
            detail = str(error).replace(self._secret, "[redacted]").strip()
            message = f"Speech recognition failed ({type(error).__name__})"
            if detail:
                message = f"{message}: {detail}"
            raise VoiceError(message) from error
