"""Vendor-neutral and NI-DAQ digital I/O adapters."""

from __future__ import annotations

import threading
from typing import Any, Protocol, cast

from acq_staging.configuration import DigitalIOConfig
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import DigitalDirection, DigitalStatus


class DigitalIODevice(Protocol):
    """Interface shared by simulated and real digital ports."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def status(self) -> DigitalStatus: ...

    def read_states(self) -> dict[int, bool | None]: ...

    def write_states(self, states: dict[int, bool]) -> None: ...


class SimulatedDigitalIODevice:
    """In-process digital port with deterministic low initial states."""

    def __init__(self, direction: DigitalDirection, config: DigitalIOConfig) -> None:
        # Inputs begin low, while outputs remain unknown until the first explicit write.
        self.direction = direction
        self.config = config
        self._connected = False
        initial: bool | None = False if direction is DigitalDirection.INPUT else None
        self._states: dict[int, bool | None] = {
            line: initial for line in config.selected_lines
        }
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def status(self) -> DigitalStatus:
        with self._lock:
            states = self._unknown_states()
            if self._connected:
                states.update(self._states)
            return DigitalStatus(self._connected, states)

    def read_states(self) -> dict[int, bool | None]:
        self._require_connected()
        with self._lock:
            return dict(self._states)

    def write_states(self, states: dict[int, bool]) -> None:
        # Require one atomic value for every selected output pin.
        self._require_connected()
        if self.direction is not DigitalDirection.OUTPUT:
            raise MotionSafetyError("digital input pins are read-only")
        self._validate_keys(states)
        with self._lock:
            self._states.update(states)

    def set_simulated_inputs(self, states: dict[int, bool]) -> None:
        """Set simulated input levels for tests and future simulation tooling."""
        # Input injection exists only for deterministic simulation and test scenarios.
        if self.direction is not DigitalDirection.INPUT:
            raise MotionSafetyError("only simulated inputs can be injected")
        self._validate_keys(states)
        with self._lock:
            self._states.update(states)

    def _require_connected(self) -> None:
        if not self.status().connected:
            raise AdapterError(f"{self.direction.value}: not connected")

    def _validate_keys(self, states: dict[int, bool]) -> None:
        if set(states) != set(self.config.selected_lines):
            raise AdapterError(f"{self.direction.value}: states must cover every configured pin")

    def _unknown_states(self) -> dict[int, bool | None]:
        return {line: None for line in self.config.selected_lines}


class NidaqmxFacade:
    """Lazy import boundary for the optional NI-DAQmx Python package."""

    def __init__(self) -> None:
        self._loaded = False
        self.Task: Any = None
        self.LineGrouping: Any = None

    def load(self) -> None:
        if self._loaded:
            return
        # Import lazily so simulation and test collection do not require NI drivers.
        import nidaqmx
        from nidaqmx.constants import LineGrouping

        self.Task = nidaqmx.Task
        self.LineGrouping = LineGrouping
        self._loaded = True


class NidaqmxDigitalIODevice:
    """One NI-DAQmx digital input or output port selection."""

    def __init__(
        self,
        direction: DigitalDirection,
        config: DigitalIOConfig,
        facade: NidaqmxFacade | Any | None = None,
    ) -> None:
        self.direction = direction
        self.config = config
        self.facade = facade or NidaqmxFacade()
        self._task: Any | None = None
        self._states: dict[int, bool | None] = dict.fromkeys(config.selected_lines)
        self._error: str | None = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            if self._task is not None:
                return
            self.facade.load()
            # Task construction configures channels but intentionally writes no output level.
            task = self.facade.Task()
            try:
                physical = f"{self.config.device}/{self.config.normalized_port}"
                channels = (
                    task.di_channels
                    if self.direction is DigitalDirection.INPUT
                    else task.do_channels
                )
                add_channel = (
                    channels.add_di_chan
                    if self.direction is DigitalDirection.INPUT
                    else channels.add_do_chan
                )
                if self.config.lines is None:
                    # Whole-port mode transfers one integer whose bits represent pin states.
                    add_channel(
                        physical,
                        line_grouping=self.facade.LineGrouping.CHAN_FOR_ALL_LINES,
                    )
                else:
                    # Selected-line mode creates ordered Boolean channels for chosen pins.
                    for line in self.config.selected_lines:
                        add_channel(
                            f"{physical}/line{line}",
                            line_grouping=self.facade.LineGrouping.CHAN_PER_LINE,
                        )
                self._task = task
                self._error = None
            except Exception:
                # Close partially configured NI tasks before propagating setup failures.
                task.close()
                raise

    def disconnect(self) -> None:
        with self._lock:
            # Forget cached levels because disconnected hardware state is unknowable.
            task, self._task = self._task, None
            self._states = dict.fromkeys(self.config.selected_lines)
            if task is not None:
                task.close()

    def status(self) -> DigitalStatus:
        with self._lock:
            return DigitalStatus(self._task is not None, dict(self._states), self._error)

    def read_states(self) -> dict[int, bool | None]:
        if self.direction is not DigitalDirection.INPUT:
            return self.status().states
        # Serialize reads with writes and disconnects that may use the same NI task.
        with self._lock:
            task = self._require_task()
            try:
                raw = task.read(timeout=self.config.operation_timeout_s)
                self._states = self._decode(raw)
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                raise AdapterError(f"digital_in: {exc}") from exc
            return dict(self._states)

    def write_states(self, states: dict[int, bool]) -> None:
        if self.direction is not DigitalDirection.OUTPUT:
            raise MotionSafetyError("digital input pins are read-only")
        if set(states) != set(self.config.selected_lines):
            raise AdapterError("digital_out: states must cover every configured pin")
        # Convert logical pin states to the representation required by the channel mode.
        with self._lock:
            task = self._require_task()
            data: int | bool | list[bool]
            if self.config.lines is None:
                data = sum(1 << line for line, high in states.items() if high)
            else:
                values = [states[line] for line in self.config.selected_lines]
                data = values[0] if len(values) == 1 else values
            try:
                task.write(data, auto_start=True, timeout=self.config.operation_timeout_s)
                self._states = dict(states)
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                raise AdapterError(f"digital_out: {exc}") from exc

    def _decode(self, raw: object) -> dict[int, bool | None]:
        lines = self.config.selected_lines
        if self.config.lines is None:
            # Decode each whole-port bit into the corresponding displayed line.
            value = int(cast(Any, raw))
            return {line: bool(value & (1 << line)) for line in lines}
        values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        if len(values) != len(lines):
            raise AdapterError("digital_in: NI-DAQ returned an unexpected pin count")
        return {line: bool(value) for line, value in zip(lines, values, strict=True)}

    def _require_task(self) -> Any:
        if self._task is None:
            raise AdapterError(f"{self.direction.value}: not connected")
        return self._task
