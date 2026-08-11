"""Tests for the script/esphome_query.py bridge (no aioesphomeapi needed).

A fake ``APIClient`` implements an in-process RD6018: it parses the Modbus
RTU frames the bridge sends over the fake serial proxy and answers from the
shared ``HOLDING`` register map, exercising the real framing end to end.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusProtocolError,
    ModbusTimeoutError,
)

from .conftest import HOLDING

_SPEC = importlib.util.spec_from_file_location(
    "riden_esphome_query",
    Path(__file__).resolve().parents[1] / "script" / "esphome_query.py",
)
assert _SPEC and _SPEC.loader
esphome_query = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(esphome_query)


class FakeAPIConnectionError(Exception):
    """Stands in for aioesphomeapi.APIConnectionError."""


class FakeAPIClient:
    """An in-process ESPHome device with an RD6018 behind its serial proxy."""

    def __init__(
        self, host: str, port: int, password: str | None, noise_psk: str | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.noise_psk = noise_psk
        self.holding: dict[int, int] = dict(HOLDING)
        self.connected = False
        self.subscribed = False
        self.configured: tuple[int, int] | None = None
        self.log: list[str] = []
        self._on_data: Any = None

    async def connect(self, login: bool = False) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def subscribe_serial_proxy_data(self, on_data: Any) -> Any:
        self._on_data = on_data
        return lambda: self.log.append("unsubscribed-callback")

    def serial_proxy_configure(
        self, instance: int, baudrate: int, **kwargs: Any
    ) -> None:
        self.configured = (instance, baudrate)

    def serial_proxy_subscribe(self, instance: int) -> None:
        self.subscribed = True

    def serial_proxy_unsubscribe(self, instance: int) -> None:
        self.subscribed = False

    def serial_proxy_write(self, instance: int, data: bytes) -> None:
        response = self._respond(bytes(data))
        # Deliver asynchronously and in two chunks, like a real serial stream.
        loop = asyncio.get_running_loop()
        loop.call_soon(self._deliver, instance, response[:3])
        loop.call_soon(self._deliver, instance, response[3:])

    def _deliver(self, instance: int, data: bytes) -> None:
        if data:
            self._on_data(SimpleNamespace(instance=instance, data=data))

    def _respond(self, request: bytes) -> bytes:
        crc = esphome_query._crc16(request[:-2]).to_bytes(2, "little")
        assert request[-2:] == crc, "bridge sent a bad CRC"
        unit, function = request[0], request[1]
        address = int.from_bytes(request[2:4], "big")
        if function == 3:
            count = int.from_bytes(request[4:6], "big")
            words = b"".join(
                self.holding.get(address + i, 0).to_bytes(2, "big")
                for i in range(count)
            )
            payload = bytes((unit, function, len(words))) + words
        elif function == 6:
            self.holding[address] = int.from_bytes(request[4:6], "big")
            payload = request[:-2]
        else:  # function 16
            count = int.from_bytes(request[4:6], "big")
            for i in range(count):
                self.holding[address + i] = int.from_bytes(
                    request[7 + i * 2 : 9 + i * 2], "big"
                )
            payload = request[:6]
        return esphome_query._frame(payload)


@pytest.fixture
def fake_aioesphomeapi(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Install a fake aioesphomeapi module and return it."""
    module = ModuleType("aioesphomeapi")
    module.APIClient = FakeAPIClient  # type: ignore[attr-defined]
    module.APIConnectionError = FakeAPIConnectionError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aioesphomeapi", module)
    return module


def test_crc16_known_vector() -> None:
    # Unit 1, FC03, address 0, count 1 is the canonical 01 03 00 00 00 01 84 0A.
    assert esphome_query._frame(bytes.fromhex("010300000001")) == bytes.fromhex(
        "010300000001840a"
    )


def test_parse_args_defaults() -> None:
    args = esphome_query._parse_args(["10.0.0.5"])
    assert args.host == "10.0.0.5"
    assert args.port == 6053
    assert args.key is None
    assert args.instance == 0
    assert args.unit == 1
    assert args.baudrate == 115200


class _PipeUnit:
    """A SerialProxyUnit wired to a scripted responder."""

    def __init__(self, responses: list[bytes], timeout: float = 0.2) -> None:
        self.requests: list[bytes] = []
        self._responses = responses
        self.unit = esphome_query.SerialProxyUnit(
            self._send, unit_id=1, timeout=timeout
        )

    def _send(self, data: bytes) -> None:
        self.requests.append(data)
        if self._responses:
            response = self._responses.pop(0)
            asyncio.get_running_loop().call_soon(self.unit.feed, response)


async def test_unit_read_holding_registers() -> None:
    response = esphome_query._frame(bytes.fromhex("010304EB3100FA"))
    pipe = _PipeUnit([response])
    assert await pipe.unit.read_holding_registers(0, 2) == [0xEB31, 0x00FA]
    assert pipe.requests == [esphome_query._frame(bytes.fromhex("010300000002"))]


async def test_unit_read_input_registers() -> None:
    """FC04 shares the framing with FC03; the RD60xx map never needs it."""
    response = esphome_query._frame(bytes.fromhex("01040200FA"))
    pipe = _PipeUnit([response])
    assert await pipe.unit.read_input_registers(0, 1) == [0x00FA]
    assert pipe.requests[0][1] == 4


