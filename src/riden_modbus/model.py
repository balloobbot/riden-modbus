"""The RD60xx component base and the pieces layered on modbus-connection.

The field factories (``gauge``, ``integer``, ``boolean`` ...) come straight from
``modbus_connection.model``; this module only adds what that framework does not
already provide: a bounded-write validator and the range-constrained
:class:`RidenComponent`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from modbus_connection.model import Component

from .exceptions import RidenValueValidationError
from .ranges import REGISTER_RANGES


def bounded(
    min_value: float | int | None = None,
    max_value: float | int | None = None,
) -> Callable[[Any], Any]:
    """Return a write validator that rejects out-of-range numeric values.

    Passed as a field's ``writable`` argument: modbus-connection calls it with
    the requested value before encoding, and a raise aborts the write.
    """

    def validate(value: Any) -> Any:
        number = float(value)
        if min_value is not None and number < min_value:
            raise RidenValueValidationError(
                f"Value {value} is below minimum {min_value}"
            )
        if max_value is not None and number > max_value:
            raise RidenValueValidationError(
                f"Value {value} is above maximum {max_value}"
            )
        return value

    return validate


class RidenComponent(Component):
    """An RD60xx sub-system constrained to the device's readable range."""

    register_ranges = REGISTER_RANGES

    async def async_write_datapoint(self, field: str, value: Any) -> None:
        """Write a data point by field name.

        The public write entry point for integrations. Unlike some Modbus
        devices the RD60xx needs no write-unlock sequence, so this delegates
        straight to the generic component write path.
        """
        await self.write(field, value)
