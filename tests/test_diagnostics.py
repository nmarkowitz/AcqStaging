import pytest

from acq_staging.diagnostics.cli import ACKNOWLEDGEMENT, main


def test_simulation_diagnostic_does_not_move(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--component", "x"]) == 0
    output = capsys.readouterr().out
    assert '"homed": false' in output
    assert '"position": 0.0' in output


def test_motion_requires_exact_acknowledgement() -> None:
    with pytest.raises(SystemExit):
        main(["--component", "x", "--home"])


def test_explicit_simulation_home_and_motion(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "--component",
                "x",
                "--home",
                "--motion-test",
                "--acknowledge",
                ACKNOWLEDGEMENT,
            ]
        )
        == 0
    )
    assert "motion test" in capsys.readouterr().out
