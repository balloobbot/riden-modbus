"""The power supply's real-time clock, as native ``datetime`` objects."""

from __future__ import annotations

import datetime

from modbus_connection.model import integer

from .model import RidenComponent, bounded


class Clock(RidenComponent):
    """Device clock, exposed as native ``date`` / ``time`` / ``datetime``."""

    year = integer(48, signed=False, writable=bounded(2000, 2099))
    month = integer(49, signed=False, writable=bounded(1, 12))
    day = integer(50, signed=False, writable=bounded(1, 31))
    hour = integer(51, signed=False, writable=bounded(0, 23))
    minute = integer(52, signed=False, writable=bounded(0, 59))
    second = integer(53, signed=False, writable=bounded(0, 59))

    @property
    def date(self) -> datetime.date | None:
        """Calendar date."""
        year, month, day = self.year, self.month, self.day
        if year is None or month is None or day is None:
            return None
        try:
            return datetime.date(year=year, month=month, day=day)
        except ValueError:
            return None

    @property
    def time(self) -> datetime.time | None:
        """Time of day."""
        hour, minute, second = self.hour, self.minute, self.second
        if hour is None or minute is None or second is None:
            return None
        try:
            return datetime.time(hour=hour, minute=minute, second=second)
        except ValueError:
            return None

    @property
    def datetime(self) -> datetime.datetime | None:
        """Combined date and time."""
        moment = self.time
        if (day := self.date) is None or moment is None:
            return None
        return datetime.datetime.combine(day, moment)

    async def set_datetime(self, value: datetime.datetime) -> None:
        """Set the device clock in one block write."""
        await self._unit.write_registers(
            self.declared_fields["year"].address,
            [
                value.year,
                value.month,
                value.day,
                value.hour,
                value.minute,
                value.second,
            ],
        )
