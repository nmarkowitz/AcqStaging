from datetime import UTC, datetime
from pathlib import Path

from acq_staging.mosaic_logging import MosaicAuditLogger


def test_mosaic_audit_log_writes_settings_before_event_lines() -> None:
    directory = Path("mosaic-log-test-output")
    directory.mkdir(exist_ok=True)
    expected = directory / "2026_07_21_14-05-09.log"
    expected.unlink(missing_ok=True)
    moments = iter(
        (
            datetime(2026, 7, 21, 14, 5, 9, tzinfo=UTC),
            datetime(2026, 7, 21, 14, 5, 10, 123000, tzinfo=UTC),
            datetime(2026, 7, 21, 14, 5, 11, 456000, tzinfo=UTC),
        )
    )
    logger = MosaicAuditLogger(
        directory,
        lambda: next(moments),
        {"coordinate_reference": "absolute_controller_home"},
    )

    logger.log("MOVEMENT_START", {"axis": "x", "absolute_destination": 2.8})
    logger.close()

    assert logger.path == expected
    contents = expected.read_text(encoding="utf-8")
    lines = contents.splitlines()
    assert "2026-07-21T14:05:10.123+00:00 | MOSAIC_SETTINGS |" in lines[0]
    assert '"coordinate_reference":"absolute_controller_home"' in lines[0]
    assert "2026-07-21T14:05:11.456+00:00 | MOVEMENT_START |" in lines[1]
    assert '"axis":"x"' in contents
    assert '"absolute_destination":2.8' in contents
    expected.unlink()
    directory.rmdir()
