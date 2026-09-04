from __future__ import annotations

import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from argus.config import (
    AIConfig,
    AppConfig,
    AssistantConfig,
    MemoryConfig,
    ServerClientConfig,
    ServerConfig,
)
from argus.memory import LocalMemoryStore
from argus.server.app import create_http_server
from argus.server.auth import ClientRateLimiter, ServerAuthError, TokenAuthenticator
from argus.server.service import RemotePermissionError, ServerService


TOKEN_ONE = "owner-token-abcdefghijklmnopqrstuvwxyz012345"
TOKEN_TWO = "reader-token-abcdefghijklmnopqrstuvwxyz01"


class EchoProvider:
    name = "test"
    model = "test-model"

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id

    def stream_reply(self, *, messages, system_prompt):
        yield f"{self.profile_id}: {messages[-1]['content']}"


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.owner = ServerClientConfig(
            "owner-laptop", "owner", "Owner", "owner", "TOKEN_ONE"
        )
        self.reader = ServerClientConfig(
            "reader-phone", "reader", "Reader", "read_only", "TOKEN_TWO"
        )
        self.config = AppConfig(
            assistant=AssistantConfig("Argus", "Help."),
            ai=AIConfig("test", "test-model", 0.3, 100),
            source=root / "config.json",
            memory=MemoryConfig(
                enabled=True,
                database_path=root / "argus.db",
                profile_id="owner",
                profile_name="Owner",
                conversation_context_messages=20,
            ),
            server=ServerConfig(
                enabled=True,
                host="127.0.0.1",
                port=8765,
                max_request_bytes=1024,
                requests_per_minute=100,
                clients=(self.owner, self.reader),
            ),
        )

    def make_service(self) -> ServerService:
        return ServerService(
            self.config,
            api_key=None,
            provider_factory=lambda profile_id: EchoProvider(profile_id),
        )

    def test_authentication_uses_distinct_environment_tokens(self) -> None:
        auth = TokenAuthenticator(
            self.config.server.clients,
            environment={"TOKEN_ONE": TOKEN_ONE, "TOKEN_TWO": TOKEN_TWO},
        )

        self.assertEqual(auth.authenticate(f"Bearer {TOKEN_ONE}"), self.owner)
        self.assertIsNone(auth.authenticate(TOKEN_ONE))
        self.assertIsNone(auth.authenticate("Bearer wrong"))

        with self.assertRaisesRegex(ServerAuthError, "at least 32"):
            TokenAuthenticator(
                self.config.server.clients,
                environment={"TOKEN_ONE": "short", "TOKEN_TWO": TOKEN_TWO},
            )
        with self.assertRaisesRegex(ServerAuthError, "different token"):
            TokenAuthenticator(
                self.config.server.clients,
                environment={"TOKEN_ONE": TOKEN_ONE, "TOKEN_TWO": TOKEN_ONE},
            )

    def test_rate_limiter_is_scoped_per_client(self) -> None:
        moment = [100.0]
        limiter = ClientRateLimiter(2, clock=lambda: moment[0])

        self.assertTrue(limiter.allow("one"))
        self.assertTrue(limiter.allow("one"))
        self.assertFalse(limiter.allow("one"))
        self.assertTrue(limiter.allow("two"))
        moment[0] += 61.0
        self.assertTrue(limiter.allow("one"))

    def test_service_separates_profiles_and_blocks_read_only_chat(self) -> None:
        service = self.make_service()
        self.addCleanup(service.close)

        self.assertEqual(service.chat(self.owner, "hello"), "owner: hello")
        with self.assertRaises(RemotePermissionError):
            service.chat(self.reader, "change something")

        owner_store = LocalMemoryStore(
            self.config.memory.database_path, profile_id="owner", profile_name="Owner"
        )
        reader_store = LocalMemoryStore(
            self.config.memory.database_path, profile_id="reader", profile_name="Reader"
        )
        self.assertEqual(len(owner_store.load_context(20)), 2)
        self.assertEqual(reader_store.load_context(20), [])

    def test_http_boundary_allows_chat_but_exposes_no_action_endpoint(self) -> None:
        service = self.make_service()
        auth = TokenAuthenticator(
            self.config.server.clients,
            environment={"TOKEN_ONE": TOKEN_ONE, "TOKEN_TWO": TOKEN_TWO},
        )
        server = create_http_server(self.config.server, service, auth, port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(service.close)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"

        with urlopen(f"{base}/v1/health", timeout=5) as response:
            self.assertTrue(json.load(response)["ok"])
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(f"{base}/v1/status", timeout=5)
        self.assertEqual(unauthorized.exception.code, 401)

        status_request = Request(
            f"{base}/v1/status",
            headers={"Authorization": f"Bearer {TOKEN_ONE}"},
        )
        with urlopen(status_request, timeout=5) as response:
            status = json.load(response)
        self.assertFalse(status["remote_actions_enabled"])

        chat_request = Request(
            f"{base}/v1/chat",
            data=json.dumps({"message": "hello"}).encode(),
            headers={
                "Authorization": f"Bearer {TOKEN_ONE}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(chat_request, timeout=5) as response:
            self.assertEqual(json.load(response)["reply"], "owner: hello")

        read_only_request = Request(
            f"{base}/v1/chat",
            data=json.dumps({"message": "hello"}).encode(),
            headers={
                "Authorization": f"Bearer {TOKEN_TWO}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as forbidden:
            urlopen(read_only_request, timeout=5)
        self.assertEqual(forbidden.exception.code, 403)

        oversized_request = Request(
            f"{base}/v1/chat",
            data=b"x" * 1025,
            headers={
                "Authorization": f"Bearer {TOKEN_ONE}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as oversized:
            urlopen(oversized_request, timeout=5)
        self.assertEqual(oversized.exception.code, 413)

        action_request = Request(
            f"{base}/v1/actuate",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {TOKEN_ONE}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as missing:
            urlopen(action_request, timeout=5)
        self.assertEqual(missing.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
