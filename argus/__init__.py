"""Argus personal AI assistant core."""

__version__ = "0.15.0"

from argus.config import AppConfig, ConfigError, load_config
from argus.core import Agent, AgentUnavailable

__all__ = [
    "Agent",
    "AgentUnavailable",
    "AppConfig",
    "ConfigError",
    "__version__",
    "load_config",
]
