"""Configuration-scoped robotics and IoT service."""

from __future__ import annotations

from collections.abc import Callable

from argus.config import RoboticsConfig, RoboticsDeviceConfig
from argus.robotics.interfaces import (
    ActuationResult,
    DeviceGateway,
    DeviceStatus,
    DeviceSummary,
    EmergencyStopResult,
    RoboticsError,
    TelemetryReading,
    TelemetrySnapshot,
)
from argus.robotics.serial_gateway import SerialDeviceGateway
from argus.robotics.simulator import SimulatedDeviceGateway


_ACTUATOR_LIMITS = {
    "led": (0, 1),
    "servo": (0, 180),
    "motor_left": (-100, 100),
    "motor_right": (-100, 100),
}


class RoboticsService:
    """Own only explicitly configured devices and their narrow gateways."""

    def __init__(
        self,
        config: RoboticsConfig,
        *,
        gateway_factory: Callable[[RoboticsDeviceConfig], DeviceGateway] | None = None,
    ) -> None:
        if not config.enabled:
            raise RoboticsError("Robotics is disabled in the configuration.")
        self._config = config
        self._devices = {device.device_id: device for device in config.devices}
        self._gateways: dict[str, DeviceGateway] = {}
        for device in config.devices:
            if gateway_factory is not None:
                gateway = gateway_factory(device)
            elif device.transport == "simulator":
                gateway = SimulatedDeviceGateway(device)
            elif device.transport == "serial":
                gateway = SerialDeviceGateway(device)
            else:
                raise RoboticsError(f"Unsupported device transport: {device.transport}")
            self._gateways[device.device_id] = gateway

    @property
    def description(self) -> str:
        transports = sorted({device.transport for device in self._config.devices})
        return (
            f"{len(self._config.devices)} allowlisted device(s) via "
            f"{', '.join(transports)}; no background control"
        )

    def _device(self, device_id: str) -> tuple[RoboticsDeviceConfig, DeviceGateway]:
        config = self._devices.get(device_id)
        gateway = self._gateways.get(device_id)
        if config is None or gateway is None:
            raise RoboticsError(f"Device '{device_id}' is not allowlisted.")
        return config, gateway

    def list_devices(self) -> tuple[DeviceSummary, ...]:
        return tuple(
            DeviceSummary(
                device_id=device.device_id,
                name=device.name,
                transport=device.transport,
                actuators_enabled=device.actuators_enabled,
                allowed_actuators=device.allowed_actuators,
            )
            for device in self._config.devices
        )

    def status(self, device_id: str) -> DeviceStatus:
        config, gateway = self._device(device_id)
        state, firmware = gateway.status()
        return DeviceStatus(
            device_id=config.device_id,
            name=config.name,
            transport=config.transport,
            connected=True,
            state=state,
            firmware=firmware,
        )

    def telemetry(self, device_id: str) -> TelemetrySnapshot:
        config, gateway = self._device(device_id)
        untrusted = gateway.telemetry()
        readings = tuple(
            TelemetryReading(channel.name, untrusted[channel.name], channel.unit)
            for channel in config.telemetry_channels
            if channel.name in untrusted
        )
        return TelemetrySnapshot(
            device_id=config.device_id,
            name=config.name,
            transport=config.transport,
            readings=readings,
        )

    @staticmethod
    def _validate_actuation(
        config: RoboticsDeviceConfig, actuator: str, value: int
    ) -> None:
        if not config.actuators_enabled:
            raise RoboticsError(
                f"Actuator writes are disabled for device '{config.device_id}'."
            )
        if actuator not in config.allowed_actuators:
            raise RoboticsError(
                f"Actuator '{actuator}' is not allowlisted for '{config.device_id}'."
            )
        limits = _ACTUATOR_LIMITS.get(actuator)
        if limits is None:
            raise RoboticsError(f"Actuator '{actuator}' has no safe value policy.")
        minimum, maximum = limits
        if isinstance(value, bool) or not isinstance(value, int):
            raise RoboticsError("Actuator values must be integers.")
        if not minimum <= value <= maximum:
            raise RoboticsError(
                f"{actuator} value must be between {minimum} and {maximum}."
            )

    def actuate(self, device_id: str, actuator: str, value: int) -> ActuationResult:
        config, gateway = self._device(device_id)
        self._validate_actuation(config, actuator, value)
        state = gateway.actuate(actuator, value)
        return ActuationResult(config.device_id, actuator, value, state)

    def emergency_stop(self, device_id: str) -> EmergencyStopResult:
        config, gateway = self._device(device_id)
        state = gateway.emergency_stop()
        return EmergencyStopResult(config.device_id, state)

    def close(self) -> None:
        for gateway in self._gateways.values():
            gateway.close()
