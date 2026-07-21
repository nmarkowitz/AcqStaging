"""Motion hardware and simulation adapters."""

from acq_staging.adapters.base import MotionAxis
from acq_staging.adapters.simulated import SimulatedMotionAxis

__all__ = ["MotionAxis", "SimulatedMotionAxis"]
