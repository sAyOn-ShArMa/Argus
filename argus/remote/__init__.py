"""Remote Argus client package."""

from argus.remote.client import (
    RemoteClient,
    RemoteError,
    create_configured_client,
    normalize_server_url,
)

__all__ = [
    "RemoteClient",
    "RemoteError",
    "create_configured_client",
    "normalize_server_url",
]
