"""Confirmation-gated model tools backed by local vision processing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from argus.tools.runtime import ToolDefinition
from argus.vision.interfaces import VisionAnalyzer


def build_vision_tool_definitions(
    analyzer: VisionAnalyzer,
) -> list[ToolDefinition]:
    """Expose local analysis while confirming before results reach the model."""

    def analyze_image(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = analyzer.analyze_image(str(arguments["path"]))
        return {"analysis": result.to_payload(), "summary": result.summary()}

    def inspect_camera(_: Mapping[str, Any]) -> Mapping[str, Any]:
        result = analyzer.capture_and_analyze()
        return {"analysis": result.to_payload(), "summary": result.summary()}

    return [
        ToolDefinition(
            name="analyze_local_image",
            description=(
                "Locally analyze one image from an approved folder for objects, QR "
                "content, anonymous faces, and gestures. The raw image is not uploaded, "
                "but analysis results are returned to the model, so confirmation is required."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 2048}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=analyze_image,
            confirmation="always",
        ),
        ToolDefinition(
            name="inspect_camera_once",
            description=(
                "Capture exactly one webcam frame, analyze it locally, discard the "
                "frame, and return only analysis results. Always requires confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=inspect_camera,
            confirmation="always",
        ),
    ]
