"""Build a provider from configuration without coupling callers to its SDK."""

from __future__ import annotations

import os

from argus.ai.groq_provider import GroqProvider
from argus.ai.provider import ModelProvider, ProviderError
from argus.config import AIConfig


def create_provider(ai_config: AIConfig, *, api_key: str | None = None) -> ModelProvider:
    provider_name = ai_config.provider.casefold()

    if provider_name == "groq":
        resolved_key = api_key or os.environ.get("GROQ_API_KEY")
        return GroqProvider(
            api_key=resolved_key,
            model=ai_config.model,
            temperature=ai_config.temperature,
            max_completion_tokens=ai_config.max_completion_tokens,
        )

    raise ProviderError(
        f"Unsupported AI provider '{ai_config.provider}'. Argus currently supports: groq."
    )
