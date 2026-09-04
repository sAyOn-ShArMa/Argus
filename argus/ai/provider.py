"""Provider-independent types used by the Argus core."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict


class Message(TypedDict):
    """One chronological conversation message."""

    role: Literal["user", "assistant"]
    content: str


class ProviderError(RuntimeError):
    """A configured AI provider could not complete a request."""


ToolSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One provider-independent local tool request from a model."""

    id: str
    name: str
    arguments: str


ToolExecutor = Callable[[ToolCall], str]


class ModelProvider(Protocol):
    """The only model behavior the Argus core depends on."""

    @property
    def name(self) -> str:
        """Return the configured provider name."""

    @property
    def model(self) -> str:
        """Return the configured model identifier."""

    def stream_reply(
        self,
        *,
        messages: Sequence[Message],
        system_prompt: str,
        tools: Sequence[ToolSchema] = (),
        execute_tool: ToolExecutor | None = None,
        max_tool_rounds: int = 6,
    ) -> Iterator[str]:
        """Yield text fragments for one assistant reply."""
