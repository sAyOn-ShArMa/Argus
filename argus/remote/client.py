"""Small authenticated text client for an explicit Argus server."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import ipaddress
import json
import os
from pathlib import Path
import ssl
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, Request, build_opener

from argus import __version__
from argus.config import AppConfig, ConfigError, ServerClientConfig, load_config


class RemoteError(RuntimeError):
    """A remote client request or configuration was rejected."""


def normalize_server_url(value: str) -> str:
    text = value.strip()
    parsed = urlsplit(text)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise RemoteError("Server URL must use http or https.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise RemoteError("Server URL must contain a host and no credentials.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RemoteError("Server URL must not contain a path, query, or fragment.")
    if parsed.scheme.casefold() == "http":
        hostname = parsed.hostname.rstrip(".").casefold()
        is_loopback = hostname == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise RemoteError("Plain HTTP is allowed only for a loopback server.")
    try:
        port = parsed.port
    except ValueError:
        raise RemoteError("Server URL contains an invalid port.") from None
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, "", "", ""))


class RemoteClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        ca_path: Path | None = None,
        client_cert_path: Path | None = None,
        client_key_path: Path | None = None,
        timeout: float = 30.0,
        opener: Any | None = None,
    ) -> None:
        normalized_token = token.strip()
        if (
            len(normalized_token) < 32
            or len(normalized_token) > 512
            or any(character.isspace() for character in normalized_token)
        ):
            raise RemoteError("The client token must be 32 to 512 non-whitespace characters.")
        self._base_url = normalize_server_url(base_url)
        self._token = normalized_token
        self._timeout = timeout
        if opener is None:
            if self._base_url.startswith("http://") and any(
                path is not None
                for path in (ca_path, client_cert_path, client_key_path)
            ):
                raise RemoteError("TLS credentials require an https server URL.")
            if (client_cert_path is None) != (client_key_path is None):
                raise RemoteError("Client certificate and key must be provided together.")
            try:
                context = ssl.create_default_context(
                    cafile=str(ca_path) if ca_path is not None else None
                )
                if client_cert_path is not None and client_key_path is not None:
                    context.load_cert_chain(
                        certfile=str(client_cert_path), keyfile=str(client_key_path)
                    )
            except (OSError, ssl.SSLError) as error:
                raise RemoteError(f"Could not load TLS credentials: {error}") from None
            opener = build_opener(HTTPSHandler(context=context))
        self._opener = opener

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": f"Argus-Remote/{__version__}",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise RemoteError("Server response exceeded the safe size limit.")
        except HTTPError as error:
            raw = error.read(65_537)
            message = "Request failed."
            if len(raw) <= 65_536:
                try:
                    error_payload = json.loads(raw.decode("utf-8"))
                    if isinstance(error_payload, dict) and isinstance(
                        error_payload.get("error"), str
                    ):
                        message = error_payload["error"]
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            raise RemoteError(f"Server returned HTTP {error.code}: {message}") from None
        except (URLError, TimeoutError, OSError, ssl.SSLError) as error:
            raise RemoteError(f"Could not reach the Argus server: {error}") from None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteError("Server returned an invalid JSON response.") from None
        if not isinstance(decoded, dict):
            raise RemoteError("Server returned an invalid response object.")
        return decoded

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def chat(self, message: str) -> str:
        response = self._request("POST", "/v1/chat", {"message": message})
        reply = response.get("reply")
        if not isinstance(reply, str):
            raise RemoteError("Server response did not contain a reply.")
        return reply

    def notifications(self, *, after_id: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        query = urlencode({"after_id": after_id, "limit": limit})
        response = self._request("GET", f"/v1/notifications?{query}")
        notifications = response.get("notifications")
        if not isinstance(notifications, list) or not all(
            isinstance(item, dict) for item in notifications
        ):
            raise RemoteError("Server returned invalid notifications.")
        return notifications

    def devices(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/v1/devices")
        devices = response.get("devices")
        if not isinstance(devices, list) or not all(
            isinstance(item, dict) for item in devices
        ):
            raise RemoteError("Server returned an invalid device list.")
        return devices


def _choose_client(
    clients: tuple[ServerClientConfig, ...],
    requested_id: str | None,
    *,
    environment: Mapping[str, str] | None = None,
) -> ServerClientConfig:
    environment = os.environ if environment is None else environment
    selected_id = requested_id or environment.get("ARGUS_REMOTE_CLIENT_ID")
    if selected_id is None:
        return clients[0]
    for client in clients:
        if client.client_id == selected_id:
            return client
    raise RemoteError(f"Configured server client '{selected_id}' was not found.")


def _default_url(host: str, port: int, *, tls: bool) -> str:
    if host in {"0.0.0.0", "::"}:
        raise RemoteError("Use --url when the server is configured with a wildcard host.")
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{'https' if tls else 'http'}://{rendered_host}:{port}"


def create_configured_client(
    config: AppConfig,
    *,
    url: str | None = None,
    client_id: str | None = None,
    ca_path: Path | None = None,
    client_cert_path: Path | None = None,
    client_key_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    opener: Any | None = None,
) -> tuple[RemoteClient, ServerClientConfig]:
    """Build a client from validated config without persisting its token."""

    if not config.server.enabled or not config.server.clients:
        raise RemoteError("Server mode is disabled in the configuration.")
    environment = os.environ if environment is None else environment
    selected = _choose_client(
        config.server.clients, client_id, environment=environment
    )
    token = environment.get(selected.token_env, "")
    if not token.strip():
        raise RemoteError(f"Set {selected.token_env} before starting the client.")
    base_url = url or _default_url(
        config.server.host,
        config.server.port,
        tls=config.server.tls_cert_path is not None,
    )
    return (
        RemoteClient(
            base_url,
            token,
            ca_path=ca_path,
            client_cert_path=client_cert_path,
            client_key_path=client_key_path,
            opener=opener,
        ),
        selected,
    )


def _print_status(status: Mapping[str, Any]) -> None:
    print(
        "Connected as "
        f"{status.get('client_id', 'unknown')} "
        f"(profile {status.get('profile_id', 'unknown')}, "
        f"role {status.get('role', 'unknown')})."
    )
    print("Remote actions: disabled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect to an Argus Phase 9 server.")
    parser.add_argument("--config", type=Path, help="Alternate Argus JSON configuration.")
    parser.add_argument("--url", help="Argus server base URL.")
    parser.add_argument("--client", help="Configured server client ID.")
    parser.add_argument("--ca", type=Path, help="Certificate authority file for HTTPS.")
    parser.add_argument("--cert", type=Path, help="Optional mutual-TLS client certificate.")
    parser.add_argument("--key", type=Path, help="Optional mutual-TLS client key.")
    parser.add_argument("--message", help="Send one message and exit.")
    arguments = parser.parse_args(argv)

    try:
        config = load_config(arguments.config)
        client, _ = create_configured_client(
            config,
            url=arguments.url,
            client_id=arguments.client,
            ca_path=arguments.ca,
            client_cert_path=arguments.cert,
            client_key_path=arguments.key,
        )
        status = client.status()
        _print_status(status)
        if arguments.message is not None:
            print(f"Argus: {client.chat(arguments.message)}")
            return 0

        print("Type a message, /status, /notifications, /devices, or /exit.")
        notification_cursor = 0
        while True:
            try:
                message = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not message:
                continue
            if message.casefold() in {"/exit", "/quit"}:
                break
            if message.casefold() == "/status":
                _print_status(client.status())
                continue
            if message.casefold() == "/notifications":
                notifications = client.notifications(after_id=notification_cursor)
                if not notifications:
                    print("Argus: No new delivered notifications.")
                for notification in notifications:
                    print(f"Argus alert: {notification.get('content', '')}")
                    identifier = notification.get("id")
                    if isinstance(identifier, int):
                        notification_cursor = max(notification_cursor, identifier)
                continue
            if message.casefold() == "/devices":
                devices = client.devices()
                if not devices:
                    print("Argus: No devices are configured.")
                for device in devices:
                    print(
                        f"- {device.get('name', 'Unknown')} "
                        f"({device.get('device_id', 'unknown')}): remote control disabled"
                    )
                continue
            print(f"Argus: {client.chat(message)}")
    except (ConfigError, RemoteError) as error:
        print(f"Argus remote client could not start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
