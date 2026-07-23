"""Vendor-neutral DAQ acquisition handshakes."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import suppress

from acq_staging.configuration import (
    AcquisitionHandshakeConfig,
    DummyAcquirerConfig,
)
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import DigitalDirection, DummyAcquirerStatus
from acq_staging.services import DigitalIOService

EventCallback = Callable[[str, dict[str, object]], None]


class DaqTileHandshake:
    """Coordinate the movement side of an idle-LOW, four-phase DAQ handshake."""

    def __init__(
        self,
        config: AcquisitionHandshakeConfig,
        digital_service: DigitalIOService,
    ) -> None:
        self.config = config
        self.digital_service = digital_service
        self._active = False
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        """Return whether this profile requires acquisition acknowledgement."""
        return self.config.enabled

    def begin(self) -> None:
        """Preflight idle signals and reserve Digital Out before mosaic motion."""
        if not self.enabled:
            return
        with self._lock:
            if self._active:
                raise MotionSafetyError("the acquisition handshake is already active")
            self.digital_service.begin_handshake(
                self.config.complete_input_line,
                self.config.ready_output_line,
            )
            self._active = True

    def acquire_tile(
        self,
        tile_number: int,
        stop_event: threading.Event,
        mosaic_deadline: float | None,
        on_event: EventCallback | None,
    ) -> None:
        """Latch Ready HIGH until acquisition completion is acknowledged and reset."""
        with self._lock:
            if not self._active:
                raise MotionSafetyError("the acquisition handshake is not active")
        # Reject stale Done or Ready levels before starting each tile exchange.
        self._require_cycle_idle()
        if on_event is not None:
            on_event("ACQUISITION_WAIT_START", {"tile": tile_number})
        try:
            # Ready stays HIGH until the acquirer raises Done, so polling cannot miss it.
            self.digital_service.write_output_line(self.config.ready_output_line, True)
            if on_event is not None:
                on_event(
                    "ACQUISITION_READY_HIGH",
                    {"tile": tile_number, "line": self.config.ready_output_line},
                )
            self._wait_for_input(
                True,
                self.config.acquisition_timeout_s,
                stop_event,
                mosaic_deadline,
                "acquisition completion",
            )
            if on_event is not None:
                on_event(
                    "ACQUISITION_DONE_HIGH",
                    {"tile": tile_number, "line": self.config.complete_input_line},
                )

            # Lowering Ready acknowledges Done and starts the reset half of the protocol.
            self.digital_service.write_output_line(self.config.ready_output_line, False)
            if on_event is not None:
                on_event(
                    "ACQUISITION_READY_LOW",
                    {"tile": tile_number, "line": self.config.ready_output_line},
                )
            self._wait_for_input(
                False,
                self.config.reset_timeout_s,
                stop_event,
                mosaic_deadline,
                "acquisition reset",
            )
            if on_event is not None:
                on_event(
                    "ACQUISITION_DONE_LOW",
                    {"tile": tile_number, "line": self.config.complete_input_line},
                )
                on_event("ACQUISITION_WAIT_COMPLETE", {"tile": tile_number})
        except Exception as exc:
            # A failed exchange returns the owned output LOW without hiding the root error.
            with suppress(Exception):
                self.digital_service.write_output_line(
                    self.config.ready_output_line,
                    False,
                    source="cleanup",
                )
            if on_event is not None:
                with suppress(Exception):
                    on_event(
                        "ACQUISITION_HANDSHAKE_FAILED",
                        {"tile": tile_number, "error": str(exc)},
                    )
            raise

    def abort(self) -> None:
        """Best-effort return of the movement Ready output to its idle LOW state."""
        with self._lock:
            if not self._active:
                return
            self.digital_service.write_output_line(
                self.config.ready_output_line,
                False,
                source="cleanup",
            )

    def finish(self) -> None:
        """Return Ready LOW and release exclusive Digital Out ownership."""
        with self._lock:
            if not self._active:
                return
            try:
                output = self.digital_service.status(DigitalDirection.OUTPUT)
                if output.states.get(self.config.ready_output_line) is True:
                    self.digital_service.write_output_line(
                        self.config.ready_output_line,
                        False,
                        source="cleanup",
                    )
            finally:
                self.digital_service.end_handshake()
                self._active = False

    def _require_cycle_idle(self) -> None:
        # Refresh Done and inspect cached Ready before every tile to reject stale state.
        inputs = self.digital_service.read_inputs()
        output = self.digital_service.status(DigitalDirection.OUTPUT)
        if inputs.get(self.config.complete_input_line) is not False:
            raise MotionSafetyError(
                f"digital_in line{self.config.complete_input_line} must be LOW before a tile"
            )
        if output.states.get(self.config.ready_output_line) is not False:
            raise MotionSafetyError(
                f"digital_out line{self.config.ready_output_line} must be LOW before a tile"
            )

    def _wait_for_input(
        self,
        expected: bool,
        timeout_s: float,
        stop_event: threading.Event,
        mosaic_deadline: float | None,
        description: str,
    ) -> None:
        # Bound every poll loop by Stop, its phase timeout, and the whole-mosaic deadline.
        phase_deadline = time.monotonic() + timeout_s
        interval_s = self.config.polling_interval_ms / 1000.0
        while True:
            if stop_event.is_set():
                raise AdapterError("mosaic stopped during acquisition handshake")
            now = time.monotonic()
            effective_deadline = min(
                phase_deadline,
                mosaic_deadline if mosaic_deadline is not None else phase_deadline,
            )
            if now >= effective_deadline:
                raise AdapterError(f"timed out waiting for {description}")
            states = self.digital_service.read_inputs()
            if states.get(self.config.complete_input_line) is expected:
                return
            stop_event.wait(min(interval_s, effective_deadline - now))


class DummyDataAcquirer:
    """Emulate an acquisition application that responds to movement Ready signals."""

    def __init__(self, config: DummyAcquirerConfig, digital_service: DigitalIOService) -> None:
        self.config = config
        self.digital_service = digital_service
        self._stop = threading.Event()
        self._complete_now = threading.Event()
        self._run_lock = threading.Lock()
        self._status_lock = threading.RLock()
        self._status = DummyAcquirerStatus(False, False, 0, 0)

    def status(self) -> DummyAcquirerStatus:
        """Return an immutable snapshot safe for GUI polling."""
        with self._status_lock:
            return self._status

    def run(self, on_event: EventCallback | None = None) -> DummyAcquirerStatus:
        """Listen and respond until Stop is requested or an error occurs."""
        # A nonblocking lock prevents two listeners from controlling one output task.
        if not self._run_lock.acquire(blocking=False):
            raise MotionSafetyError("the dummy acquirer is already listening")
        self._stop.clear()
        self._complete_now.clear()
        handshake_started = False
        try:
            # Idle preflight fails before the dummy claims or changes any line.
            self.digital_service.begin_handshake(
                self.config.handshake.ready_input_line,
                self.config.handshake.complete_output_line,
            )
            handshake_started = True
            self._clear_error()
            self._replace_status(listening=True, acquiring=False, error=None)
            self._emit(on_event, "DUMMY_LISTENING_START", {})
            while not self._stop.is_set():
                # Waiting for the next Ready HIGH has no timeout but remains stoppable.
                if not self._wait_for_ready(True, None):
                    break
                self._complete_now.clear()
                self._replace_status(
                    acquiring=True,
                    requests_received=self.status().requests_received + 1,
                )
                self._emit(
                    on_event,
                    "DUMMY_ACQUISITION_START",
                    {"request": self.status().requests_received},
                )
                if not self._wait_for_acquisition():
                    break

                # Done remains HIGH until movement lowers Ready, preventing missed pulses.
                self.digital_service.write_output_line(
                    self.config.handshake.complete_output_line,
                    True,
                )
                self._emit(on_event, "DUMMY_DONE_HIGH", {})
                if not self._wait_for_ready(
                    False,
                    self.config.handshake.reset_timeout_s,
                ):
                    break
                self.digital_service.write_output_line(
                    self.config.handshake.complete_output_line,
                    False,
                )
                self._replace_status(
                    acquiring=False,
                    acquisitions_completed=self.status().acquisitions_completed + 1,
                )
                self._emit(
                    on_event,
                    "DUMMY_ACQUISITION_COMPLETE",
                    {"completed": self.status().acquisitions_completed},
                )
            return self.status()
        except Exception as exc:
            self._replace_status(error=str(exc))
            self._emit(on_event, "DUMMY_ACQUIRER_FAILED", {"error": str(exc)})
            raise
        finally:
            # Shutdown returns Done LOW before allowing normal Digital Out control again.
            if handshake_started:
                with suppress(Exception):
                    self.digital_service.write_output_line(
                        self.config.handshake.complete_output_line,
                        False,
                        source="cleanup",
                    )
                self.digital_service.end_handshake()
            self._replace_status(listening=False, acquiring=False)
            self._run_lock.release()
            self._emit(on_event, "DUMMY_LISTENING_STOP", {})

    def request_stop(self) -> None:
        """Wake listener and acquisition waits so the worker can shut down."""
        self._stop.set()
        self._complete_now.set()

    def complete_now(self) -> None:
        """End the current timed dummy acquisition at the operator's request."""
        if not self.status().acquiring:
            raise MotionSafetyError("the dummy acquirer is not currently acquiring")
        self._complete_now.set()

    def _wait_for_acquisition(self) -> bool:
        # Manual completion and Stop interrupt the configured dummy duration.
        deadline = time.monotonic() + self.config.handshake.acquisition_duration_s
        interval_s = self.config.handshake.polling_interval_ms / 1000.0
        while not self._stop.is_set():
            if self._complete_now.is_set() or time.monotonic() >= deadline:
                return True
            self._stop.wait(min(interval_s, max(0.0, deadline - time.monotonic())))
        return False

    def _wait_for_ready(self, expected: bool, timeout_s: float | None) -> bool:
        # Poll the static level so the latched protocol remains independent of edge timing.
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        interval_s = self.config.handshake.polling_interval_ms / 1000.0
        while not self._stop.is_set():
            states = self.digital_service.read_inputs()
            if states.get(self.config.handshake.ready_input_line) is expected:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                raise AdapterError("timed out waiting for movement Ready to reset LOW")
            self._stop.wait(interval_s)
        return False

    def _replace_status(
        self,
        *,
        listening: bool | None = None,
        acquiring: bool | None = None,
        requests_received: int | None = None,
        acquisitions_completed: int | None = None,
        error: str | None = None,
    ) -> None:
        # Replace the immutable status atomically so GUI reads never see partial updates.
        with self._status_lock:
            current = self._status
            self._status = DummyAcquirerStatus(
                current.listening if listening is None else listening,
                current.acquiring if acquiring is None else acquiring,
                current.requests_received
                if requests_received is None
                else requests_received,
                current.acquisitions_completed
                if acquisitions_completed is None
                else acquisitions_completed,
                current.error if error is None else error,
            )

    def _clear_error(self) -> None:
        # A new successful listener preflight clears any previous run failure.
        with self._status_lock:
            current = self._status
            self._status = DummyAcquirerStatus(
                current.listening,
                current.acquiring,
                current.requests_received,
                current.acquisitions_completed,
            )

    @staticmethod
    def _emit(
        callback: EventCallback | None,
        event: str,
        details: dict[str, object],
    ) -> None:
        if callback is not None:
            callback(event, details)
