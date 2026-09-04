# Argus Tier 7 device firmware

The starter sketch implements the bounded `ARGUS/1` newline protocol over USB
serial for Arduino and ESP32 boards. It controls only the board's built-in LED
and reports `uptime_ms` plus `led_on`; it contains no motor, servo, relay, camera,
or autonomous-motion code.

## Safe first hardware test

1. Disconnect motors, servos, relays, batteries, and external power.
2. Upload `argus_serial_starter/argus_serial_starter.ino` using the Arduino IDE.
3. Close the Arduino Serial Monitor so it does not hold the COM port.
4. In the Argus virtual environment, list ports:

   ```powershell
   .\.venv\Scripts\python.exe -m serial.tools.list_ports
   ```

5. Replace the simulator entry in `config/argus.json` with an explicitly
   allowlisted serial device. Change `COM4` to the exact port reported above:

   ```json
   {
     "id": "arduino",
     "name": "Arduino Starter",
     "transport": "serial",
     "port": "COM4",
     "baud_rate": 115200,
     "timeout_seconds": 2.0,
     "startup_delay_seconds": 2.0,
     "actuators_enabled": true,
     "allowed_actuators": ["led"],
     "telemetry": {
       "uptime_ms": "ms",
       "led_on": ""
     }
   }
   ```

6. Restart Argus. Test `/device-status arduino`, `/telemetry arduino`, then
   `/actuate arduino led 1`. Only the full word `yes` sends that one LED command.
7. Use `/estop arduino` to send the fixed stop command and turn the LED off.

Opening a serial port resets many Arduino-class boards; `startup_delay_seconds`
allows the board to reboot before Argus sends its first request. Argus opens only
the exact configured local port, caps each response at 4096 bytes, rejects
malformed frames, filters telemetry to configured channel names, and never
provides a raw serial-console tool.

## Adding real actuators

Do not connect a motor, servo, relay, or high-current load directly to a
microcontroller pin. Select the correct driver, separate power supply, common
grounding, voltage, current limit, fuse, and physical emergency stop for the
specific hardware. Keep `actuators_enabled` false until the wiring and firmware
are independently tested. Argus still requires a fresh confirmation for every
write after it is enabled.

The current host protocol recognizes bounded values for:

- `led`: `0` or `1`
- `servo`: `0` through `180`
- `motor_left` and `motor_right`: `-100` through `100`

The starter firmware deliberately rejects everything except `led`. Device
firmware must independently enforce the same or stricter limits; laptop-side
validation is not a substitute for hardware limit switches or motor protection.
