import pytest


@pytest.mark.hardware
def test_real_hardware_placeholder_is_excluded_by_default() -> None:
    pytest.fail("Hardware tests must never run in the normal test suite")
