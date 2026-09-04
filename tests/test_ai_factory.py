from __future__ import annotations

from unittest.mock import patch
import unittest

from argus.ai.factory import (
    credential_environment_names,
    create_provider,
    resolve_api_key,
)
from argus.config import AIConfig


def make_config(
    provider: str = "groq",
    *,
    base_url: str | None = None,
    api_key_env: str = "ARGUS_API_KEY",
) -> AIConfig:
    return AIConfig(
        provider=provider,
        model="test-model",
        temperature=0.3,
        max_completion_tokens=256,
        api_key_env=api_key_env,
        base_url=base_url,
    )


class CredentialResolutionTests(unittest.TestCase):
    def test_generic_key_precedes_legacy_provider_key(self) -> None:
        config = make_config("groq")
        with patch.dict(
            "os.environ",
            {"ARGUS_API_KEY": "generic", "GROQ_API_KEY": "legacy"},
            clear=True,
        ):
            self.assertEqual(resolve_api_key(config), "generic")

    def test_custom_environment_name_is_supported(self) -> None:
        config = make_config(
            "openai_compatible",
            base_url="https://api.company.example/v1",
            api_key_env="COMPANY_API_KEY",
        )
        with patch.dict("os.environ", {"COMPANY_API_KEY": "custom"}, clear=True):
            self.assertEqual(resolve_api_key(config), "custom")

    def test_key_prefix_does_not_select_a_provider(self) -> None:
        names = credential_environment_names("openai_compatible", "COMPANY_API_KEY")
        self.assertEqual(names, ("COMPANY_API_KEY", "ARGUS_API_KEY"))


class ProviderFactoryTests(unittest.TestCase):
    @patch("argus.ai.factory.OpenAICompatibleProvider")
    def test_builds_openai_compatible_provider(self, provider_class) -> None:
        config = make_config(
            "openai_compatible",
            base_url="https://api.company.example/v1",
        )

        create_provider(config, api_key="company-key")

        provider_class.assert_called_once_with(
            api_key="company-key",
            model="test-model",
            temperature=0.3,
            max_completion_tokens=256,
            base_url="https://api.company.example/v1",
            provider_name="openai_compatible",
            use_native_token_parameter=False,
        )

    @patch("argus.ai.factory.OpenAICompatibleProvider")
    def test_openai_uses_official_default_endpoint(self, provider_class) -> None:
        create_provider(make_config("openai"), api_key="openai-key")

        self.assertEqual(
            provider_class.call_args.kwargs["base_url"],
            "https://api.openai.com/v1",
        )
        self.assertTrue(provider_class.call_args.kwargs["use_native_token_parameter"])


if __name__ == "__main__":
    unittest.main()
