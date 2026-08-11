#!/usr/bin/env python3
"""Query a Riden RD60xx through an ESPHome ``serial_proxy`` bridge.

Connects to the ESPHome device over its native API, opens the proxied serial
port (see ``esphome/rd60xx-serial-proxy.yaml``), speaks Modbus RTU across it,
and dumps every sub-system's values to the terminal — the same output as
``script/query.py``, but through the ESP inside the supply instead of a
Modbus TCP gateway or a local serial port.

Requires the ``esphome`` extra::

    uv run --extra esphome python script/esphome_query.py 10.0.0.5
    uv run --extra esphome python script/esphome_query.py 10.0.0.5 --key <api-key>

``--key`` is the device's API encryption key; omit it for a keyless
(provisioning-mode) device.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from modbus_connection import (
    ModbusError,
    ModbusExceptionError,
    ModbusProtocolError,
    ModbusTimeoutError,
)

from riden_modbus import RD60xx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import query  # noqa: E402  (sibling module: reuses the printing helpers)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="hostname or IP of the ESPHome device")
    parser.add_argument("--port", type=int, default=6053, help="default: 6053")
    parser.add_argument("--key", default=None, help="API encryption key (base64)")
    parser.add_argument(
        "--instance",
        type=int,
        default=0,
        help="serial_proxy instance index on the device (default: 0)",
    )
    parser.add_argument(
        "--unit",
        type=int,
        default=1,
        help="Modbus unit/station address (default: 1)",
    )
    parser.add_argument("--baudrate", type=int, default=115200, help="default: 115200")
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="per-request response timeout in seconds (default: 2.0)",
    )
    return parser.parse_args(argv)


def _crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _frame(payload: bytes) -> bytes:
    """Append the RTU CRC (little-endian) to a payload."""
    return payload + _crc16(payload).to_bytes(2, "little")


class SerialProxyUnit:
    """A ``ModbusUnit`` speaking Modbus RTU over an ESPHome serial proxy.

    ``write`` is a callable sending raw bytes to the proxy (the aioesphomeapi
    write is fire-and-forget); incoming proxy data is pushed in via
    :meth:`feed`. Requests are serialized by the modbus-connection framework,
    so one in-flight request at a time suffices.
    """

    def __init__(
        self,
        write,
        *,
        unit_id: int = 1,
        timeout: float = 2.0,
    ) -> None:
        self._write = write
        self._unit_id = unit_id
        self._timeout = timeout
        self._buffer = bytearray()
        self._received = asyncio.Event()

    def feed(self, data: bytes) -> None:
        """Push bytes received from the proxied serial port."""
        self._buffer.extend(data)
        self._received.set()

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._read_registers(3, address, count)

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        return await self._read_registers(4, address, count)

    async def _read_registers(
        self, function: int, address: int, count: int
    ) -> list[int]:
        payload = (
            bytes((self._unit_id, function))
            + address.to_bytes(2, "big")
            + count.to_bytes(2, "big")
        )
        response = await self._request(payload, 5 + 2 * count)
        data = response[3 : 3 + 2 * count]
        return [
            int.from_bytes(data[offset : offset + 2], "big")
            for offset in range(0, len(data), 2)
        ]

    async def write_register(self, address: int, value: int) -> None:
        payload = (
            bytes((self._unit_id, 6))
            + address.to_bytes(2, "big")
            + value.to_bytes(2, "big")
        )
        await self._request(payload, 8)

    async def write_registers(self, address: int, values: list[int]) -> None:
        data = b"".join(value.to_bytes(2, "big") for value in values)
        payload = (
            bytes((self._unit_id, 16))
            + address.to_bytes(2, "big")
            + len(values).to_bytes(2, "big")
            + bytes((len(data),))
            + data
        )
        await self._request(payload, 8)

    async def _request(self, payload: bytes, expected: int) -> bytes:
        """Send one RTU request and return the validated response frame."""
        self._buffer.clear()
        self._write(_frame(payload))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            self._received.clear()
            if (frame := self._complete_frame(expected)) is not None:
                return frame
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ModbusTimeoutError("No response from device")
            try:
                await asyncio.wait_for(self._received.wait(), remaining)
            except TimeoutError:
                raise ModbusTimeoutError("No response from device") from None

    def _complete_frame(self, expected: int) -> bytes | None:
        """Return the response frame once fully buffered, else None."""
        if len(self._buffer) < 2:
            return None
        # An exception response is always 5 bytes: unit, func|0x80, code, CRC.
        length = 5 if self._buffer[1] & 0x80 else expected
        if len(self._buffer) < length:
            return None
        frame = bytes(self._buffer[:length])
        if _crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
            raise ModbusProtocolError(f"CRC mismatch in response {frame.hex()}")
        if frame[1] & 0x80:
            # A refusal is a well-formed answer, not a broken frame: hand the
            # code to modbus-connection so it raises the typed class for it
            # (IllegalDataAddressError and friends) instead of a magic number.
            raise ModbusExceptionError.from_code(frame[2])
        return frame


async def _run(args: argparse.Namespace) -> int:
    # Imported here so the module loads (and --help works) without the extra.
    from aioesphomeapi import APIClient, APIConnectionError

    client = APIClient(args.host, args.port, None, noise_psk=args.key)
    try:
        await client.connect(login=True)
    except APIConnectionError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1

    try:
        unit = SerialProxyUnit(
            lambda data: client.serial_proxy_write(args.instance, data),
            unit_id=args.unit,
            timeout=args.timeout,
        )

        def on_data(message) -> None:
            if message.instance == args.instance:
                unit.feed(message.data)

        unsubscribe = client.subscribe_serial_proxy_data(on_data)
        client.serial_proxy_configure(args.instance, args.baudrate)
        client.serial_proxy_subscribe(args.instance)
        try:
            probe = await RD60xx.async_probe(unit)  # type: ignore[arg-type]
            if not probe.is_supported:
                print(
                    f"Unsupported model: {probe.model_name} (ID {probe.model})",
                    file=sys.stderr,
                )
                return 1
            device = RD60xx(
                unit,  # type: ignore[arg-type]
                model=probe.model,
                current_range=probe.current_range,
            )
            start = time.monotonic()
            await device.async_update()
            elapsed = time.monotonic() - start
        except ModbusError as err:
            print(f"Error reading device: {err}", file=sys.stderr)
            return 1
        finally:
            client.serial_proxy_unsubscribe(args.instance)
            unsubscribe()
    finally:
        await client.disconnect()
    query._print(device)
    print(f"\nQueried in {elapsed * 1000:.0f} ms")
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
