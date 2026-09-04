"""Shared streaming conversation core."""

from __future__ import annotations

from collections.abc import Iterator

from argus.ai.provider import Message, ModelProvider, ProviderError
from argus.memory.interfaces import ConversationStore
from argus.memory.store import MemoryStoreError
from argus.tools.runtime import ToolRuntime


class AgentUnavailable(RuntimeError):
    """A turn could not be completed without corrupting conversation state."""


class Agent:
    """A provider-independent agent with optional durable local history."""

    def __init__(
        self,
        provider: ModelProvider,
        system_prompt: str,
        *,
        tool_runtime: ToolRuntime | None = None,
        conversation_store: ConversationStore | None = None,
        context_limit: int = 20,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        self._provider = provider
        self._system_prompt = system_prompt
        self._tool_runtime = tool_runtime
        self._conversation_store = conversation_store
        self._context_limit = context_limit
        if conversation_store is None:
            self._history: list[Message] = []
        else:
            self._history = conversation_store.load_context(context_limit)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def model_name(self) -> str:
        return self._provider.model

    @property
    def history(self) -> tuple[Message, ...]:
        return tuple(message.copy() for message in self._history)

    @property
    def tool_descriptions(self) -> tuple[str, ...]:
        if self._tool_runtime is None:
            return ()
        return self._tool_runtime.descriptions

    def reset(self) -> None:
        """Start fresh context while retaining any durable history record."""

        if self._conversation_store is not None:
            try:
                self._conversation_store.reset_context()
            except MemoryStoreError as error:
                raise AgentUnavailable(str(error)) from error
        self.clear_active_context()

    def clear_active_context(self) -> None:
        """Discard currently loaded context without changing durable storage."""

        self._history.clear()
        if self._tool_runtime is not None:
            self._tool_runtime.clear_receipts()

    def stream_turn(self, user_text: str) -> Iterator[str]:
        text = user_text.strip()
        if not text:
            raise ValueError("user_text cannot be empty")

        user_message: Message = {"role": "user", "content": text}
        self._history.append(user_message)
        fragments: list[str] = []

        try:
            system_prompt = self._system_prompt
            if self._tool_runtime is not None:
                receipts = self._tool_runtime.verified_action_context
                if receipts:
                    system_prompt = f"{system_prompt.rstrip()}\n\n{receipts}"
            provider_arguments = {
                "messages": self.history,
                "system_prompt": system_prompt,
            }
            if self._tool_runtime is not None:
                provider_arguments.update(
                    tools=self._tool_runtime.schemas,
                    execute_tool=self._tool_runtime.execute,
                    max_tool_rounds=self._tool_runtime.max_rounds,
                )

            for fragment in self._provider.stream_reply(**provider_arguments):
                if fragment:
                    fragments.append(fragment)
                    yield fragment

            reply = "".join(fragments).strip()
            if not reply:
                raise ProviderError("The configured model returned no text.")
            if self._conversation_store is not None:
                try:
                    self._conversation_store.append_turn(text, reply)
                except MemoryStoreError as error:
                    raise AgentUnavailable(
                        "The reply was generated but local memory could not save it: "
                        f"{error}"
                    ) from error
            self._history.append({"role": "assistant", "content": reply})
            if self._conversation_store is not None:
                self._history = self._history[-self._context_limit :]
        except BaseException as error:
            if self._history and self._history[-1] is user_message:
                self._history.pop()
            if isinstance(error, ProviderError):
                raise AgentUnavailable(str(error)) from error
            raise
