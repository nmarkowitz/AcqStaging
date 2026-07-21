"""Safe diagnostic command-line interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from acq_staging.configuration import default_simulation_config, load_config
from acq_staging.factory import build_axes
from acq_staging.models import AxisName
from acq_staging.services import MotionService

ACKNOWLEDGEMENT = "I-UNDERSTAND-HARDWARE-WILL-MOVE"


def build_parser() -> argparse.ArgumentParser:
    """Build the diagnostic argument parser."""
    parser = argparse.ArgumentParser(description="AcqStaging component diagnostics")
    parser.add_argument("--config", help="Configuration JSON path")
    parser.add_argument("--component", required=True, choices=("x", "y", "z", "rotation"))
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Allow constructing a real adapter; this does not authorize motion",
    )
    parser.add_argument(
        "--home",
        action="store_true",
        help="Explicitly home the selected component",
    )
    parser.add_argument(
        "--motion-test",
        action="store_true",
        help="Run the configured small move and return to the starting position",
    )
    parser.add_argument(
        "--acknowledge",
        help=f"Required for motion; must equal {ACKNOWLEDGEMENT}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a non-motion diagnostic."""
    args = build_parser().parse_args(argv)
    # Real adapters require an explicit profile; motion additionally requires consent.
    if args.hardware and not args.config:
        build_parser().error("--hardware requires --config")
    if (args.home or args.motion_test) and args.acknowledge != ACKNOWLEDGEMENT:
        build_parser().error("homing or motion testing requires the exact --acknowledge value")
    config = load_config(args.config) if args.config else default_simulation_config()
    axes, manager = build_axes(config, allow_hardware=args.hardware)
    axis = AxisName(args.component)
    service = MotionService(config, axes)
    # Cleanup runs for successful diagnostics and all failures after construction.
    try:
        service.connect(axis)
        result = service.diagnose(axis)
        print(
            json.dumps(
                {
                    "axis": axis.value,
                    "adapter": result.identity.adapter.value,
                    "serial_number": result.identity.serial_number,
                    "model": result.identity.model,
                    "status": {
                        "connected": result.status.connected,
                        "homed": result.status.homed,
                        "busy": result.status.busy,
                        "position": result.status.position,
                    },
                },
                indent=2,
            )
        )
        if args.home:
            service.home(axis)
            print(f"{axis.value}: home completed")
        if args.motion_test:
            start, target = service.motion_test(axis)
            print(f"{axis.value}: motion test {start} -> {target} -> {start} completed")
    finally:
        service.disconnect(axis)
        if manager is not None:
            manager.close_all()
    return 0
