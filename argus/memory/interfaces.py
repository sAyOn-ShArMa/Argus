"""Small provider-independent interfaces for durable Argus context."""

from __future__ import annotations

from typing import Protocol

from argus.ai.provider import Message


class ConversationStore(Protocol):
    """The durable behavior needed by the conversation core."""

    def load_context(self, limit: int) -> list[Message]:
        """Load chronological messages after the current context boundary."""

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        """Atomically persist one completed user/assistant turn."""

    def reset_context(self) -> None:
        """Start a fresh context without deleting stored history."""
