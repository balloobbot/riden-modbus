"""The top-level RD60xx device object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modbus_connection.model import Component, ComponentGroup

from .battery import battery_class
from .clock import Clock
from .device_info import DeviceInformation
from .models import ModelProfile, is_supported_model, model_name, profile_for
from .output import output_class
from .preset import preset_class
from .settings import Settings

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

PRESET_COUNT = 10


@dataclass(frozen=True)
class RD60xxProbe:
    """Result of the safe setup probe."""

    model: int
    serial_number: str
    firmware_version: str
    current_range: int

    @property
    def model_name(self) -> str:
        """Return the user-facing model name."""
        return model_name(self.model)

    @property
    def is_supported(self) -> bool:
        """Whether this library models the probed device."""
        return is_supported_model(self.model)


class RD60xx:
    """A Riden RD60xx (or RK6006) power supply."""

    def __init__(
        self,
        unit: ModbusUnit,
        *,
        model: int = 60181,
        current_range: int = 0,
    ) -> None:
        self._unit = unit
        self.model = model
        self.profile: ModelProfile = profile_for(model, current_range=current_range)

        self.info = DeviceInformation(unit)
        self.output = output_class(self.profile)(unit)
        self.battery = battery_class(self.profile)(unit)
        self.clock = Clock(unit)
        self.settings = Settings(unit)
        presets = preset_class(self.profile)
        self.presets = tuple(presets(unit, index=m + 1) for m in range(PRESET_COUNT))

        self._group = ComponentGroup(unit, self.components)

    @classmethod
    async def async_probe(cls, unit: ModbusUnit) -> RD60xxProbe:
        """Read only safe identity data for setup.

        Reads up to the current-range register (20) in one request, so the
        result carries everything needed to construct the right model profile.
        """
        registers = await unit.read_holding_registers(0, 21)
        serial = (registers[1] << 16) | registers[2]
        return RD60xxProbe(
            model=int(registers[0]),
            serial_number=f"{serial:08d}",
            firmware_version=f"{registers[3] / 100:.2f}",
            current_range=int(registers[20]),
        )

    @property
    def components(self) -> tuple[Component, ...]:
        """Return every actively polled subsystem."""
        return (
            self.info,
            self.output,
            self.battery,
            self.clock,
            self.settings,
            *self.presets,
        )

    async def async_update(self) -> None:
        """Refresh all subsystems in pooled Modbus reads."""
        await self._group.async_update()

    async def async_read_raw(self) -> dict[str, dict[int, int | bool]]:
        """Refresh, and return every modeled register word by address.

        The undecoded view of the same read :meth:`async_update` performs —
        for capturing a device dump when a decoded value looks wrong.
        """
        return await self._group.async_read_raw()

    async def async_recall_preset(self, number: int) -> None:
        """Load preset group M0-M9 into the active setpoints."""
        await self.output.async_write_datapoint("active_preset", number)
