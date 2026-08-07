# riden-modbus

`riden-modbus` is an asynchronous Python library for reading and controlling Riden RD60xx bench power supplies over Modbus.

The library is backend-neutral: it consumes a [`modbus_connection.ModbusUnit`](https://github.com/home-assistant-libs/modbus-connection) and does not create or own the transport itself. Applications can therefore use `tmodbus`, `pymodbus`, or another backend supported by `modbus-connection`.

## Features

- Object-oriented access to power-supply subsystems
- Automatic model probe with identity data (model, serial, firmware)
- Model-aware value scaling and output limits
- Full-map pooled reads — one Modbus request refreshes the whole device
- Read and write support with field-specific write validation
- All ten preset groups (M0-M9), including recall
- Neutral metadata for units, limits, steps, enums, and writable flags

## Supported models

All models share one register map and differ only in scaling and limits:

| Model   | ID register | Voltage scale | Current scale   | Max output  |
| ------- | ----------- | ------------- | --------------- | ----------- |
| RD6006  | 60060-60064 | 0.01 V        | 0.001 A         | 60 V / 6 A  |
| RD6006P | 60065       | 0.001 V       | 0.0001 A        | 60 V / 6 A  |
| RK6006  | 60066       | 0.01 V        | 0.001 A         | 60 V / 6 A  |
| RD6012  | 60120-60124 | 0.01 V        | 0.01 A          | 60 V / 12 A |
| RD6012P | 60125-60129 | 0.001 V       | range-dependent | 60 V / 12 A |
| RD6018  | 60180-60189 | 0.01 V        | 0.01 A          | 60 V / 18 A |
| RD6024  | 60241-60249 | 0.01 V        | 0.01 A          | 60 V / 24 A |

The RD6012P's current resolution follows its selected current range (register 20): 0.1 mA on the 6 A range, 1 mA on the 12 A range. The probe reads that register, and `RD60xx` must be constructed with the probed `current_range`; if the range is changed on the device, re-probe and rebuild the device object.

## Device structure

An `RD60xx` object exposes the following subsystems:

| Attribute  | Description                                                           |
| ---------- | --------------------------------------------------------------------- |
| `info`     | Model, firmware version, and serial-number information                |
| `output`   | Setpoints, live measurements, protection, CV/CC state, internal temp  |
| `battery`  | Battery-charging state, probe temperature, and Ah/Wh counters         |
| `clock`    | The device's real-time clock                                          |
| `settings` | Front-panel menu options (buzzer, backlight, language, ...)           |
| `presets`  | The stored preset groups M0-M9                                        |

The register map is documented in the [Baldanos `rd6006` register notes](https://github.com/Baldanos/rd6006/blob/master/registers.md) and the [ShayBox `Riden` register constants](https://github.com/ShayBox/Riden); addresses are plain zero-based Modbus addresses. Registers 12-13 are one 32-bit power value — an RD6018 tops out at 1080 W, past a single word's range. The Fahrenheit mirrors of the temperature registers (6-7, 36-37) are derived values and not modeled; the calibration registers (55-62) and maintenance registers (`SYSTEM`, bootloader) are deliberately left alone.

## Basic usage

Install the library together with the desired `modbus-connection` backend.

Example using `tmodbus` and transparent RTU over TCP:

```python
import asyncio

from modbus_connection import ModbusTcpParams
from modbus_connection.tmodbus import ModbusConnection
from riden_modbus import RD60xx


async def main() -> None:
    connection = ModbusConnection(
        ModbusTcpParams(
            host="192.168.1.50",
            port=502,
            framer="rtu",
        )
    )

    try:
        unit = connection.for_unit(1)

        probe = await RD60xx.async_probe(unit)
        if not probe.is_supported:
            raise RuntimeError(f"Unsupported model: {probe.model_name}")

        device = RD60xx(
            unit,
            model=probe.model,
            current_range=probe.current_range,
        )
        await device.async_update()

        print("Model:", device.info.model)
        print("Output voltage:", device.output.voltage)
        print("Output power:", device.output.power)
        print("Battery charge:", device.battery.charge)

        await device.output.set_voltage(13.5)
        await device.output.set_current(2.5)
        await device.output.set_enabled(True)
    finally:
        await connection.close()


asyncio.run(main())
```

Constructing the connection performs no I/O — the first request establishes the link, and a dropped link reconnects on the next one, so nothing here needs to watch for or recover from a disconnect. `close()` is the permanent end of the connection.

For a serial/USB connection, build a `ModbusSerialParams` instead (the supplies default to 115200 baud, 8N1, station address 1).

## Metadata and writes

The library is the source of truth for neutral Riden datapoint metadata:

- register reference
- value type
- model-aware scaling
- unit
- model-aware minimum and maximum
- step
- enum options
- writable state

Writes use:

```python
await component.async_write_datapoint(field, value)
```

Field validation is applied before the value reaches the device; the RD60xx needs no write-unlock sequence.

`await device.async_read_raw()` performs the same pooled read as `async_update()` but returns the undecoded register words keyed by address — useful for capturing a device dump when a decoded value looks wrong.

## ESPHome bridge firmware

`esphome/rd60xx-serial-proxy.yaml` is a ready-made, distributable firmware for the ESP8266 module inside the supply. It exposes the Modbus RTU TTL link through ESPHome's `serial_proxy` component, making the ESP a transparent serial bridge that Home Assistant can drive with this library.

The firmware contains no credentials, so prebuilt images can be flashed as-is: Wi-Fi is provisioned through the fallback hotspot's captive portal, the API starts keyless so Home Assistant generates and stores an encryption key on adoption, and `name_add_mac_suffix` keeps multiple supplies distinct. Build locally with `esphome run esphome/rd60xx-serial-proxy.yaml`.

CI compiles the firmware on every pull request that touches it, and pushes to `main` publish the built image to GitHub Pages with a browser-based [ESP Web Tools](https://esphome.github.io/esp-web-tools/) installer, served at `https://jesserockz.github.io/riden-modbus/`.

## Command-line query tool

The repository contains `script/query.py` for querying a power supply without Home Assistant. It probes the model automatically.

Install the CLI backend:

```bash
python -m pip install -e ".[cli]"
```

Examples:

```bash
python script/query.py 192.168.1.50 --unit 1
python script/query.py /dev/ttyUSB0 --transport serial --unit 1
python script/query.py 192.168.1.50 --transport udp
```

The framing defaults to `rtu` — the supplies speak Modbus RTU, and a network gateway is normally a transparent serial bridge. Pass `--framer socket` for a gateway speaking native Modbus TCP/UDP. Run with `--help` for the full set of connection options.

For a supply running the ESPHome bridge firmware, `script/esphome_query.py` does the same through the ESPHome native API instead:

```bash
python -m pip install -e ".[esphome]"
python script/esphome_query.py 10.0.0.5 --key <api-encryption-key>
```

Omit `--key` for a freshly flashed device that has not been adopted yet.

## Development and tests

Install the project in editable mode and run the test suite:

```bash
uv sync
uv run pytest
uvx prek run --all-files
```

The test suite uses the in-memory mock backend provided by `modbus-connection`; no physical power supply or external Modbus server is required.

## Releases

Every push to `main` updates a draft GitHub release: Release Drafter resolves the next version from PR labels, `pyproject.toml` is bumped to match, and the built distributions are attached to the draft — required up front because releases are immutable once published. While assets are still uploading the draft body carries a warning; once it disappears, the draft is ready to be published manually, which triggers the PyPI publish of exactly those attached files.
