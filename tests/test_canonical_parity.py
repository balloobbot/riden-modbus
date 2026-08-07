"""Verify modeled fields against the RD60xx reference point list.

The reference (``tests/reference/canonical_points.json``) is the address table
cross-checked between the Baldanos ``rd6006`` register documentation and the
ShayBox ``Riden`` register constants. Scale entries are either a fixed number
or a scale *kind* (``"voltage"`` / ``"current"`` / ``"power"``) resolved
through each model's profile. It is used as a known reference for modeled
fields, but it is not treated as a complete list of everything the library
must expose.

This test catches wrong addresses, scales, or read-only/writable labels for
fields that are currently modeled, across every supported model profile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from modbus_connection.model import RegisterField

from riden_modbus import RD60xx, profile_for

_REF = json.loads(
    (Path(__file__).parent / "reference" / "canonical_points.json").read_text()
)
CANON_REG: dict[int, dict[str, Any]] = {e["id"]: e for e in _REF["registers"].values()}

# Every supported (model ID, current range) combination.
MODELS = [
    (60062, 0),
    (60065, 0),
    (60066, 0),
    (60121, 0),
    (60125, 0),
    (60125, 1),
    (60181, 0),
    (60241, 0),
]


def _fields(model: int, current_range: int) -> list[tuple[str, int, RegisterField]]:
    """Every (component, effective address, field) across all components."""
    device = RD60xx(unit=None, model=model, current_range=current_range)  # type: ignore[arg-type]
    out: list[tuple[str, int, RegisterField]] = []
    for component in device.components:
        label = type(component).__name__ + (
            f"[{component._index}]" if component._index != 1 else ""
        )
        for field in component.declared_fields.values():
            out.append((label, component._address(field), field))
    return out


REGISTER_CASES = [
    pytest.param(
        model,
        current_range,
        label,
        addr,
        field,
        id=f"{profile_for(model, current_range=current_range).name}"
        f"{'/high' if current_range else ''}.{label}.{field.name}",
    )
    for model, current_range in MODELS
    for label, addr, field in _fields(model, current_range)
]


@pytest.mark.parametrize(
    ("model", "current_range", "label", "address", "field"), REGISTER_CASES
)
def test_register_matches_canonical(
    model: int, current_range: int, label: str, address: int, field: RegisterField
) -> None:
    assert address in CANON_REG, f"{label}.{field.name} address {address} not in spec"
    entry = CANON_REG[address]
    # Plain scaled numbers (not enum-mapped) must match the canonical scale,
    # resolving voltage/current/power kinds through the model profile.
    scale = getattr(field, "scale", None)
    if scale is not None and getattr(field, "enum_type", None) is None:
        expected = entry["scale"]
        if isinstance(expected, str):
            scaling = profile_for(model, current_range=current_range).scaling
            expected = getattr(scaling, expected)
        assert scale == pytest.approx(expected), (
            f"{label}.{field.name} scale {scale} != spec {expected} ({entry['name']})"
        )
    if field.writable:
        assert entry["access"] == "rw", f"{label}.{field.name} is read-only in the spec"


def test_no_field_spans_out_of_spec() -> None:
    """Multi-word fields (uint32) stay inside documented registers."""
    for label, address, field in _fields(60181, 0):
        for offset in range(field.count):
            assert address + offset in CANON_REG, (
                f"{label}.{field.name} word {offset} at {address + offset} not in spec"
            )
