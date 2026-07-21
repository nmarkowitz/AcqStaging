"""Basic interactive control of three Zaber linear stages.

Fill in the serial port, serial numbers, limits, and motion parameters before
running this script. Running it opens the configured serial port. It performs
no home or movement command until the operator selects one action and enters
the exact confirmation requested on screen.

This is intentionally a top-level exploratory script: it has no helper
functions, classes, or ``if __name__ == "__main__"`` block.
"""

import time
from typing import Any

from zaber_motion import Units
from zaber_motion.ascii import Connection

# Replace these placeholders with the reviewed laboratory configuration.
SERIAL_PORT = "COM5"
BAUD_RATE = 115200
AXIS_NUMBER = 1

# Device discovery order is not used for assignment. Each logical stage is
# matched to its controller's unique serial number.
EXPECTED_SERIALS = {
    "x": "128442",
    "y": "126569",
    #"z": "None",
}

# Review these software limits against the physical installation before motion.
LIMITS_MM = {
    "x": (0.0, 300.0),
    "y": (0.0, 300.0),
    "z": (0.0, 100.0),
}

# Conservative example motion parameters; verify them for the actual stages.
VELOCITY_MM_S = {"x": 10.0, "y": 10.0, "z": 5.0}
ACCELERATION_MM_S2 = {"x": 50.0, "y": 50.0, "z": 25.0}
OPERATION_TIMEOUT_S = 60.0

if SERIAL_PORT == "COM_REPLACE" or any(
    serial.startswith("REPLACE_WITH") for serial in EXPECTED_SERIALS.values()
):
    raise SystemExit(
        "Set SERIAL_PORT and all three EXPECTED_SERIALS values before running."
    )

connection: Any | None = None
axes: dict[str, Any] = {}
devices_by_axis: dict[str, Any] = {}

try:
    # Open the Zaber ASCII serial connection and discover its daisy-chained devices.
    #connection = Connection.open_serial_port(SERIAL_PORT, BAUD_RATE)
    connection = Connection.open_serial_port(SERIAL_PORT)
    devices = list(connection.detect_devices())
    print(f"Discovered {len(devices)} Zaber device(s) on {SERIAL_PORT}:")
    for device in devices:
        print(
            f"  address={device.device_address}, "
            f"serial={device.serial_number}, name={device.name}"
        )

    # Match X, Y, and Z by serial number instead of relying on discovery order.
    for axis_name, expected_serial in EXPECTED_SERIALS.items():
        matches = [
            device
            for device in devices
            if str(device.serial_number) == expected_serial
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"{axis_name.upper()}: expected one device with serial "
                f"{expected_serial}, found {len(matches)}"
            )
        devices_by_axis[axis_name] = matches[0]
        axes[axis_name] = matches[0].get_axis(AXIS_NUMBER)

    # Report status and position without moving any stage.
    print("\nCurrent stage status:")
    for axis_name, axis in axes.items():
        print(
            f"  {axis_name.upper()}: "
            f"homed={axis.is_homed()}, busy={axis.is_busy()}, "
            f"position={axis.get_position(Units.LENGTH_MILLIMETRES):.4f} mm"
        )

    print("\nAvailable actions: status, identify, home, absolute, relative, stop, quit")
    action = input("Choose one action: ").strip().lower()

    if action in {"quit", "status", ""}:
        print("No hardware command requested.")
    elif action not in {"identify", "home", "absolute", "relative", "stop"}:
        raise ValueError(f"Unknown action: {action!r}")
    else:
        axis_name = input("Choose axis (x, y, or z): ").strip().lower()
        if axis_name not in axes:
            raise ValueError(f"Unknown axis: {axis_name!r}")

        axis = axes[axis_name]
        device = devices_by_axis[axis_name]

        if action == "identify":
            acknowledgement = input(
                f"Type IDENTIFY {axis_name.upper()} to blink that device: "
            ).strip()
            if acknowledgement != f"IDENTIFY {axis_name.upper()}":
                raise RuntimeError("Identify cancelled: confirmation did not match")
            device.identify()
            print(f"Identify command sent to {axis_name.upper()}.")

        elif action == "stop":
            acknowledgement = input(
                f"Type STOP {axis_name.upper()} to request a stop: "
            ).strip()
            if acknowledgement != f"STOP {axis_name.upper()}":
                raise RuntimeError("Stop cancelled: confirmation did not match")
            axis.stop(wait_until_idle=False)
            print(f"Stop requested for {axis_name.upper()}.")

        elif action == "home":
            acknowledgement = input(
                f"Type HOME {axis_name.upper()} to authorize physical homing: "
            ).strip()
            if acknowledgement != f"HOME {axis_name.upper()}":
                raise RuntimeError("Home cancelled: confirmation did not match")

            axis.home(wait_until_idle=False)
            deadline = time.monotonic() + OPERATION_TIMEOUT_S
            while axis.is_busy():
                if time.monotonic() >= deadline:
                    axis.stop(wait_until_idle=False)
                    raise TimeoutError(
                        f"{axis_name.upper()} home timed out; stop requested"
                    )
                time.sleep(0.1)
            print(f"{axis_name.upper()} home completed.")

        else:
            requested_mm = float(input("Enter the requested value in millimetres: "))
            current_mm = float(axis.get_position(Units.LENGTH_MILLIMETRES))
            destination_mm = (
                requested_mm if action == "absolute" else current_mm + requested_mm
            )
            minimum_mm, maximum_mm = LIMITS_MM[axis_name]
            if not minimum_mm <= destination_mm <= maximum_mm:
                raise ValueError(
                    f"Destination {destination_mm:.4f} mm is outside the configured "
                    f"{axis_name.upper()} limits [{minimum_mm}, {maximum_mm}] mm"
                )
            if not axis.is_homed():
                raise RuntimeError(
                    f"{axis_name.upper()} movement refused because the stage is not homed"
                )

            acknowledgement = input(
                f"Type MOVE {axis_name.upper()} TO {destination_mm:.4f} MM "
                "to authorize physical motion: "
            ).strip()
            expected = f"MOVE {axis_name.upper()} TO {destination_mm:.4f} MM"
            if acknowledgement != expected:
                raise RuntimeError("Movement cancelled: confirmation did not match")

            axis.move_absolute(
                destination_mm,
                Units.LENGTH_MILLIMETRES,
                wait_until_idle=False,
                velocity=VELOCITY_MM_S[axis_name],
                velocity_unit=Units.VELOCITY_MILLIMETRES_PER_SECOND,
                acceleration=ACCELERATION_MM_S2[axis_name],
                acceleration_unit=Units.ACCELERATION_MILLIMETRES_PER_SECOND_SQUARED,
            )
            deadline = time.monotonic() + OPERATION_TIMEOUT_S
            while axis.is_busy():
                if time.monotonic() >= deadline:
                    axis.stop(wait_until_idle=False)
                    raise TimeoutError(
                        f"{axis_name.upper()} move timed out; stop requested"
                    )
                time.sleep(0.1)
            print(
                f"{axis_name.upper()} move completed at "
                f"{axis.get_position(Units.LENGTH_MILLIMETRES):.4f} mm."
            )
finally:
    # Always close the serial connection, including after validation or API errors.
    if connection is not None:
        connection.close()
        print("Zaber connection closed.")
