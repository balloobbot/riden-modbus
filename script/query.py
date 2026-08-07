#!/usr/bin/env python3
"""Query a Riden RD60xx over Modbus and print every value.

Connects over Modbus TCP (a network gateway) or a serial/USB port, reads the
whole device once, and dumps every sub-system's values to the terminal. Handy
for checking a real power supply without Home Assistant.

The library only needs the connection *protocol*; the backend comes from the
``cli`` extra::

    uv run --extra cli python script/query.py 192.168.1.50 --unit 1
    uv run --extra cli python script/query.py /dev/ttyUSB0 --transport serial

The supplies speak Modbus RTU, so a network gateway is a transparent serial
bridge: framing defaults to ``rtu`` (pass ``--framer socket`` for a gateway
speaking native Modbus TCP) and serial to 115200 baud, 8N1.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from modbus_connection import ModbusError
from modbus_connection.cli_helper import (
    CountingUnit,
    add_connection_args,
    connect_from_args,
    print_component,
)

from riden_modbus import RD60xx

# (label, attribute name on RD60xx) — the order things are printed.
SECTIONS: list[tuple[str, str]] = [
    ("Device", "info"),
    ("Output", "output"),
    ("Battery", "battery"),
    ("Clock", "clock"),
    ("Settings", "settings"),
]

# The connections a Riden can be reached over. It speaks RTU — either straight
# down a serial line, or through a gateway that may re-frame it as native
# Modbus TCP. ASCII framing, UDP and Modbus/TLS have no place in that picture.
CONNECTIONS: tuple[tuple[str, str | None], ...] = (
    ("tcp", "rtu"),
    ("tcp", "socket"),
    ("serial", "rtu"),
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        # add_connection_args describes the framer as backend-defaulted; this
        # tool pins it below, so say what the effective defaults are.
        epilog=(
            "Defaults suit a Riden: RTU framing (pass --framer socket for a "
            "gateway speaking native Modbus TCP) and 115200 baud, 8N1 on "
            "serial."
        ),
    )
    group = add_connection_args(parser, connections=CONNECTIONS)
    group.add_argument(
        "--unit",
        type=int,
        default=1,
        help="Modbus unit/station address (default: 1)",
    )
    # add_connection_args leaves framing and baud rate to the backend; the
    # supplies need RTU at 115200, so they are the defaults here.
    parser.set_defaults(framer="rtu", baudrate=115200)
    args = parser.parse_args(argv)
    # --transport and --framer are independent flags, so argparse accepts pairs
    # CONNECTIONS never declared (serial can only be framed as RTU). Reject
    # those here rather than letting the params dataclass raise on construction.
    if (args.transport, args.framer) not in CONNECTIONS:
        parser.error(
            f"--framer {args.framer} is not valid for --transport {args.transport}"
        )
    return args


def _print(device: RD60xx) -> None:
    sections = [(label, getattr(device, attr)) for label, attr in SECTIONS]
    sections += [(f"Preset M{preset.number}", preset) for preset in device.presets]
    for label, component in sections:
        print()
        print_component(component, title=label)


async def _run(args: argparse.Namespace) -> int:
    # Requests would connect on demand, but connecting up front reports an
    # unreachable device as a connection failure rather than a failed read.
    try:
        connection = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1
    unit = CountingUnit(connection.for_unit(args.unit))
    try:
        probe = await RD60xx.async_probe(unit)
        if not probe.is_supported:
            print(
                f"Unsupported model: {probe.model_name} (ID {probe.model})",
                file=sys.stderr,
            )
            return 1
        device = RD60xx(unit, model=probe.model, current_range=probe.current_range)
        start = time.monotonic()
        await device.async_update()
        elapsed = time.monotonic() - start
    except ModbusError as err:
        print(f"Error reading device: {err}", file=sys.stderr)
        return 1
    finally:
        # close() is permanent — the right end for a one-shot query.
        await connection.close()
    _print(device)
    print(f"\nQueried in {elapsed * 1000:.0f} ms ({unit.reads} Modbus reads)")
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
