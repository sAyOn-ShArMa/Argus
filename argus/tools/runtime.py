"""Validated tool dispatch and the hard confirmation boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from collections import deque
from dataclasses import dataclass
import json
from typing import Any, Literal

from argus.ai.provider import ToolCall, ToolSchema


ConfirmationPolicy = Literal["never", "always"]
ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Confirmer = Callable[["ToolDefinition", Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    confirmation: ConfirmationPolicy = "never"

    def schema(self) -> ToolSchema:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _validate_value(value: Any, schema: Mapping[str, Any], location: str) -> None:
    expected = schema.get("type")
    valid = True
    if expected == "string":
        valid = isinstance(value, str)
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "array":
        valid = isinstance(value, list)
    elif expected == "object":
        valid = isinstance(value, dict)
    elif expected == "boolean":
        valid = isinstance(value, bool)
    if not valid:
        raise ValueError(f"'{location}' must be a {expected}.")

    if "enum" in schema and value not in schema["enum"]:
        choices = ", ".join(str(choice) for choice in schema["enum"])
        raise ValueError(f"'{location}' must be one of: {choices}.")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"'{location}' is too short.")
        if len(value) > schema.get("maxLength", len(value)):
            raise ValueError(f"'{location}' is too long.")
    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value):
            raise ValueError(f"'{location}' is below the minimum.")
        if value > schema.get("maximum", value):
            raise ValueError(f"'{location}' is above the maximum.")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{location}[{index}]")


def _validate_arguments(arguments: Any, schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        if key not in arguments:
            raise ValueError(f"Missing required argument: '{key}'.")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ValueError(f"Unknown argument: '{unknown[0]}'.")
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if property_schema is not None:
            _validate_value(value, property_schema, key)
    return arguments


class ToolRuntime:
    """Expose tool schemas and execute only validated, policy-approved calls."""

    def __init__(
        self,
        definitions: list[ToolDefinition],
        *,
        confirmer: Confirmer | None = None,
        max_rounds: int = 6,
    ) -> None:
        names = [definition.name for definition in definitions]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique.")
        if not 1 <= max_rounds <= 12:
            raise ValueError("max_rounds must be between 1 and 12.")
        self._definitions = {definition.name: definition for definition in definitions}
        self._confirmer = confirmer
        self._verified_receipts: deque[str] = deque(maxlen=20)
        self.max_rounds = max_rounds

    @property
    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(definition.schema() for definition in self._definitions.values())

    @property
    def descriptions(self) -> tuple[str, ...]:
        return tuple(
            f"{definition.name} ({'confirmation required' if definition.confirmation == 'always' else 'low risk'})"
            for definition in self._definitions.values()
        )

    @property
    def verified_action_context(self) -> str:
        """Return trusted, data-free success receipts for later model turns."""

        if not self._verified_receipts:
            return ""
        receipts = "\n".join(f"- {item}" for item in self._verified_receipts)
        return (
            "VERIFIED LOCAL TOOL RECEIPTS FROM EARLIER TURNS:\n"
            f"{receipts}\n"
            "These lines are trusted runtime facts, not user claims. Never deny that "
            "these actions succeeded, never call their success messages inaccurate, "
            "and never claim you lack the listed capability. A receipt does not prove "
            "that an opened application is still running now."
        )

    def clear_receipts(self) -> None:
        self._verified_receipts.clear()

    @staticmethod
    def _result(**payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def execute(self, call: ToolCall) -> str:
        definition = self._definitions.get(call.name)
        if definition is None:
            return self._result(ok=False, error=f"Unknown tool: {call.name}")

        try:
            decoded = json.loads(call.arguments or "{}")
            arguments = _validate_arguments(decoded, definition.parameters)
        except (json.JSONDecodeError, ValueError) as error:
            return self._result(ok=False, error=f"Invalid arguments: {error}")

        if definition.confirmation == "always":
            if self._confirmer is None:
                return self._result(
                    ok=False,
                    status="denied",
                    error="Confirmation is unavailable, so the action was not performed.",
                )
            try:
                confirmed = self._confirmer(definition, arguments)
            except Exception as error:
                return self._result(
                    ok=False,
                    status="denied",
                    error=f"Confirmation failed: {error}",
                )
            if not confirmed:
                return self._result(
                    ok=False,
                    status="denied",
                    error="The user denied this action. It was not performed.",
                )

        try:
            result = dict(definition.handler(arguments))
            self._verified_receipts.append(
                f"{definition.name} completed successfully."
            )
            return self._result(ok=True, **result)
        except Exception as error:
            return self._result(
                ok=False,
                error=f"{type(error).__name__}: {error}",
            )
