"""Deterministic local robotics simulator used before hardware is connected."""

from __future__ import annotations

from argus.config import RoboticsDeviceConfig
from argus.robotics.interfaces import TelemetryValue


class SimulatedDeviceGateway:
    def __init__(self, config: RoboticsDeviceConfig) -> None:
        self._config = config
        self._telemetry: dict[str, TelemetryValue] = dict(
            config.simulated_telemetry
        )
        self._actuators = {name: 0 for name in config.allowed_actuators}
        self._state = "idle"

    def status(self) -> tuple[str, str | None]:
        return self._state, "argus-simulator-1.0"

    def telemetry(self) -> dict[str, TelemetryValue]:
        return self._telemetry.copy()

    def actuate(self, actuator: str, value: int) -> str:
        self._actuators[actuator] = value
        if actuator.startswith("motor_"):
            moving = any(
                current != 0
                for name, current in self._actuators.items()
                if name.startswith("motor_")
            )
            self._state = "moving" if moving else "idle"
        elif actuator == "servo":
            self._state = "positioned"
        else:
            self._state = "idle"
        return self._state

    def emergency_stop(self) -> str:
        for actuator in self._actuators:
            if actuator.startswith("motor_"):
                self._actuators[actuator] = 0
        self._state = "emergency_stopped"
        return self._state

    def close(self) -> None:
        return None
