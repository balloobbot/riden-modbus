"""Device identity: the power supply's model, firmware and serial number.

Exposes the fields Home Assistant's ``DeviceInfo`` wants (manufacturer, model,
sw_version, serial_number) directly on the component.
"""

from __future__ import annotations

from modbus_connection.model import gauge, integer, uint32

from .model import RidenComponent
from .models import model_name


class DeviceInformation(RidenComponent):
    """Power-supply identity and firmware version."""

    _model_raw = integer(0, signed=False)
    _serial_raw = uint32(1)
    _firmware_raw = gauge(3, 0.01, signed=False)

    @property
    def manufacturer(self) -> str:
        """Manufacturer name, alongside the other identity properties."""
        return "Riden"

    @property
    def model(self) -> str | None:
        """Model name, e.g. 'RD6018'."""
        value = self._model_raw
        return model_name(value) if value else None

    @property
    def model_code(self) -> int | None:
        """Numeric model code, e.g. 6018."""
        value = self._model_raw
        return value // 10 if value else None

    @property
    def firmware_version(self) -> str | None:
        """Firmware version, e.g. '1.41'."""
        value = self._firmware_raw
        return f"{value:.2f}" if value is not None else None

    @property
    def serial_number(self) -> str | None:
        """Serial number as shown on the device's info screen."""
        value = self._serial_raw
        return f"{value:08d}" if value is not None else None
