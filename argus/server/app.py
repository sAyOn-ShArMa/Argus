"""Explicit authenticated HTTP entry point for the Argus central service."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

from argus.ai.factory import resolve_api_key
from argus.ai.provider import ProviderError
from argus.config import AppConfig, ConfigError, ServerConfig, load_config
from argus.core import AgentUnavailable
from argus.memory import MemoryStoreError
from argus.server.auth import ClientRateLimiter, ServerAuthError, TokenAuthenticator
from argus.server.service import RemotePermissionError, ServerService


def _log_service_error(error: BaseException) -> None:
    """Report a bounded local diagnostic without returning it to the client."""

    detail = " ".join(str(error).split())
    if len(detail) > 1_000:
        detail = detail[:997] + "..."
    suffix = f": {detail}" if detail else ""
    print(
        f"Argus server request failed ({type(error).__name__}){suffix}",
        file=sys.stderr,
        flush=True,
    )


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _HTTPServerV6(_HTTPServer):
    address_family = socket.AF_INET6


def _handler_factory(
    service: ServerService,
    authenticator: TokenAuthenticator,
    limiter: ClientRateLimiter,
    config: ServerConfig,
) -> type[BaseHTTPRequestHandler]:
    class ArgusRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Argus"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15.0)

        def log_message(self, format: str, *args: object) -> None:
            # Do not let URLs, headers, or user content leak into console logs.
            return

        def _send_json(
            self,
            status: HTTPStatus,
            payload: dict[str, Any],
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(
            self,
            status: HTTPStatus,
            message: str,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            # Closing prevents an unread rejected request body from being parsed
            # as a second request on the same HTTP/1.1 connection.
            self.close_connection = True
            headers = {"Connection": "close"}
            if extra_headers:
                headers.update(extra_headers)
            self._send_json(
                status,
                {"ok": False, "error": message},
                extra_headers=headers,
            )

        def _client(self):
            client = authenticator.authenticate(self.headers.get("Authorization"))
            if client is None:
                self._error(
                    HTTPStatus.UNAUTHORIZED,
                    "A valid bearer token is required.",
                    extra_headers={"WWW-Authenticate": "Bearer"},
                )
                return None
            if not limiter.allow(client.client_id):
                self._error(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "Request rate limit exceeded.",
                    extra_headers={"Retry-After": "60"},
                )
                return None
            return client

        def _read_json_object(self) -> dict[str, Any] | None:
            if self.headers.get("Transfer-Encoding"):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "Chunked request bodies are not supported.",
                )
                return None
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type.strip().casefold() != "application/json":
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Content-Type must be application/json.",
                )
                return None
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                length = -1
            if length < 0:
                self._error(
                    HTTPStatus.LENGTH_REQUIRED,
                    "A valid Content-Length header is required.",
                )
                return None
            if length > config.max_request_bytes:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large.")
                return None
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "Request body is not valid JSON.")
                return None
            if not isinstance(payload, dict):
                self._error(HTTPStatus.BAD_REQUEST, "Request body must be a JSON object.")
                return None
            return payload

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/v1/health" and not parsed.query:
                self._send_json(HTTPStatus.OK, service.health())
                return

            client = self._client()
            if client is None:
                return
            try:
                if parsed.path == "/v1/status" and not parsed.query:
                    self._send_json(HTTPStatus.OK, service.status(client))
                    return
                if parsed.path == "/v1/devices" and not parsed.query:
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "devices": service.devices(client)},
                    )
                    return
                if parsed.path == "/v1/notifications":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"after_id", "limit"}:
                        raise ValueError("Unknown notification query parameter.")
                    after_id = int(query.get("after_id", ["0"])[0])
                    limit = int(query.get("limit", ["20"])[0])
                    if after_id < 0 or limit < 1 or limit > 100:
                        raise ValueError(
                            "after_id must be non-negative and limit must be 1 to 100."
                        )
                    notifications = service.notifications(
                        client, after_id=after_id, limit=limit
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "notifications": notifications},
                    )
                    return
            except (TypeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "Invalid request parameters.")
                return
            except (AgentUnavailable, ProviderError, MemoryStoreError) as error:
                _log_service_error(error)
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Argus is unavailable.")
                return
            self._error(HTTPStatus.NOT_FOUND, "Endpoint not found.")

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != "/v1/chat" or parsed.query:
                self._error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
                return
            client = self._client()
            if client is None:
                return
            payload = self._read_json_object()
            if payload is None:
                return
            if set(payload) != {"message"} or not isinstance(
                payload.get("message"), str
            ):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "Request must contain only a string message field.",
                )
                return
            try:
                reply = service.chat(client, payload["message"])
            except RemotePermissionError:
                self._error(HTTPStatus.FORBIDDEN, "This client is read-only.")
                return
            except ValueError:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "Message must contain 1 to 4000 characters.",
                )
                return
            except (AgentUnavailable, ProviderError, MemoryStoreError) as error:
                _log_service_error(error)
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Argus is unavailable.")
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "reply": reply})

        def _method_not_allowed(self) -> None:
            self._error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "Method not allowed.",
                extra_headers={"Allow": "GET, POST"},
            )

        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_OPTIONS = _method_not_allowed

    return ArgusRequestHandler


def create_http_server(
    config: ServerConfig,
    service: ServerService,
    authenticator: TokenAuthenticator,
    *,
    port: int | None = None,
) -> ThreadingHTTPServer:
    limiter = ClientRateLimiter(config.requests_per_minute)
    handler = _handler_factory(service, authenticator, limiter, config)
    server_class = _HTTPServerV6 if ":" in config.host else _HTTPServer
    server = server_class((config.host, config.port if port is None else port), handler)
    try:
        if config.tls_cert_path is not None and config.tls_key_path is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(
                certfile=str(config.tls_cert_path), keyfile=str(config.tls_key_path)
            )
            if config.tls_ca_path is not None:
                context.verify_mode = ssl.CERT_REQUIRED
                context.load_verify_locations(cafile=str(config.tls_ca_path))
            server.socket = context.wrap_socket(server.socket, server_side=True)
    except BaseException:
        server.server_close()
        raise
    return server


def _server_notification(profile_id: str, notification: object) -> None:
    summary = getattr(notification, "summary", lambda: str(notification))()
    print(f"\nArgus alert [{profile_id}]: {summary}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Argus Phase 9 server.")
    parser.add_argument(
        "--config", type=Path, help="Path to an alternate Argus JSON configuration."
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration and credentials."
    )
    arguments = parser.parse_args(argv)

    server: ThreadingHTTPServer | None = None
    service: ServerService | None = None
    try:
        config: AppConfig = load_config(arguments.config)
        if not config.server.enabled:
            raise ConfigError("Server mode is disabled in the configuration.")
        authenticator = TokenAuthenticator(config.server.clients)
        service = ServerService(
            config,
            api_key=resolve_api_key(config.ai),
            notification_sink=_server_notification,
        )
        if arguments.check:
            print("Argus Phase 9 server configuration is valid.")
            return 0

        server = create_http_server(config.server, service, authenticator)
        service.start()
        scheme = "https" if config.server.tls_cert_path is not None else "http"
        print("Argus Phase 9 server")
        print(f"Listening on {scheme}://{config.server.host}:{config.server.port}")
        print("Remote actions are disabled. Press Ctrl+C to stop.")
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nArgus server stopped.")
    except (
        ConfigError,
        MemoryStoreError,
        OSError,
        ProviderError,
        ServerAuthError,
        ValueError,
        ssl.SSLError,
    ) as error:
        print(f"Argus server could not start: {error}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
        if service is not None:
            service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
