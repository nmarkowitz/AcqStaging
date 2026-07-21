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

# Choose an axis to work with 
ax_name = "y"
axis = axes[ax_name]
device = devices_by_axis[ax_name]

# Identify device
for _ in range(10):
    device.identify()
    time.sleep(0.2)

# Get some device info
device_minimum = axis.settings.get(
    "limit.min",
    Units.LENGTH_MILLIMETRES,
)
device_maximum = axis.settings.get(
    "limit.max",
    Units.LENGTH_MILLIMETRES,
)
n_indices = axis.get_number_of_index_positions()
print(f'Device range: {device_minimum}-{device_maximum}')
print(f'Num indices of device: {n_indices}')


# Home device
axis.home(wait_until_idle=True)
deadline = time.monotonic() + OPERATION_TIMEOUT_S
while axis.is_busy():
    if time.monotonic() >= deadline:
        axis.stop(wait_until_idle=False)
        raise TimeoutError(
            f"{axis_name.upper()} home timed out; stop requested"
        )
    time.sleep(0.1)
print(f"{axis_name.upper()} home completed.")


# Move
destination_mm = 50
axis.move_absolute(
    destination_mm,
    Units.LENGTH_MILLIMETRES,
    #wait_until_idle=False,
    #velocity=VELOCITY_MM_S[axis_name],
    #velocity_unit=Units.VELOCITY_MILLIMETRES_PER_SECOND,
    #acceleration=ACCELERATION_MM_S2[axis_name],
    #acceleration_unit=Units.ACCELERATION_MILLIMETRES_PER_SECOND_SQUARED,
)

# Move based on current position
current_mm = float(axis.get_position(Units.LENGTH_MILLIMETRES))

# Close connection
connection.close()
print("Zaber connection closed.")
