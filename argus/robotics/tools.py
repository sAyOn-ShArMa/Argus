"""Model tools for allowlisted robotics and IoT devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from argus.robotics.interfaces import RoboticsController
from argus.tools.runtime import ToolDefinition


def build_robotics_tool_definitions(
    controller: RoboticsController,
) -> list[ToolDefinition]:
    """Expose bounded reads, confirmed writes, and a fixed emergency stop."""

    summaries = controller.list_devices()
    device_ids = [device.device_id for device in summaries]
    actuator_names = sorted(
        {
            actuator
            for device in summaries
            for actuator in device.allowed_actuators
        }
    )
    device_parameter = {"type": "string", "enum": device_ids}

    def list_devices(_: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"devices": [device.to_payload() for device in controller.list_devices()]}

    def status(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = controller.status(str(arguments["device_id"]))
        return {"status": result.to_payload(), "summary": result.summary()}

    def telemetry(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = controller.telemetry(str(arguments["device_id"]))
        return {"telemetry": result.to_payload(), "summary": result.summary()}

    def actuate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = controller.actuate(
            str(arguments["device_id"]),
            str(arguments["actuator"]),
            int(arguments["value"]),
        )
        return {"actuation": result.to_payload(), "summary": result.summary()}

    def emergency_stop(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = controller.emergency_stop(str(arguments["device_id"]))
        return {"emergency_stop": result.to_payload(), "summary": result.summary()}

    object_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    device_schema = {
        "type": "object",
        "properties": {"device_id": device_parameter},
        "required": ["device_id"],
        "additionalProperties": False,
    }
    definitions = [
        ToolDefinition(
            name="list_robotics_devices",
            description=(
                "List the user's explicitly configured robotics/IoT devices. "
                "This does not contact hardware."
            ),
            parameters=object_schema,
            handler=list_devices,
        ),
        ToolDefinition(
            name="get_device_status",
            description=(
                "Read one allowlisted device's current connection and state. "
                "Confirmation is required before local device data reaches the model."
            ),
            parameters=device_schema,
            handler=status,
            confirmation="always",
        ),
        ToolDefinition(
            name="read_device_telemetry",
            description=(
                "Read one bounded snapshot of allowlisted sensor channels. "
                "Confirmation is required before local sensor data reaches the model."
            ),
            parameters=device_schema,
            handler=telemetry,
            confirmation="always",
        ),
        ToolDefinition(
            name="emergency_stop_device",
            description=(
                "Send only the fixed stop command to one allowlisted device. "
                "This cannot set speed or position and is intentionally immediate."
            ),
            parameters=device_schema,
            handler=emergency_stop,
        ),
    ]
    if actuator_names:
        definitions.insert(
            -1,
            ToolDefinition(
                name="actuate_device",
                description=(
                    "Perform exactly one allowlisted actuator write. Every call "
                    "requires fresh user confirmation and bounded numeric values."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "device_id": device_parameter,
                        "actuator": {"type": "string", "enum": actuator_names},
                        "value": {
                            "type": "integer",
                            "minimum": -100,
                            "maximum": 180,
                        },
                    },
                    "required": ["device_id", "actuator", "value"],
                    "additionalProperties": False,
                },
                handler=actuate,
                confirmation="always",
            ),
        )
    return definitions
