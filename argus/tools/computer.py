"""Conservative Windows computer tools used during Argus Phase 2."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
from typing import Any
import webbrowser

from argus.config import ToolsConfig
from argus.tools.applications import build_installed_application_tool_definitions
from argus.tools.runtime import Confirmer, ToolDefinition, ToolRuntime
from argus.tools.web import build_web_tool_definitions, validate_public_url


_SKIPPED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _system_info(_: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
    }


def _find_files(
    arguments: Mapping[str, Any], roots: tuple[Path, ...]
) -> Mapping[str, Any]:
    query = str(arguments["query"]).casefold()
    maximum = int(arguments.get("max_results", 20))
    matches: list[str] = []

    for root in roots:
        for current, directories, filenames in os.walk(root, onerror=lambda _: None):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in _SKIPPED_DIRECTORIES
                and not directory.startswith(".")
            )
            for filename in sorted(filenames):
                path = Path(current) / filename
                relative = path.relative_to(root)
                if query in str(relative).casefold():
                    matches.append(str(path))
                    if len(matches) >= maximum:
                        return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def _open_application(
    arguments: Mapping[str, Any], applications: Mapping[str, str]
) -> Mapping[str, Any]:
    alias = str(arguments["application"])
    executable = applications[alias]
    process = subprocess.Popen(
        [executable],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    return {"application": alias, "started": True, "process_id": process.pid}


def _open_website(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    url = validate_public_url(str(arguments["url"]))
    opened = webbrowser.open(url, new=2)
    if not opened:
        raise RuntimeError("The default browser did not accept the URL.")
    return {"url": url, "opened": True}


def _media_control(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("Media controls are currently implemented only for Windows.")
    action = str(arguments["action"])
    virtual_keys = {
        "play_pause": 0xB3,
        "next": 0xB0,
        "previous": 0xB1,
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "mute": 0xAD,
    }
    key = virtual_keys[action]
    _send_windows_media_key(key)
    return {"action": action, "sent": True}


def _send_windows_media_key(key: int) -> None:
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)  # type: ignore[attr-defined]
    ctypes.windll.user32.keybd_event(key, 0, 0x0002, 0)  # type: ignore[attr-defined]


def _resolve_command(program: str) -> str:
    """Resolve bundled Windows commands without trusting the current PATH first."""

    if platform.system() == "Windows":
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            raise FileNotFoundError("Windows did not report its system folder.")
        candidate = Path(system_root) / "System32" / program
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError(f"Approved Windows command is unavailable: {program}")

    executable = shutil.which(program)
    if executable is None:
        raise FileNotFoundError(f"Approved command is not installed: {program}")
    return executable


def _run_command(
    arguments: Mapping[str, Any], allowed_commands: tuple[str, ...], cwd: Path
) -> Mapping[str, Any]:
    program = str(arguments["program"])
    if program not in allowed_commands:
        raise PermissionError(f"Command is not allowlisted: {program}")
    executable = _resolve_command(program)
    command_arguments = [str(item) for item in arguments.get("arguments", [])]
    if any("\0" in item or len(item) > 512 for item in command_arguments):
        raise ValueError("Command arguments contain an invalid or oversized value.")

    completed = subprocess.run(
        [executable, *command_arguments],
        shell=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    output_limit = 12_000
    return {
        "program": program,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[:output_limit],
        "stderr": completed.stderr[:output_limit],
        "output_truncated": (
            len(completed.stdout) > output_limit or len(completed.stderr) > output_limit
        ),
    }


def build_computer_tool_definitions(config: ToolsConfig) -> list[ToolDefinition]:
    """Build the exact, configuration-scoped Phase 2 tool definitions."""

    applications = {item.alias: item.executable for item in config.applications}
    app_aliases = list(applications)
    command_names = list(config.allowed_commands)
    working_directory = config.allowed_roots[0]

    object_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    definitions = [
        ToolDefinition(
            name="get_system_info",
            description=(
                "Read basic operating-system, host, CPU-count, and Python information."
            ),
            parameters=object_schema,
            handler=_system_info,
        ),
        ToolDefinition(
            name="find_files",
            description=(
                "Find file paths whose names or relative paths contain a query. "
                "Search is restricted to user-approved folders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _find_files(arguments, config.allowed_roots),
        ),
        ToolDefinition(
            name="open_application",
            description="Open one application from the user's approved application list.",
            parameters={
                "type": "object",
                "properties": {
                    "application": {"type": "string", "enum": app_aliases},
                },
                "required": ["application"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _open_application(arguments, applications),
        ),
        ToolDefinition(
            name="open_website",
            description=(
                "Open any public HTTP or HTTPS website requested by the user in the "
                "default browser. This is not limited to the named web-app catalog."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "minLength": 8, "maxLength": 2048},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_open_website,
        ),
        ToolDefinition(
            name="media_control",
            description="Send one low-risk media key to Windows.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play_pause",
                            "next",
                            "previous",
                            "volume_up",
                            "volume_down",
                            "mute",
                        ],
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=_media_control,
        ),
        ToolDefinition(
            name="run_command",
            description=(
                "Run one approved read-only system command without a shell. "
                "This always requires a fresh user confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "program": {"type": "string", "enum": command_names},
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 512},
                    },
                },
                "required": ["program"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _run_command(
                arguments, config.allowed_commands, working_directory
            ),
            confirmation="always",
        ),
    ]
    definitions.extend(build_installed_application_tool_definitions(config))
    definitions.extend(build_web_tool_definitions(config))
    return definitions


def build_computer_tool_runtime(
    config: ToolsConfig, *, confirmer: Confirmer | None = None
) -> ToolRuntime:
    """Build a standalone runtime for the computer tool set."""

    return ToolRuntime(
        build_computer_tool_definitions(config),
        confirmer=confirmer,
        max_rounds=config.max_rounds,
    )
