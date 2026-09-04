"""Provider-independent local tools for Argus."""

from argus.tools.applications import build_installed_application_tool_definitions
from argus.tools.computer import (
    build_computer_tool_definitions,
    build_computer_tool_runtime,
)
from argus.tools.runtime import ToolDefinition, ToolRuntime
from argus.tools.web import build_web_tool_definitions

__all__ = [
    "ToolDefinition",
    "ToolRuntime",
    "build_installed_application_tool_definitions",
    "build_computer_tool_definitions",
    "build_computer_tool_runtime",
    "build_web_tool_definitions",
]
