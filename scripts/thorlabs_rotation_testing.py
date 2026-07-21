"""Exploratory BSC101/HDR50 connection and motion script.

WARNING: Running this file connects to real hardware, enables channel 1, homes
the HDR50, performs an absolute move, performs repeated relative moves, and
performs one final relative move. It is not a connection-only test.

The comments explain the existing code as written. They do not make its motion
sequence safe and do not add validation, limits, confirmations, or guaranteed
cleanup when an exception occurs.
"""

# Import sleep(), used to give Kinesis time to process state changes.
import time

# Import pythonnet's CLR bridge so Python can load and call .NET assemblies.
import clr

# Load the Kinesis assembly that discovers Thorlabs devices and provides
# DeviceManagerCLI.
clr.AddReference(
    "C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll"
)
# Load the generic Kinesis motor types, status objects, and motion parameters.
clr.AddReference(
    "C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll"
)
# Load the BSC-series benchtop stepper controller and channel implementation.
clr.AddReference(
    "C:\\Program Files\\Thorlabs\\Kinesis\\"
    "Thorlabs.MotionControl.Benchtop.StepperMotorCLI.dll"
)

# Import .NET Decimal plus the controller factory, device manager, and direction
# enumeration after their assemblies have been loaded above.
from System import Decimal  # noqa: E402
from Thorlabs.MotionControl.Benchtop.StepperMotorCLI import BenchtopStepperMotor  # noqa: E402
from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI  # noqa: E402
from Thorlabs.MotionControl.GenericMotorCLI import MotorDirection  # noqa: E402

# Ask Kinesis to refresh its list of attached Thorlabs USB devices. This scans
# for devices but does not itself open the BSC101 controller.
DeviceManagerCLI.BuildDeviceList()

# Store the BSC101 controller serial number as a string.
serial_no = "40531374"

dev = BenchtopStepperMotor.CreateBenchtopStepperMotor(serial_no)

print(dev.Connect(serial_no))
time.sleep(0.25)

# Sanity check we're actually talking to the motor/stage
print(f'Device Name: {dev.DeviceName}')
print(f'Is Connected: {dev.IsConnected}')

# init the channel we actually talk to
channel = dev.GetChannel(1) # Only one channel on the motor controller
channel_info = channel.GetDeviceInfo()

# sanity check channel connection
print(f'Channel Descript: {channel_info.Description}')

# init settings on the channel (seems archaic but essential)
channel_conf = channel.LoadMotorConfiguration(serial_no)
channel_conf.DeviceSettingsName = "HDR50"
time.sleep(0.25)
channel_settings = channel.GetSettings(channel.MotorDeviceSettings)

# This being returned as false does not mean update failed/rejected
channel_conf.UpdateCurrentConfiguration()
time.sleep(0.25)

# print enabled
print(f'Is enabled: {channel.Status.IsEnabled}')
time.sleep(0.25)

# Home stage
print('Homing...')
channel.Home(60000)
print('Home complete.')

time.sleep(2)

# Movement
# The rotation stage has position 0-359
timeout_ms = 10000

# Move to 180 degrees position (absolute)
channel.MoveTo(Decimal(180), timeout_ms)

time.sleep(2)

# Move 10 degrees going forward basic on current position. Not an absolute coordinate
channel.MoveRelative(MotorDirection.Forward, Decimal(10), timeout_ms) 
channel.MoveRelative(MotorDirection.Backward, Decimal(10), timeout_ms) 

time.sleep(2)

# Set a pre-defined position then move to it
channel.SetMoveAbsolutePosition(Decimal(90.0))
channel.MoveAbsolute(timeout_ms)

time.sleep(2)

# Stop periodic status polling for channel 1. This does not stop physical motion.
channel.StopPolling()
# Close the BSC101 controller connection. Because cleanup is not inside finally,
# an earlier exception can prevent these two cleanup calls from executing.
dev.Disconnect()

print('exiting')
