"""
Use digital pins
"""

import time
from datetime import datetime

import nidaqmx
from nidaqmx.constants import LineGrouping


acq_in = nidaqmx.Task()
acq_in.di_channels.add_di_chan("Dev1/port0/line7", line_grouping=LineGrouping.CHAN_PER_LINE)
acq_in.start()

acq_out = nidaqmx.Task()
acq_out.do_channels.add_do_chan(
        "Dev1/port0/line0", 
        line_grouping=LineGrouping.CHAN_PER_LINE
        )
acq_out.start()

mov_in = nidaqmx.Task()
mov_in.di_channels.add_di_chan("Dev2/port0/line6", line_grouping=LineGrouping.CHAN_PER_LINE)
mov_in.start()

mov_out = nidaqmx.Task()
mov_out.do_channels.add_do_chan(
        "Dev2/port0/line7", 
        line_grouping=LineGrouping.CHAN_PER_LINE
        )
mov_out.start()

tasks = [acq_out, acq_in, mov_out, mov_in]
for t in tasks:
    t.stop()
    t.close()