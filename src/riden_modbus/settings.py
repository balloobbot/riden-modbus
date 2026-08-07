"""Front-panel settings: the device's menu options."""

from __future__ import annotations

from modbus_connection.model import boolean, enum, integer

from .enums import Language
from .model import RidenComponent, bounded


class Settings(RidenComponent):
    """The options behind the device's settings menu."""

    take_ok = boolean(66, writable=True)
    """Ask for confirmation when recalling a preset group."""

    take_out = boolean(67, writable=True)
    """Keep the output on when recalling a preset group."""

    boot_power = boolean(68, writable=True)
    """Turn the output on at power-up."""

    buzzer = boolean(69, writable=True)
    """Key-press buzzer."""

    logo = boolean(70, writable=True)
    """Show the logo at boot."""

    language = enum(71, Language, writable=True)
    """Front-panel UI language."""

    backlight = integer(72, signed=False, writable=bounded(0, 5))
    """Backlight brightness level (0-5)."""
