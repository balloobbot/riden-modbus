"""The battery-charging block: battery state and the session Ah/Wh counters."""

from __future__ import annotations

from functools import cache

from modbus_connection.model import boolean, gauge, integer, raw_register, uint32

from .model import RidenComponent
from .models import ModelProfile


class Battery(RidenComponent):
    """Battery charging state, external temperature probe and counters.

    The Ah/Wh counters accumulate whenever the output is on (not only in
    battery mode) and reset when the supply powers off. The battery-voltage
    scale differs per model, so that field lives on the model-specific
    subclass built by :func:`battery_class`.
    """

    # Model-scaled field, declared on the subclass battery_class() builds.
    voltage: float | None
    """Battery voltage (V)."""

    active = boolean(32)
    """True while a battery is connected to the rear charging terminals."""

    charge = uint32(38, scale=0.001, unit="Ah")
    """Accumulated charge this session (Ah)."""

    energy = uint32(40, scale=0.001, unit="Wh")
    """Accumulated energy this session (Wh)."""

    # Sign lives in its own register: 0 = positive, 1 = negative.
    _temperature_sign = raw_register(34)
    _temperature_value = integer(35, signed=False)

    @property
    def temperature(self) -> int | None:
        """External temperature probe (°C)."""
        sign = self._temperature_sign
        value = self._temperature_value
        if sign is None or value is None:
            return None
        return -value if sign else value


@cache
def battery_class(profile: ModelProfile) -> type[Battery]:
    """Build the :class:`Battery` subclass carrying a model's scaled fields."""

    class _Battery(Battery):
        voltage = gauge(33, profile.scaling.voltage, signed=False, unit="V")

    return _Battery
