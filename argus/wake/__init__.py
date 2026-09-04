"""Local, opt-in wake-word mode for Argus."""

from argus.wake.interfaces import WakeError, WakeModeSession
from argus.wake.vosk_detector import VoskWakeDetector

__all__ = ["VoskWakeDetector", "WakeError", "WakeModeSession"]
