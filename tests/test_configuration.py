import json
from pathlib import Path

import pytest

from acq_staging.configuration import (
    config_from_dict,
    default_dummy_acquirer_config,
    default_simulation_config,
    dummy_acquirer_config_from_dict,
    load_config,
    load_dummy_acquirer_config,
)
from acq_staging.errors import ConfigurationError
from acq_staging.models import (
    AdapterType,
    AxisName,
    DigitalDirection,
    MosaicOrientation,
)


def test_default_config_round_trip() -> None:
    config = default_simulation_config()
    assert config_from_dict(config.to_dict()) == config


def test_rejects_missing_component() -> None:
    data = default_simulation_config().to_dict()
    del data["components"]["z"]
    with pytest.raises(ConfigurationError, match="all required"):
        config_from_dict(data)


def test_rejects_invalid_limits() -> None:
    data = default_simulation_config().to_dict()
    data["components"]["x"]["limits"] = {"minimum": 10.0, "maximum": 2.0}
    with pytest.raises(ConfigurationError, match="minimum"):
        config_from_dict(data)


def test_config_dictionary_is_json_serializable() -> None:
    encoded = json.dumps(default_simulation_config().to_dict())
    assert json.loads(encoded)["schema_version"] == 7


def test_schema_three_profile_migrates_with_handshake_disabled() -> None:
    # Removing the new section reproduces a saved profile from the prior schema.
    data = default_simulation_config().to_dict()
    data["schema_version"] = 3
    del data["acquisition_handshake"]

    migrated = config_from_dict(data)

    assert migrated.schema_version == 7
    assert migrated.acquisition_handshake.enabled is False


def test_rejects_mosaic_step_outside_multi_tile_dimension() -> None:
    data = default_simulation_config().to_dict()
    data["mosaic"]["step_x_mm"] = 0.0

    with pytest.raises(ConfigurationError, match="steps"):
        config_from_dict(data)


def test_hardware_example_keeps_bsc101_dll_path_in_profile() -> None:
    config = load_config(Path("configs/hardware.example.json"))
    rotation = config.components[AxisName.ROTATION]

    assert rotation.adapter is AdapterType.THORLABS_BSC101
    assert isinstance(rotation.serial_number, str)
    assert rotation.serial_number
    assert rotation.axis_number == 1
    assert rotation.device_settings_name == "HDR50"
    assert rotation.dll_directory == r"C:\Program Files\Thorlabs\Kinesis"
    assert all(component.limits is None for component in config.components.values())
    assert config.mosaic.operation_timeout_s == 3600.0


def test_schema_four_hardware_limits_migrate_to_vendor_enforcement() -> None:
    # Older physical profiles are accepted, but authored limits are discarded in memory.
    data = load_config(Path("configs/hardware.example.json")).to_dict()
    data["schema_version"] = 4
    data["components"]["x"]["limits"] = {"minimum": -99.0, "maximum": 99.0}

    migrated = config_from_dict(data)

    assert migrated.schema_version == 7
    assert migrated.components[AxisName.X].limits is None


def test_schema_five_mosaic_defaults_migrate_without_z_or_counts() -> None:
    data = default_simulation_config().to_dict()
    data["schema_version"] = 5
    data["mosaic"].update(
        {"step_z_mm": 0.1, "tiles_x": 3, "tiles_y": 4, "tiles_z": 2}
    )

    migrated = config_from_dict(data)

    assert migrated.schema_version == 7
    assert set(migrated.to_dict()["mosaic"]) == {
        "step_x_mm",
        "step_y_mm",
        "settling_time_s",
        "dwell_time_s",
        "operation_timeout_s",
        "orientation",
    }


def test_schema_six_profile_migrates_to_prior_positive_xy_orientation() -> None:
    data = default_simulation_config().to_dict()
    data["schema_version"] = 6
    del data["mosaic"]["orientation"]

    migrated = config_from_dict(data)

    assert migrated.schema_version == 7
    assert migrated.mosaic.orientation == MosaicOrientation()


def test_profile_orientation_round_trips_signed_swapped_axes() -> None:
    data = default_simulation_config().to_dict()
    data["mosaic"]["orientation"] = {
        "right_axis": "y",
        "right_sign": -1,
        "down_axis": "x",
        "down_sign": 1,
    }

    config = config_from_dict(data)

    assert config.mosaic.orientation == MosaicOrientation(
        AxisName.Y, -1, AxisName.X, 1
    )
    assert config.to_dict()["mosaic"]["orientation"] == data["mosaic"]["orientation"]


@pytest.mark.parametrize("invalid_sign", [0, 1.5, True])
def test_profile_orientation_rejects_invalid_signs(invalid_sign: object) -> None:
    data = default_simulation_config().to_dict()
    data["mosaic"]["orientation"]["down_sign"] = invalid_sign

    with pytest.raises(ConfigurationError, match="expected integer -1 or 1"):
        config_from_dict(data)


def test_hardware_profiles_map_the_reported_bottom_left_home_to_top_left() -> None:
    # Both maintained laboratory profiles use decreasing controller Y for physical down.
    expected = MosaicOrientation(AxisName.X, 1, AxisName.Y, -1)

    assert load_config(Path("configs/true_hardware.json")).mosaic.orientation == expected
    assert (
        load_config(Path("configs/true_hardware_no-z.json")).mosaic.orientation
        == expected
    )


def test_bsc101_requires_attached_stage_settings_name() -> None:
    data = load_config(Path("configs/hardware.example.json")).to_dict()
    data["components"]["rotation"]["device_settings_name"] = None

    with pytest.raises(ConfigurationError, match="device_settings_name"):
        config_from_dict(data)


def test_rejects_nonpositive_mosaic_timeout() -> None:
    data = default_simulation_config().to_dict()
    data["mosaic"]["operation_timeout_s"] = 0.0

    with pytest.raises(ConfigurationError, match="operation_timeout_s"):
        config_from_dict(data)


def test_digital_port_supports_whole_port_and_pin_subset() -> None:
    config = load_config(Path("configs/simulation.json"))

    assert config.digital_io[DigitalDirection.INPUT].selected_lines == tuple(range(8))
    assert config.digital_io[DigitalDirection.INPUT].normalized_port == "port0"
    assert config.digital_io[DigitalDirection.OUTPUT].selected_lines == (0, 1, 2, 3)


def test_rejects_digital_pin_outside_port_width() -> None:
    data = default_simulation_config().to_dict()
    data["digital_io"]["digital_out"]["lines"] = [8]

    with pytest.raises(ConfigurationError, match="outside configured port width"):
        config_from_dict(data)


def test_rejects_digital_ports_wider_than_eight_channels() -> None:
    data = default_simulation_config().to_dict()
    data["digital_io"]["digital_in"]["line_count"] = 9

    with pytest.raises(ConfigurationError, match="between 1 and 8"):
        config_from_dict(data)


def test_enabled_handshake_requires_selected_lines() -> None:
    # Enabling an unowned output line must fail before any DAQ is constructed.
    data = default_simulation_config().to_dict()
    data["acquisition_handshake"]["enabled"] = True
    data["acquisition_handshake"]["ready_output_line"] = 7

    with pytest.raises(ConfigurationError, match="not selected by digital_out"):
        config_from_dict(data)


def test_dummy_acquirer_profile_round_trip() -> None:
    config = default_dummy_acquirer_config()

    assert dummy_acquirer_config_from_dict(config.to_dict()) == config
    assert load_dummy_acquirer_config("configs/dummy_acquirer.simulation.json") == config
