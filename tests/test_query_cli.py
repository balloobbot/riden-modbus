"""Tests for the script/query.py CLI (no real backend needed).

The connection plumbing, read counting and field rendering all come from
``modbus_connection.cli_helper`` and are tested there; what is left here is the
wiring — the Riden-specific defaults, the section layout, and the exit codes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from modbus_connection import ModbusConnectionError
from modbus_connection.mock import MockModbusConnection

from riden_modbus import RD60xx

from .conftest import HOLDING

_SPEC = importlib.util.spec_from_file_location(
    "riden_query", Path(__file__).resolve().parents[1] / "script" / "query.py"
)
assert _SPEC and _SPEC.loader
query = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(query)


def _mock_connection() -> MockModbusConnection:
    connection = MockModbusConnection()
    connection.for_unit(1).holding.update(HOLDING)
    return connection


def _serve(monkeypatch: pytest.MonkeyPatch, connection: MockModbusConnection) -> None:
    """Hand ``_run`` a mock connection instead of opening a real one."""

    async def connect_from_args(args: object) -> MockModbusConnection:
        return connection

    monkeypatch.setattr(query, "connect_from_args", connect_from_args)


def test_parse_args_defaults_to_rtu_over_tcp() -> None:
    args = query._parse_args(["1.2.3.4", "--unit", "7"])
    assert args.transport == "tcp"
    assert args.target == "1.2.3.4"
    assert args.unit == 7
    assert args.framer == "rtu"  # RTU-over-TCP default for serial gateways
    assert args.timeout == 10.0


def test_parse_args_serial() -> None:
    args = query._parse_args(["/dev/ttyUSB0", "--transport", "serial"])
    assert args.transport == "serial"
    assert args.target == "/dev/ttyUSB0"
    assert args.unit == 1  # Riden station-address default
    assert args.baudrate == 115200  # Riden serial default
    assert args.framer == "rtu"


def test_parse_args_accepts_native_modbus_framing() -> None:
    args = query._parse_args(["1.2.3.4", "--transport", "udp", "--framer", "socket"])
    assert args.transport == "udp"
    assert args.framer == "socket"


async def test_print_covers_every_section(
    capsys: pytest.CaptureFixture[str], rd6018: RD60xx
) -> None:
    await rd6018.async_update()
    query._print(rd6018)

    out = capsys.readouterr().out
    for label, _attribute in query.SECTIONS:
        assert label in out
    assert "Preset M0" in out
    assert "Preset M9" in out
    # Rows come from the components' declared fields and properties, with the
    # field's unit appended.
    assert "manufacturer      Riden" in out
    assert "voltage_setpoint         13.5 V" in out
    assert "temperature              32" in out


async def test_run_queries_device(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _mock_connection()
    _serve(monkeypatch, connection)

    assert await query._run(query._parse_args(["1.2.3.4"])) == 0
    out = capsys.readouterr().out
    assert "RD6018" in out
    assert "2 Modbus reads" in out  # one probe read + one full-map read
    assert connection.connected is False  # closed afterwards


async def test_run_rejects_unsupported_model(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = MockModbusConnection()
    connection.for_unit(1).holding[0] = 52051  # a DPS5205 is not an RD60xx
    _serve(monkeypatch, connection)

    assert await query._run(query._parse_args(["1.2.3.4"])) == 1
    assert "Unsupported model" in capsys.readouterr().err
    assert connection.connected is False  # still closed


async def test_run_reports_connect_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connect_from_args(args: object) -> MockModbusConnection:
        raise ModbusConnectionError("no route to host")

    monkeypatch.setattr(query, "connect_from_args", connect_from_args)

    assert await query._run(query._parse_args(["1.2.3.4"])) == 1
    assert "Could not connect" in capsys.readouterr().err


async def test_run_reports_read_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = MockModbusConnection()

    def boom() -> int:
        raise ModbusConnectionError("device gone")

    connection.for_unit(1).holding[0] = boom
    _serve(monkeypatch, connection)

    assert await query._run(query._parse_args(["1.2.3.4"])) == 1
    assert "Error reading device" in capsys.readouterr().err
    assert connection.connected is False  # still closed on failure


def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _mock_connection())
    monkeypatch.setattr(sys, "argv", ["query.py", "1.2.3.4"])

    assert query.main() == 0
