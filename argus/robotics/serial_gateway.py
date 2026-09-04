"""Bounded newline serial transport for approved Arduino and ESP32 devices."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import re
import secrets
import time
from typing import Any

from argus.config import RoboticsDeviceConfig
from argus.robotics.interfaces import (
    DeviceProtocolError,
    RoboticsError,
    TelemetryValue,
)


_MAX_FRAME_BYTES = 4096
_MAX_UNRELATED_FRAMES = 8
_TOKEN = re.compile(r"[a-zA-Z0-9_-]{1,50}")
_CHANNEL = re.compile(r"[a-z][a-z0-9_]{0,39}")


class SerialDeviceGateway:
    """Use the fixed ARGUS/1 protocol without arbitrary serial commands."""

    def __init__(
        self,
        config: RoboticsDeviceConfig,
        *,
        serial_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._serial_factory = serial_factory
        self._sleep = sleep
        self._connection: Any | None = None

    def _open(self) -> Any:
        if self._connection is not None:
            return self._connection
        factory = self._serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as error:
                raise RoboticsError(
                    "Serial support requires the declared pyserial dependency."
                ) from error
            factory = serial.Serial
        try:
            connection = factory(
                port=self._config.port,
                baudrate=self._config.baud_rate,
                timeout=self._config.timeout_seconds,
                write_timeout=self._config.timeout_seconds,
            )
            if self._config.startup_delay_seconds:
                self._sleep(self._config.startup_delay_seconds)
            connection.reset_input_buffer()
        except Exception as error:
            raise RoboticsError(
                f"Could not open approved serial device {self._config.device_id} "
                f"on {self._config.port}: {error}"
            ) from error
        self._connection = connection
        return connection

    @staticmethod
    def _safe_scalar(name: str, value: object) -> TelemetryValue:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        if isinstance(value, str) and len(value) <= 100 and not any(
            ord(character) < 32 for character in value
        ):
            return value
        raise DeviceProtocolError(
            f"Device returned an invalid or oversized value for '{name}'."
        )

    @staticmethod
    def _payload(text: str) -> Mapping[str, object]:
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as error:
            raise DeviceProtocolError("Device returned invalid JSON.") from error
        if not isinstance(decoded, dict) or len(decoded) > 32:
            raise DeviceProtocolError(
                "Device payload must be a JSON object with at most 32 fields."
            )
        return decoded

    def _exchange(self, operation: str, *arguments: str) -> Mapping[str, object]:
        if not _TOKEN.fullmatch(operation) or any(
            not _TOKEN.fullmatch(argument) for argument in arguments
        ):
            raise RoboticsError("Blocked an invalid serial protocol token.")
        request_id = secrets.token_hex(6)
        request = " ".join(("ARGUS/1", request_id, operation, *arguments)) + "\n"
        connection = self._open()
        try:
            connection.write(request.encode("ascii"))
            connection.flush()
            for _ in range(_MAX_UNRELATED_FRAMES):
                raw = connection.read_until(b"\n", size=_MAX_FRAME_BYTES + 1)
                if not raw:
                    raise RoboticsError(
                        f"Device {self._config.device_id} did not respond before timeout."
                    )
                if len(raw) > _MAX_FRAME_BYTES or not raw.endswith(b"\n"):
                    raise DeviceProtocolError(
                        "Device response exceeded the bounded line protocol."
                    )
                try:
                    line = raw[:-1].decode("utf-8", errors="strict").rstrip("\r")
                except UnicodeDecodeError as error:
                    raise DeviceProtocolError(
                        "Device response was not valid UTF-8."
                    ) from error
                parts = line.split(" ", 3)
                if len(parts) < 3 or parts[0] != "ARGUS/1":
                    raise DeviceProtocolError("Device used an invalid protocol frame.")
                if parts[1] != request_id:
                    continue
                status = parts[2]
                payload_text = parts[3] if len(parts) == 4 else ""
                if status == "ERROR":
                    safe_error = " ".join(payload_text.split())[:200]
                    raise RoboticsError(
                        f"Device {self._config.device_id} rejected the operation"
                        + (f": {safe_error}" if safe_error else ".")
                    )
                if status != "OK":
                    raise DeviceProtocolError("Device returned an unknown status token.")
                return self._payload(payload_text)
        except (RoboticsError, DeviceProtocolError):
            raise
        except Exception as error:
            self.close()
            raise RoboticsError(
                f"Serial communication with {self._config.device_id} failed: {error}"
            ) from error
        raise DeviceProtocolError("Device did not return the matching response frame.")

    @staticmethod
    def _state(payload: Mapping[str, object]) -> str:
        state = payload.get("state")
        if not isinstance(state, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,39}", state
        ):
            raise DeviceProtocolError("Device returned an invalid state.")
        return state

    def status(self) -> tuple[str, str | None]:
        payload = self._exchange("STATUS")
        state = self._state(payload)
        firmware = payload.get("firmware")
        if firmware is not None and (
            not isinstance(firmware, str)
            or len(firmware) > 100
            or any(ord(character) < 32 for character in firmware)
        ):
            raise DeviceProtocolError("Device returned an invalid firmware name.")
        return state, firmware

    def telemetry(self) -> dict[str, TelemetryValue]:
        payload = self._exchange("TELEMETRY")
        readings: dict[str, TelemetryValue] = {}
        for name, value in payload.items():
            if not isinstance(name, str) or not _CHANNEL.fullmatch(name):
                raise DeviceProtocolError("Device returned an invalid telemetry name.")
            readings[name] = self._safe_scalar(name, value)
        return readings

    def actuate(self, actuator: str, value: int) -> str:
        payload = self._exchange("ACTUATE", actuator, str(value))
        return self._state(payload)

    def emergency_stop(self) -> str:
        payload = self._exchange("ESTOP")
        return self._state(payload)

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
