from __future__ import annotations

import json
import unittest

from argus.ai.provider import ToolCall
from argus.config import (
    RoboticsConfig,
    RoboticsDeviceConfig,
    TelemetryChannelConfig,
)
from argus.robotics import RoboticsError, RoboticsService
from argus.robotics.serial_gateway import SerialDeviceGateway
from argus.robotics.tools import build_robotics_tool_definitions
from argus.tools.runtime import ToolRuntime


def simulated_config(*, actuators_enabled: bool = True) -> RoboticsConfig:
    return RoboticsConfig(
        enabled=True,
        devices=(
            RoboticsDeviceConfig(
                device_id="sim_robot",
                name="Simulator",
                transport="simulator",
                actuators_enabled=actuators_enabled,
                allowed_actuators=("led", "servo", "motor_left", "motor_right"),
                telemetry_channels=(
                    TelemetryChannelConfig("distance_cm", "cm"),
                    TelemetryChannelConfig("battery_percent", "%"),
                ),
                simulated_telemetry=(
                    ("distance_cm", 240.0),
                    ("battery_percent", 87),
                ),
            ),
        ),
    )


class RoboticsServiceTests(unittest.TestCase):
    def test_simulator_reports_allowlisted_status_and_telemetry(self) -> None:
        service = RoboticsService(simulated_config())

        status = service.status("sim_robot")
        telemetry = service.telemetry("sim_robot")

        self.assertTrue(status.connected)
        self.assertEqual(status.state, "idle")
        self.assertEqual(
            [reading.name for reading in telemetry.readings],
            ["distance_cm", "battery_percent"],
        )
        self.assertIn("240.0 cm", telemetry.summary())

    def test_actuation_is_bounded_and_emergency_stop_is_fixed(self) -> None:
        service = RoboticsService(simulated_config())

        moving = service.actuate("sim_robot", "motor_left", 50)
        stopped = service.emergency_stop("sim_robot")

        self.assertEqual(moving.state, "moving")
        self.assertEqual(stopped.state, "emergency_stopped")
        with self.assertRaisesRegex(RoboticsError, "between -100 and 100"):
            service.actuate("sim_robot", "motor_left", 101)

    def test_disabled_actuators_cannot_be_written(self) -> None:
        service = RoboticsService(simulated_config(actuators_enabled=False))

        with self.assertRaisesRegex(RoboticsError, "writes are disabled"):
            service.actuate("sim_robot", "led", 1)

    def test_unknown_device_is_never_contacted(self) -> None:
        service = RoboticsService(simulated_config())

        with self.assertRaisesRegex(RoboticsError, "not allowlisted"):
            service.telemetry("other")


class RoboticsToolTests(unittest.TestCase):
    def test_denial_prevents_actuator_write(self) -> None:
        service = RoboticsService(simulated_config())
        runtime = ToolRuntime(
            build_robotics_tool_definitions(service),
            confirmer=lambda definition, arguments: False,
        )

        denied = json.loads(
            runtime.execute(
                ToolCall(
                    "1",
                    "actuate_device",
                    '{"device_id":"sim_robot","actuator":"motor_left","value":50}',
                )
            )
        )
        status = service.status("sim_robot")

        self.assertEqual(denied["status"], "denied")
        self.assertEqual(status.state, "idle")

    def test_telemetry_requires_confirmation_before_reaching_model(self) -> None:
        service = RoboticsService(simulated_config())
        confirmations: list[str] = []
        runtime = ToolRuntime(
            build_robotics_tool_definitions(service),
            confirmer=lambda definition, arguments: confirmations.append(
                definition.name
            )
            or True,
        )

        result = json.loads(
            runtime.execute(
                ToolCall(
                    "1", "read_device_telemetry", '{"device_id":"sim_robot"}'
                )
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(confirmations, ["read_device_telemetry"])

    def test_emergency_stop_does_not_wait_for_confirmation(self) -> None:
        service = RoboticsService(simulated_config())
        service.actuate("sim_robot", "motor_left", 50)
        confirmations: list[str] = []
        runtime = ToolRuntime(
            build_robotics_tool_definitions(service),
            confirmer=lambda definition, arguments: confirmations.append(
                definition.name
            )
            or False,
        )

        result = json.loads(
            runtime.execute(
                ToolCall(
                    "1", "emergency_stop_device", '{"device_id":"sim_robot"}'
                )
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(confirmations, [])
        self.assertEqual(service.status("sim_robot").state, "emergency_stopped")


class SerialProtocolTests(unittest.TestCase):
    def test_bounded_protocol_reads_status_telemetry_and_actuation(self) -> None:
        class FakeSerial:
            def __init__(self, **kwargs) -> None:
                self.responses: list[bytes] = []
                self.closed = False
                self.kwargs = kwargs

            def reset_input_buffer(self):
                pass

            def write(self, request: bytes):
                parts = request.decode("ascii").strip().split()
                request_id, operation = parts[1], parts[2]
                payloads = {
                    "STATUS": '{"state":"ready","firmware":"starter-1.0"}',
                    "TELEMETRY": '{"distance_cm":123.5}',
                    "ACTUATE": '{"state":"positioned"}',
                    "ESTOP": '{"state":"emergency_stopped"}',
                }
                response = f"ARGUS/1 {request_id} OK {payloads[operation]}\n"
                self.responses.append(response.encode())
                return len(request)

            def flush(self):
                pass

            def read_until(self, expected, size):
                return self.responses.pop(0)

            def close(self):
                self.closed = True

        created: list[FakeSerial] = []

        def factory(**kwargs):
            connection = FakeSerial(**kwargs)
            created.append(connection)
            return connection

        config = RoboticsDeviceConfig(
            device_id="arduino",
            name="Arduino",
            transport="serial",
            port="COM4",
            startup_delay_seconds=0,
            actuators_enabled=True,
            allowed_actuators=("servo",),
            telemetry_channels=(TelemetryChannelConfig("distance_cm", "cm"),),
        )
        gateway = SerialDeviceGateway(config, serial_factory=factory)

        self.assertEqual(gateway.status(), ("ready", "starter-1.0"))
        self.assertEqual(gateway.telemetry(), {"distance_cm": 123.5})
        self.assertEqual(gateway.actuate("servo", 90), "positioned")
        self.assertEqual(gateway.emergency_stop(), "emergency_stopped")
        gateway.close()

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
