"""
Use digital pins
"""

import time
from datetime import datetime

import nidaqmx
from nidaqmx.constants import LineGrouping

# Set digital pin 0 to high for 1 second
with nidaqmx.Task() as task:

    # Add channel to task
    task.do_channels.add_do_chan(
        "Dev1/port0/line0", 
        line_grouping=LineGrouping.CHAN_PER_LINE
        )
    
    # Set pin to high
    task.start()
    task.write(True)
    task.stop()

    time.sleep(1)
    
    # Set pin to low
    task.start()
    task.write(False)
    task.stop()


# Read the pins
t1 = time.perf_counter()

with nidaqmx.Task() as task:
    task.di_channels.add_di_chan("Dev1/port1/line1", line_grouping=LineGrouping.CHAN_PER_LINE)

    timeout = 60

    t_start = datetime.now()
    timestamps = []
    last_update = task.read()
    keep_going = True

    while keep_going:
        current_data = task.read()
        if current_data != last_update:
            timestamps.append((current_data,datetime.now()))
            print("Data changed!")

        td = datetime.now() - t_start
        if len(timestamps)>=2 or td.total_seconds()>=timeout or (time.perf_counter()-t1>80):
            print('over!')
            keep_going = False

        last_update = current_data
