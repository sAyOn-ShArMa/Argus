from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError

from argus.config import (
    AIConfig,
    AppConfig,
    AssistantConfig,
    ServerClientConfig,
    ServerConfig,
)
from argus.remote.client import (
    RemoteClient,
    RemoteError,
    create_configured_client,
    normalize_server_url,
)


TOKEN = "remote-token-abcdefghijklmnopqrstuvwxyz012345"


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self._body[:limit]


class FakeOpener:
    def __init__(self, response: object) -> None:
        self.response = response
        self.last_request = None

    def open(self, request, timeout: float):
        self.last_request = request
        if isinstance(self.response, BaseException):
            raise self.response
        return FakeResponse(self.response)


class RemoteClientTests(unittest.TestCase):
    def test_plain_http_is_loopback_only(self) -> None:
        self.assertEqual(
            normalize_server_url("http://127.0.0.1:8765/"),
            "http://127.0.0.1:8765",
        )
        with self.assertRaisesRegex(RemoteError, "loopback"):
            normalize_server_url("http://192.168.1.5:8765")
        with self.assertRaisesRegex(RemoteError, "no credentials"):
            normalize_server_url("https://user:pass@example.com")
        with self.assertRaisesRegex(RemoteError, "path"):
            normalize_server_url("https://example.com/argus")
        with self.assertRaisesRegex(RemoteError, "invalid port"):
            normalize_server_url("https://example.com:wrong")

    def test_chat_uses_bearer_token_and_exact_payload(self) -> None:
        opener = FakeOpener({"ok": True, "reply": "Hello."})
        client = RemoteClient("http://localhost:8765", TOKEN, opener=opener)

        self.assertEqual(client.chat("Hi"), "Hello.")
        self.assertEqual(
            opener.last_request.get_header("Authorization"), f"Bearer {TOKEN}"
        )
        self.assertEqual(json.loads(opener.last_request.data), {"message": "Hi"})

    def test_tls_credentials_are_rejected_for_plain_http(self) -> None:
        with self.assertRaisesRegex(RemoteError, "https"):
            RemoteClient(
                "http://localhost:8765",
                TOKEN,
                ca_path=Path("unused-ca.pem"),
            )

    def test_http_errors_do_not_include_the_token(self) -> None:
        error = HTTPError(
            "http://localhost:8765/v1/status",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"ok":false,"error":"Bad token."}'),
        )
        client = RemoteClient(
            "http://localhost:8765", TOKEN, opener=FakeOpener(error)
        )

        with self.assertRaises(RemoteError) as raised:
            client.status()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIn("HTTP 401", str(raised.exception))

    def test_builds_configured_client_from_environment_without_exposing_token(self) -> None:
        configured = ServerClientConfig(
            "owner-laptop", "owner", "Owner", "owner", "ARGUS_SERVER_TOKEN"
        )
        config = AppConfig(
            assistant=AssistantConfig("Argus", "Help."),
            ai=AIConfig("test", "test-model", 0.3, 100),
            source=Path("config.json"),
            server=ServerConfig(enabled=True, clients=(configured,)),
        )
        opener = FakeOpener({"ok": True})

        client, selected = create_configured_client(
            config,
            environment={"ARGUS_SERVER_TOKEN": TOKEN},
            opener=opener,
        )
        client.status()

        self.assertEqual(selected, configured)
        self.assertEqual(
            opener.last_request.get_header("Authorization"), f"Bearer {TOKEN}"
        )
        with self.assertRaisesRegex(RemoteError, "Set ARGUS_SERVER_TOKEN"):
            create_configured_client(config, environment={}, opener=opener)


if __name__ == "__main__":
    unittest.main()
