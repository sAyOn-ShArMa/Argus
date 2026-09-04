from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from argus.ai.provider import ToolCall
from argus.memory import LocalMemoryStore, build_memory_tool_definitions
from argus.tools import ToolRuntime


class MemoryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalMemoryStore(
            Path(self.temporary.name) / "argus.db",
            profile_id="owner",
            profile_name="Owner",
        )

    def test_model_memory_write_is_blocked_without_confirmation(self) -> None:
        runtime = ToolRuntime(
            build_memory_tool_definitions(self.store),
            confirmer=lambda definition, arguments: False,
        )

        result = json.loads(
            runtime.execute(
                ToolCall(
                    "1",
                    "save_memory",
                    '{"content":"My private project is Helios","category":"project"}',
                )
            )
        )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.store.list_memories(), [])

    def test_confirmed_memory_can_be_recalled(self) -> None:
        runtime = ToolRuntime(
            build_memory_tool_definitions(self.store),
            confirmer=lambda definition, arguments: True,
        )
        runtime.execute(
            ToolCall(
                "1",
                "save_memory",
                '{"content":"The robotics project is Helios","category":"project"}',
            )
        )

        result = json.loads(
            runtime.execute(
                ToolCall("2", "search_memories", '{"query":"Helios"}')
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["memories"][0]["category"], "project")

    def test_model_has_no_permanent_deletion_tool(self) -> None:
        names = {
            definition.name for definition in build_memory_tool_definitions(self.store)
        }

        self.assertFalse(any("delete" in name or "forget" in name for name in names))


if __name__ == "__main__":
    unittest.main()
