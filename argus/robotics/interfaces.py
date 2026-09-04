"""Provider-independent contracts and result types for robotics and IoT."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, TypeAlias


TelemetryValue: TypeAlias = str | int | float | bool


class RoboticsError(RuntimeError):
    """A configured device operation could not be completed safely."""


class DeviceProtocolError(RoboticsError):
    """A device returned malformed or unexpected protocol data."""


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    device_id: str
    name: str
    transport: str
    actuators_enabled: bool
    allowed_actuators: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        writes = "enabled with confirmation" if self.actuators_enabled else "disabled"
        return f"{self.device_id}: {self.name} [{self.transport}; writes {writes}]"


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    device_id: str
    name: str
    transport: str
    connected: bool
    state: str
    firmware: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        connection = "connected" if self.connected else "unavailable"
        firmware = f" | firmware: {self.firmware}" if self.firmware else ""
        return (
            f"{self.device_id} ({self.name}): {connection} via {self.transport}; "
            f"state: {self.state}{firmware}."
        )


@dataclass(frozen=True, slots=True)
class TelemetryReading:
    name: str
    value: TelemetryValue
    unit: str = ""

    def display(self) -> str:
        suffix = f" {self.unit}" if self.unit else ""
        return f"{self.name}={self.value}{suffix}"


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    device_id: str
    name: str
    transport: str
    readings: tuple[TelemetryReading, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "transport": self.transport,
            "readings": [asdict(reading) for reading in self.readings],
        }

    def summary(self) -> str:
        if not self.readings:
            return f"{self.device_id}: no allowlisted telemetry was returned."
        values = ", ".join(reading.display() for reading in self.readings)
        return f"{self.device_id} telemetry: {values}."


@dataclass(frozen=True, slots=True)
class ActuationResult:
    device_id: str
    actuator: str
    value: int
    state: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"{self.device_id}: set {self.actuator} to {self.value}; "
            f"device state is {self.state}."
        )


@dataclass(frozen=True, slots=True)
class EmergencyStopResult:
    device_id: str
    state: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return f"{self.device_id}: emergency stop sent; state is {self.state}."


class DeviceGateway(Protocol):
    def status(self) -> tuple[str, str | None]:
        """Return the device state and optional firmware version."""

    def telemetry(self) -> dict[str, TelemetryValue]:
        """Return untrusted raw telemetry for service-level allowlisting."""

    def actuate(self, actuator: str, value: int) -> str:
        """Perform one already-authorized actuator operation."""

    def emergency_stop(self) -> str:
        """Issue the fixed stop-only operation."""

    def close(self) -> None:
        """Release the transport."""


class RoboticsController(Protocol):
    @property
    def description(self) -> str:
        """Describe configured device transports without contacting them."""

    def list_devices(self) -> tuple[DeviceSummary, ...]:
        """List exactly the allowlisted devices."""

    def status(self, device_id: str) -> DeviceStatus:
        """Read one device's connection and state."""

    def telemetry(self, device_id: str) -> TelemetrySnapshot:
        """Read and allowlist one telemetry snapshot."""

    def actuate(self, device_id: str, actuator: str, value: int) -> ActuationResult:
        """Perform one configuration-allowed, already-confirmed write."""

    def emergency_stop(self, device_id: str) -> EmergencyStopResult:
        """Send the fixed emergency-stop operation."""

    def close(self) -> None:
        """Release all device transports."""
