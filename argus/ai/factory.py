"""Build a provider from configuration without coupling callers to its SDK."""

from __future__ import annotations

import os

from argus.ai.groq_provider import GroqProvider
from argus.ai.openai_compatible_provider import OpenAICompatibleProvider
from argus.ai.provider import ModelProvider, ProviderError
from argus.config import AIConfig


def credential_environment_names(
    provider_name: str,
    configured_name: str = "ARGUS_API_KEY",
) -> tuple[str, ...]:
    """Return explicit credential sources without inferring from key contents."""

    names = [configured_name, "ARGUS_API_KEY"]
    legacy_name = {
        "groq": "GROQ_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider_name.casefold())
    if legacy_name:
        names.append(legacy_name)
    return tuple(dict.fromkeys(name for name in names if name))


def resolve_api_key(ai_config: AIConfig, explicit_key: str | None = None) -> str | None:
    """Resolve one chat key from the configured environment or legacy names."""

    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    for name in credential_environment_names(
        ai_config.provider, ai_config.api_key_env
    ):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def create_provider(ai_config: AIConfig, *, api_key: str | None = None) -> ModelProvider:
    provider_name = ai_config.provider.casefold()
    resolved_key = resolve_api_key(ai_config, api_key)

    if provider_name == "groq":
        return GroqProvider(
            api_key=resolved_key,
            model=ai_config.model,
            temperature=ai_config.temperature,
            max_completion_tokens=ai_config.max_completion_tokens,
        )

    if provider_name in {"openai", "openai_compatible"}:
        base_url = ai_config.base_url
        if provider_name == "openai":
            base_url = base_url or "https://api.openai.com/v1"
        if base_url is None:
            raise ProviderError(
                "The openai_compatible provider requires an explicit base URL."
            )
        return OpenAICompatibleProvider(
            api_key=resolved_key,
            model=ai_config.model,
            temperature=ai_config.temperature,
            max_completion_tokens=ai_config.max_completion_tokens,
            base_url=base_url,
            provider_name=provider_name,
            use_native_token_parameter=provider_name == "openai",
        )

    raise ProviderError(
        f"Unsupported AI provider '{ai_config.provider}'. Argus supports: "
        "groq, openai, openai_compatible."
    )
