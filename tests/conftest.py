"""Fixtures: an RD60xx over modbus-connection's in-memory mock backend.

The mock backend (and its ``mock_modbus_unit`` fixture) ship with
``modbus-connection`` as an auto-registered pytest plugin, so there is no real
server, socket, or backend here — just an address-keyed store the test loads
with RD6018-shaped register values.
"""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit

from riden_modbus import RD60xx

# Raw register words keyed by their address; decoded view inline (RD6018 scale).
HOLDING: dict[int, int] = {
    0: 60181,  # ID -> RD6018
    1: 188,  # SN high -> serial 12345678
    2: 24910,  # SN low
    3: 141,  # firmware -> 1.41
    4: 0,  # internal temp sign -> positive
    5: 32,  # internal temp -> 32 °C
    8: 1350,  # voltage_setpoint -> 13.5
    9: 250,  # current_setpoint -> 2.5
    10: 1348,  # voltage -> 13.48
    11: 210,  # current -> 2.1
    12: 1,  # power high word -> 900.0 W crosses the 16-bit boundary
    13: 24464,  # power low word
    14: 2415,  # input_voltage -> 24.15
    15: 1,  # keypad_lock -> True
    16: 2,  # protection -> OVER_CURRENT
    17: 1,  # mode -> CONSTANT_CURRENT
    18: 1,  # enabled -> True
    19: 3,  # active_preset -> M3
    20: 0,  # current_range (RD6012P only)
    32: 1,  # battery.active -> True
    33: 1289,  # battery.voltage -> 12.89
    34: 1,  # external temp sign -> negative
    35: 5,  # external temp -> -5 °C
    38: 1,  # charge high word -> 100.000 Ah
    39: 34464,  # charge low word
    40: 1,  # energy high word -> 66.536 Wh
    41: 1000,  # energy low word
    48: 2026,  # clock
    49: 7,
    50: 8,
    51: 14,
    52: 30,
    53: 45,
    66: 1,  # take_ok -> True
    67: 0,  # take_out -> False
    68: 1,  # boot_power -> True
    69: 1,  # buzzer -> True
    70: 0,  # logo -> False
    71: 0,  # language -> ENGLISH
    72: 4,  # backlight -> 4
    80: 1350,  # M0 mirrors the active setpoints
    81: 250,
    82: 5500,  # over_voltage_protection -> 55.0
    83: 1600,  # over_current_protection -> 16.0
    84: 500,  # M1 -> 5.0 V
    85: 100,  # M1 -> 1.0 A
    86: 5500,
    87: 1200,
    116: 4200,  # M9 -> 42.0 V
    117: 300,  # M9 -> 3.0 A
    118: 4500,  # M9 OVP -> 45.0 V
    119: 350,  # M9 OCP -> 3.5 A
}


@pytest.fixture
def rd6018(mock_modbus_unit: MockModbusUnit) -> RD60xx:
    """An RD6018 over the mock unit, preloaded with device values."""
    mock_modbus_unit.holding.update(HOLDING)
    return RD60xx(mock_modbus_unit, model=60181)


@pytest.fixture
def unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """The mock unit the ``rd6018`` fixture reads and writes through.

    Request it alongside ``rd6018`` to assert on the register store a write
    landed in, rather than reaching for the unit a component holds.
    """
    return mock_modbus_unit
