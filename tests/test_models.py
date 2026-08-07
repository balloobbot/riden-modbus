"""Model profiles: ID detection and per-model scaling through real decodes."""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit

from riden_modbus import RD60xx, is_supported_model, model_name, profile_for
from riden_modbus.models import (
    RD6006,
    RD6006P,
    RD6012,
    RD6012P_HIGH,
    RD6012P_LOW,
    RD6018,
    RD6024,
    RK6006,
    ModelProfile,
)


@pytest.mark.parametrize(
    ("model", "profile"),
    [
        pytest.param(60060, RD6006, id="RD6006-low-id"),
        pytest.param(60062, RD6006, id="RD6006"),
        pytest.param(60065, RD6006P, id="RD6006P"),
        pytest.param(60066, RK6006, id="RK6006"),
        pytest.param(60121, RD6012, id="RD6012"),
        pytest.param(60125, RD6012P_LOW, id="RD6012P"),
        pytest.param(60181, RD6018, id="RD6018"),
        pytest.param(60241, RD6024, id="RD6024"),
    ],
)
def test_profile_for(model: int, profile: ModelProfile) -> None:
    assert profile_for(model) is profile
    assert is_supported_model(model) is True
    assert model_name(model) == profile.name


def test_profile_for_rd6012p_follows_current_range() -> None:
    assert profile_for(60125, current_range=0) is RD6012P_LOW
    assert profile_for(60125, current_range=1) is RD6012P_HIGH
    assert RD6012P_LOW.max_current == 6.0
    assert RD6012P_HIGH.max_current == 12.0


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(52051, id="DPS5205"),
        pytest.param(60070, id="between-known-ranges"),
        pytest.param(60250, id="above-RD6024"),
    ],
)
def test_unsupported_model(model: int) -> None:
    with pytest.raises(ValueError, match="Unsupported Riden model ID"):
        profile_for(model)
    assert is_supported_model(model) is False
    assert model_name(model) == f"RD{model // 10}"


@pytest.mark.parametrize(
    ("model", "current_range", "voltage", "current", "power", "battery_voltage"),
    [
        pytest.param(60181, 0, 13.5, 1.35, 900.0, 13.5, id="RD6018"),
        pytest.param(60241, 0, 13.5, 1.35, 900.0, 13.5, id="RD6024"),
        pytest.param(60121, 0, 13.5, 1.35, 900.0, 13.5, id="RD6012"),
        pytest.param(60062, 0, 13.5, 0.135, 900.0, 13.5, id="RD6006-milliamp"),
        pytest.param(60066, 0, 13.5, 0.135, 900.0, 13.5, id="RK6006-milliamp"),
        pytest.param(60065, 0, 1.35, 0.0135, 90.0, 1.35, id="RD6006P-precision"),
        pytest.param(60125, 0, 1.35, 0.0135, 90.0, 1.35, id="RD6012P-low-range"),
        pytest.param(60125, 1, 1.35, 0.135, 90.0, 1.35, id="RD6012P-high-range"),
    ],
)
async def test_model_scaling_decodes(
    mock_modbus_unit: MockModbusUnit,
    model: int,
    current_range: int,
    voltage: float,
    current: float,
    power: float,
    battery_voltage: float,
) -> None:
    """The same raw words decode per the model's scaling profile."""
    mock_modbus_unit.holding.update({10: 1350, 11: 135, 12: 1, 13: 24464, 33: 1350})
    device = RD60xx(mock_modbus_unit, model=model, current_range=current_range)
    await device.async_update()
    assert device.output.voltage == pytest.approx(voltage)
    assert device.output.current == pytest.approx(current)
    assert device.output.power == pytest.approx(power)
    assert device.battery.voltage == pytest.approx(battery_voltage)
    # Input voltage stays in centivolts on every model.
    assert device.output.input_voltage == pytest.approx(0.0)


async def test_model_limits_apply_to_writes(mock_modbus_unit: MockModbusUnit) -> None:
    """An RD6006 rejects a current an RD6018 accepts, at its own scale."""
    from riden_modbus import RidenValueValidationError

    device = RD60xx(mock_modbus_unit, model=60062)
    with pytest.raises(RidenValueValidationError):
        await device.output.set_current(7.0)  # above the RD6006's 6 A

    await device.output.set_current(5.0)
    raw = (await mock_modbus_unit.read_holding_registers(9, 1))[0]
    assert raw == 5000  # written in milliamps


def test_component_classes_are_cached(rd6018: RD60xx, unit: MockModbusUnit) -> None:
    """Two devices of one model share the generated component classes."""
    other = RD60xx(unit, model=60182)  # any RD6018-range ID
    assert type(other.output) is type(rd6018.output)
    assert type(other.battery) is type(rd6018.battery)
    assert type(other.presets[0]) is type(rd6018.presets[5])
