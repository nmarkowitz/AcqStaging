"""Simulation-only tests for the standalone movement handshake script."""

from __future__ import annotations

from collections import deque
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    """Load the standalone script without turning the scripts directory into a package."""
    script_path = Path("scripts/nidaq_movement_handshake.py")
    spec = spec_from_file_location("nidaq_movement_handshake_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script_path}")

    # Execute the file under a test-only module name without invoking its main block.
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


class FakeInputTask:
    """Return a deterministic sequence of paired digital input levels."""

    def __init__(self, readings: list[list[bool]]) -> None:
        self._readings = deque(readings)
        self._last = readings[-1]

    def read(self, *, timeout: float) -> list[bool]:
        del timeout
        # Hold the final state after the scripted transitions are exhausted.
        if self._readings:
            self._last = self._readings.popleft()
        return list(self._last)


class FakeOutputTask:
    """Record paired output writes without opening NI-DAQ hardware."""

    def __init__(self) -> None:
        self.writes: list[list[bool]] = []

    def write(self, values: list[bool], *, auto_start: bool, timeout: float) -> None:
        del auto_start, timeout
        self.writes.append(list(values))


def test_run_handshake_requires_high_then_low_before_reset() -> None:
    input_task = FakeInputTask(
        [
            [False, False],
            [False, True],
            [True, True],
            [True, False],
            [False, False],
        ]
    )
    output_task = FakeOutputTask()

    # Zero polling delay keeps this deterministic test independent of wall-clock timing.
    SCRIPT.run_handshake(
        input_task,
        output_task,
        timeout_s=1.0,
        polling_interval_s=0.0,
    )

    assert output_task.writes == [
        [True, True],
        [False, False],
        [True, True],
    ]


def test_run_handshake_rejects_stale_high_input_and_restores_idle() -> None:
    input_task = FakeInputTask([[True, False]])
    output_task = FakeOutputTask()

    # A partially HIGH input pair is stale and must fail before asserting either request.
    with pytest.raises(RuntimeError, match="must be LOW/LOW"):
        SCRIPT.run_handshake(
            input_task,
            output_task,
            timeout_s=1.0,
            polling_interval_s=0.0,
        )

    assert output_task.writes == [[True, True], [True, True]]
