from __future__ import annotations

import json
import unittest

from argus.ai.provider import ToolCall
from argus.tools.runtime import ToolDefinition, ToolRuntime


PARAMETERS = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "enum": ["safe"]},
    },
    "required": ["value"],
    "additionalProperties": False,
}


class ToolRuntimeTests(unittest.TestCase):
    def test_validates_arguments_before_calling_handler(self) -> None:
        handled: list[dict[str, object]] = []
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "demo",
                    "Demo",
                    PARAMETERS,
                    lambda arguments: handled.append(dict(arguments)) or {"done": True},
                )
            ]
        )

        result = json.loads(runtime.execute(ToolCall("1", "demo", '{"value": 3}')))

        self.assertFalse(result["ok"])
        self.assertEqual(handled, [])

    def test_rejects_unknown_fields_and_unknown_tools(self) -> None:
        runtime = ToolRuntime(
            [ToolDefinition("demo", "Demo", PARAMETERS, lambda arguments: {})]
        )

        extra = json.loads(
            runtime.execute(
                ToolCall("1", "demo", '{"value": "safe", "extra": true}')
            )
        )
        unknown = json.loads(runtime.execute(ToolCall("2", "missing", "{}")))

        self.assertIn("Unknown argument", extra["error"])
        self.assertIn("Unknown tool", unknown["error"])

    def test_denial_prevents_sensitive_handler(self) -> None:
        handled: list[bool] = []
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "demo",
                    "Demo",
                    PARAMETERS,
                    lambda arguments: handled.append(True) or {},
                    confirmation="always",
                )
            ],
            confirmer=lambda definition, arguments: False,
        )

        result = json.loads(
            runtime.execute(ToolCall("1", "demo", '{"value": "safe"}'))
        )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(handled, [])

    def test_sensitive_handler_runs_once_after_confirmation(self) -> None:
        handled: list[dict[str, object]] = []
        confirmations: list[str] = []
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "demo",
                    "Demo",
                    PARAMETERS,
                    lambda arguments: handled.append(dict(arguments)) or {"done": True},
                    confirmation="always",
                )
            ],
            confirmer=lambda definition, arguments: confirmations.append(
                definition.name
            )
            or True,
        )

        result = json.loads(
            runtime.execute(ToolCall("1", "demo", '{"value": "safe"}'))
        )

        self.assertTrue(result["ok"])
        self.assertEqual(confirmations, ["demo"])
        self.assertEqual(handled, [{"value": "safe"}])
        self.assertIn("demo completed successfully", runtime.verified_action_context)

    def test_denied_action_does_not_create_verified_receipt(self) -> None:
        runtime = ToolRuntime(
            [
                ToolDefinition(
                    "demo",
                    "Demo",
                    PARAMETERS,
                    lambda arguments: {},
                    confirmation="always",
                )
            ],
            confirmer=lambda definition, arguments: False,
        )

        runtime.execute(ToolCall("1", "demo", '{"value": "safe"}'))

        self.assertEqual(runtime.verified_action_context, "")


if __name__ == "__main__":
    unittest.main()
