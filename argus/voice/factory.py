"""Construct a voice pipeline without coupling the CLI to its providers."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from argus.config import VoiceConfig, WakeConfig
from argus.wake.interfaces import WakeError, WakeModeSession
from argus.wake.vosk_detector import VoskWakeDetector
from argus.voice.groq_stt import GroqTranscriber
from argus.voice.interfaces import VoiceError, VoiceSession
from argus.voice.recording import PushToTalkRecorder, SilenceStoppingRecorder
from argus.voice.tts import WindowsSpeechSynthesizer


@dataclass(frozen=True, slots=True)
class VoiceServices:
    push_to_talk: VoiceSession
    wake_mode: WakeModeSession | None = None
    wake_error: str | None = None


def _shared_voice_components(config: VoiceConfig, api_key: str | None):
    if not config.enabled:
        raise VoiceError("Voice mode is disabled in the configuration.")
    if config.stt_provider != "groq":
        raise VoiceError(f"Unsupported speech provider: {config.stt_provider}")

    transcriber = GroqTranscriber(
        api_key=api_key,
        model=config.stt_model,
        language=config.language,
    )
    synthesizer = None
    if config.tts_enabled:
        synthesizer = WindowsSpeechSynthesizer(
            rate=config.tts_rate,
            volume=config.tts_volume,
            preferred_keywords=config.preferred_voice_keywords,
        )
    return transcriber, synthesizer


def create_voice_session(config: VoiceConfig, *, api_key: str | None) -> VoiceSession:
    transcriber, synthesizer = _shared_voice_components(config, api_key)
    recorder = PushToTalkRecorder(
        sample_rate=config.sample_rate,
        max_seconds=config.max_recording_seconds,
        minimum_seconds=config.minimum_recording_seconds,
    )
    return VoiceSession(recorder, transcriber, synthesizer)


def create_silence_stopping_voice_session(
    voice_config: VoiceConfig,
    wake_config: WakeConfig,
    *,
    api_key: str | None,
    stop_requested: Callable[[], bool] | None = None,
) -> VoiceSession:
    """Create a GUI-friendly voice turn that ends after the user stops speaking."""

    transcriber, synthesizer = _shared_voice_components(voice_config, api_key)
    recorder = SilenceStoppingRecorder(
        sample_rate=voice_config.sample_rate,
        max_seconds=voice_config.max_recording_seconds,
        silence_seconds=wake_config.silence_seconds,
        speech_threshold=wake_config.speech_threshold,
        stop_requested=stop_requested,
    )
    return VoiceSession(recorder, transcriber, synthesizer)


def create_voice_services(
    voice_config: VoiceConfig,
    wake_config: WakeConfig,
    *,
    api_key: str | None,
) -> VoiceServices:
    transcriber, synthesizer = _shared_voice_components(voice_config, api_key)
    push_to_talk = VoiceSession(
        PushToTalkRecorder(
            sample_rate=voice_config.sample_rate,
            max_seconds=voice_config.max_recording_seconds,
            minimum_seconds=voice_config.minimum_recording_seconds,
        ),
        transcriber,
        synthesizer,
    )
    if not wake_config.enabled:
        return VoiceServices(push_to_talk)

    try:
        if wake_config.model_path is None:
            raise WakeError("Wake mode has no configured local model path.")
        detector = VoskWakeDetector(
            model_path=wake_config.model_path,
            sample_rate=wake_config.sample_rate,
            phrases=wake_config.recognition_aliases,
        )
        command_recorder = detector.command_recorder(
            max_seconds=wake_config.command_max_seconds,
            silence_seconds=wake_config.silence_seconds,
            speech_threshold=wake_config.speech_threshold,
        )
        wake_mode = WakeModeSession(
            detector,
            command_recorder,
            transcriber,
            synthesizer,
            phrase=wake_config.phrase,
            acknowledgement=wake_config.acknowledgement,
            command_attempts=wake_config.command_attempts,
        )
        return VoiceServices(push_to_talk, wake_mode=wake_mode)
    except WakeError as error:
        return VoiceServices(push_to_talk, wake_error=str(error))
