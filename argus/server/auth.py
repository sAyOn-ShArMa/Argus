"""Environment-backed bearer authentication and bounded request rates."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
import os
import secrets
from threading import Lock
import time

from argus.config import ServerClientConfig


class ServerAuthError(RuntimeError):
    """Server client credentials are missing or invalid."""


class TokenAuthenticator:
    def __init__(
        self,
        clients: tuple[ServerClientConfig, ...],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        environment = environment if environment is not None else os.environ
        credentials: list[tuple[str, ServerClientConfig]] = []
        seen_tokens: list[str] = []
        for client in clients:
            token = environment.get(client.token_env, "").strip()
            if (
                len(token) < 32
                or len(token) > 512
                or any(character.isspace() for character in token)
            ):
                raise ServerAuthError(
                    f"Set {client.token_env} to a random token of at least "
                    "32 non-whitespace characters."
                )
            if any(secrets.compare_digest(token, existing) for existing in seen_tokens):
                raise ServerAuthError(
                    "Every configured server client must use a different token."
                )
            seen_tokens.append(token)
            credentials.append((token, client))
        self._credentials = tuple(credentials)

    def authenticate(self, authorization: str | None) -> ServerClientConfig | None:
        if (
            authorization is None
            or len(authorization) > 1024
            or not authorization.startswith("Bearer ")
        ):
            return None
        token = authorization[7:]
        if not token or any(character.isspace() for character in token):
            return None
        matched = None
        for expected, client in self._credentials:
            if secrets.compare_digest(token, expected):
                matched = client
        return matched


class ClientRateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock=time.monotonic,
    ) -> None:
        self._limit = requests_per_minute
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, client_id: str) -> bool:
        now = self._clock()
        cutoff = now - 60.0
        with self._lock:
            events = self._events[client_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                return False
            events.append(now)
            return True
