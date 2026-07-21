"""Deterministic simulated motion axis."""

from __future__ import annotations

import threading

from acq_staging.configuration import ComponentConfig
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import (
    AdapterType,
    AxisName,
    ComponentIdentity,
    ComponentStatus,
)


class SimulatedMotionAxis:
    """Reusable in-process simulator for linear and rotary axes."""

    def __init__(self, axis: AxisName, config: ComponentConfig) -> None:
        self.axis = axis
        self.config = config
        self._position = config.simulation_start
        self._connected = False
        self._homed = False
        self._busy = False
        self._error: str | None = None
        self._fail_next: str | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

    def inject_failure(self, message: str) -> None:
        """Cause the next state-changing operation to fail."""
        with self._lock:
            self._fail_next = message

    def connect(self) -> None:
        with self._lock:
            self._connected = True
            self._error = None

    def disconnect(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._busy = False
            self._connected = False
            self._homed = False

    def identity(self) -> ComponentIdentity:
        self._require_connected()
        return ComponentIdentity(
            axis=self.axis,
            adapter=AdapterType.SIMULATED,
            serial_number=f"SIM-{self.axis.value.upper()}",
            model="Simulated Motion Axis",
        )

    def identify(self) -> None:
        """Simulate a non-motion device identification request."""
        self._require_connected()

    def status(self) -> ComponentStatus:
        with self._lock:
            return ComponentStatus(
                connected=self._connected,
                enabled=self._connected,
                homed=self._homed,
                busy=self._busy,
                position=self._position if self._connected else None,
                error=self._error,
            )

    def home(self) -> None:
        # Simulation treats the configured minimum as the repeatable home position.
        self._start_operation()
        try:
            self._delay()
            with self._lock:
                self._position = self.config.limits.minimum
                self._homed = True
        finally:
            self._finish_operation()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._busy = False

    def move_absolute(self, destination: float) -> None:
        # Mirror production prerequisites so simulation exercises the same safety flow.
        self._require_ready_for_motion()
        if not self.config.limits.contains(destination):
            raise MotionSafetyError(
                f"{self.axis.value}: destination {destination} is outside software limits"
            )
        self._start_operation()
        try:
            self._delay()
            with self._lock:
                self._position = destination
        finally:
            self._finish_operation()

    def move_relative(self, displacement: float) -> None:
        self.move_absolute(self.get_position() + displacement)

    def get_position(self) -> float:
        self._require_connected()
        with self._lock:
            return self._position

    def _require_connected(self) -> None:
        if not self.status().connected:
            raise AdapterError(f"{self.axis.value}: not connected")

    def _require_ready_for_motion(self) -> None:
        self._require_connected()
        if not self.status().homed:
            raise MotionSafetyError(f"{self.axis.value}: must be homed before movement")

    def _start_operation(self) -> None:
        self._require_connected()
        # State transitions are locked because GUI workers and polling run concurrently.
        with self._lock:
            if self._busy:
                raise AdapterError(f"{self.axis.value}: already busy")
            if self._fail_next is not None:
                message = self._fail_next
                self._fail_next = None
                self._error = message
                raise AdapterError(message)
            self._busy = True
            self._error = None
            self._stop_event.clear()

    def _finish_operation(self) -> None:
        with self._lock:
            self._busy = False

    def _delay(self) -> None:
        # Event.wait models latency while allowing Stop to interrupt immediately.
        if self.config.simulation_latency_s and self._stop_event.wait(
            self.config.simulation_latency_s
        ):
            raise AdapterError(f"{self.axis.value}: operation stopped")