async def test_unit_write_register() -> None:
    echo = esphome_query._frame(bytes.fromhex("010600080215"))
    pipe = _PipeUnit([echo])
    await pipe.unit.write_register(8, 533)
    assert pipe.requests == [echo]  # FC06 echoes the request


async def test_unit_write_registers() -> None:
    ack = esphome_query._frame(bytes.fromhex("011000300006"))
    pipe = _PipeUnit([ack])
    await pipe.unit.write_registers(48, [2026, 7, 8, 14, 30, 45])
    request = pipe.requests[0]
    assert request[:7] == bytes.fromhex("0110003000060C")
    assert int.from_bytes(request[7:9], "big") == 2026


async def test_unit_reports_modbus_exception() -> None:
    """A refusal surfaces as the typed class for its code, not a magic number."""
    exception = esphome_query._frame(bytes.fromhex("018302"))
    pipe = _PipeUnit([exception])
    with pytest.raises(IllegalDataAddressError) as caught:
        await pipe.unit.read_holding_registers(0, 1)
    assert caught.value.exception_code == 2


async def test_unit_rejects_bad_crc() -> None:
    good = esphome_query._frame(bytes.fromhex("01030200FA"))
    corrupted = good[:-1] + bytes((good[-1] ^ 0xFF,))
    pipe = _PipeUnit([corrupted])
    with pytest.raises(ModbusProtocolError, match="CRC mismatch"):
        await pipe.unit.read_holding_registers(0, 1)


async def test_unit_times_out_without_response() -> None:
    pipe = _PipeUnit([], timeout=0.05)
    with pytest.raises(ModbusTimeoutError):
        await pipe.unit.read_holding_registers(0, 1)


async def test_unit_times_out_on_expired_deadline() -> None:
    """A deadline that has already passed fails before waiting at all."""
    good = esphome_query._frame(bytes.fromhex("01030200FA"))
    pipe = _PipeUnit([good], timeout=0.0)
    with pytest.raises(ModbusTimeoutError):
        await pipe.unit.read_holding_registers(0, 1)


async def test_unit_times_out_on_partial_response() -> None:
    good = esphome_query._frame(bytes.fromhex("01030200FA"))
    pipe = _PipeUnit([good[:3]], timeout=0.05)  # header only, never completed
    with pytest.raises(ModbusTimeoutError):
        await pipe.unit.read_holding_registers(0, 1)


@pytest.mark.usefixtures("fake_aioesphomeapi")
async def test_run_queries_device(capsys: pytest.CaptureFixture[str]) -> None:
    assert await esphome_query._run(esphome_query._parse_args(["10.0.0.5"])) == 0
    out = capsys.readouterr().out
    assert "RD6018" in out
    assert "Preset M9" in out
    assert "Queried in" in out


async def test_run_passes_connection_options(
    fake_aioesphomeapi: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    clients: list[FakeAPIClient] = []

    class RecordingClient(FakeAPIClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            clients.append(self)

    fake_aioesphomeapi.APIClient = RecordingClient

    args = esphome_query._parse_args(["10.0.0.5", "--key", "abc123", "--port", "6054"])
    assert await esphome_query._run(args) == 0
    (client,) = clients
    assert client.port == 6054
    assert client.noise_psk == "abc123"
    assert client.configured == (0, 115200)
    assert client.connected is False  # disconnected afterwards
    assert client.subscribed is False  # unsubscribed afterwards
    assert "unsubscribed-callback" in client.log


async def test_run_rejects_unsupported_model(
    fake_aioesphomeapi: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    class DPSClient(FakeAPIClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.holding[0] = 52051  # a DPS5205 is not an RD60xx

    fake_aioesphomeapi.APIClient = DPSClient

    assert await esphome_query._run(esphome_query._parse_args(["10.0.0.5"])) == 1
    assert "Unsupported model" in capsys.readouterr().err


async def test_run_reports_connect_error(
    fake_aioesphomeapi: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailingClient(FakeAPIClient):
        async def connect(self, login: bool = False) -> None:
            raise FakeAPIConnectionError("no route to host")

    fake_aioesphomeapi.APIClient = FailingClient

    assert await esphome_query._run(esphome_query._parse_args(["10.0.0.5"])) == 1
    assert "Could not connect" in capsys.readouterr().err


async def test_run_reports_read_error(
    fake_aioesphomeapi: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    class SilentClient(FakeAPIClient):
        def serial_proxy_write(self, instance: int, data: bytes) -> None:
            pass  # the supply never answers

    fake_aioesphomeapi.APIClient = SilentClient

    args = esphome_query._parse_args(["10.0.0.5", "--timeout", "0.05"])
    assert await esphome_query._run(args) == 1
    assert "Error reading device" in capsys.readouterr().err


async def test_run_ignores_other_instances(fake_aioesphomeapi: ModuleType) -> None:
    class TwoPortClient(FakeAPIClient):
        def serial_proxy_write(self, instance: int, data: bytes) -> None:
            # Noise from another proxy instance arrives first and is ignored.
            self._deliver(instance + 1, b"\x01\x03\xff")
            super().serial_proxy_write(instance, data)

    fake_aioesphomeapi.APIClient = TwoPortClient

    assert await esphome_query._run(esphome_query._parse_args(["10.0.0.5"])) == 0


@pytest.mark.usefixtures("fake_aioesphomeapi")
def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["esphome_query.py", "10.0.0.5"])
    assert esphome_query.main() == 0
