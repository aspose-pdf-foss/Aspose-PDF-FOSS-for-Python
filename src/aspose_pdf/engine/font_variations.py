"""The axis machinery an OpenType variable font is read through.

A variable font stores one outline plus *deltas*, and which deltas apply is
decided by where the requested instance sits on each axis: ``fvar`` declares the
axes, the request is normalised into ``[-1, 1]``, ``avar`` may reshape that
response, and each variation region contributes in proportion to a scalar
computed from the result (OpenType 1.9, "OpenType Font Variations Common Table
Formats").

None of that is particular to how the outlines themselves are stored. CFF2
carries its deltas inside the charstrings and ``glyf`` carries them in ``gvar``,
but both ask the same questions of the same tables, so the answers live here and
:mod:`.cff_outlines` and :mod:`.glyph_outlines` share them.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any

__all__ = [
    "apply_avar",
    "normalize_coordinates",
    "parse_fvar_axes",
    "region_scalar",
    "sfnt_table",
]


def parse_fvar_axes(data: bytes) -> list[dict[str, Any]]:
    """Axis records from an SFNT's ``fvar`` table, or ``[]``."""
    table = sfnt_table(data, b"fvar")
    if table is None or len(table) < 16:
        return []
    try:
        axes_offset, _, axis_count, axis_size = struct.unpack_from(">HHHH", table, 4)
    except struct.error:
        return []
    axes: list[dict[str, Any]] = []
    for index in range(axis_count):
        start = axes_offset + index * axis_size
        if start + 20 > len(table):
            break
        tag = table[start : start + 4].decode("latin-1")
        minimum, default, maximum = struct.unpack_from(">lll", table, start + 4)
        axes.append(
            {
                "tag": tag,
                "min": minimum / 65536.0,
                "default": default / 65536.0,
                "max": maximum / 65536.0,
            }
        )
    return axes

def sfnt_table(data: bytes, tag: bytes) -> bytes | None:
    """The named table of an SFNT wrapper, or ``None`` for a bare CFF."""
    if len(data) < 12 or data[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        return None
    try:
        count = struct.unpack_from(">H", data, 4)[0]
        for index in range(count):
            record = 12 + index * 16
            if data[record : record + 4] != tag:
                continue
            offset, length = struct.unpack_from(">II", data, record + 8)
            if offset + length <= len(data):
                return data[offset : offset + length]
    except struct.error:
        return None
    return None

def normalize_coordinates(
    axes: list[dict[str, Any]],
    variation: Mapping[str, float] | None,
    raw: bytes,
) -> tuple[float, ...]:
    """User axis values -> normalised [-1, 1] coordinates, ``avar`` applied."""
    if not axes:
        return ()
    coords: list[float] = []
    for axis in axes:
        value = None if variation is None else variation.get(axis["tag"])
        if value is None:
            coords.append(0.0)
            continue
        value = max(axis["min"], min(axis["max"], float(value)))
        default = axis["default"]
        if value == default:
            coords.append(0.0)
        elif value < default:
            span = default - axis["min"]
            coords.append((value - default) / span if span else 0.0)
        else:
            span = axis["max"] - default
            coords.append((value - default) / span if span else 0.0)
    return tuple(apply_avar(raw, coords))

def apply_avar(raw: bytes, coords: list[float]) -> list[float]:
    """Apply the ``avar`` segment maps, which reshape an axis's response."""
    table = sfnt_table(raw, b"avar")
    if table is None or len(table) < 8:
        return coords
    try:
        axis_count = struct.unpack_from(">H", table, 6)[0]
    except struct.error:
        return coords
    cursor = 8
    out = list(coords)
    for axis in range(min(axis_count, len(out))):
        try:
            pair_count = struct.unpack_from(">H", table, cursor)[0]
        except struct.error:
            return out
        cursor += 2
        pairs: list[tuple[float, float]] = []
        for _ in range(pair_count):
            try:
                from_value, to_value = struct.unpack_from(">hh", table, cursor)
            except struct.error:
                return out
            pairs.append((from_value / 16384.0, to_value / 16384.0))
            cursor += 4
        out[axis] = piecewise(pairs, out[axis])
    return out

def piecewise(pairs: list[tuple[float, float]], value: float) -> float:
    if len(pairs) < 2:
        return value
    for index in range(1, len(pairs)):
        left_from, left_to = pairs[index - 1]
        right_from, right_to = pairs[index]
        if left_from <= value <= right_from:
            span = right_from - left_from
            if span <= 0:
                return left_to
            ratio = (value - left_from) / span
            return left_to + ratio * (right_to - left_to)
    return value

def region_scalar(
    region: list[tuple[float, float, float]], coords: tuple[float, ...]
) -> float:
    """The region's weight at *coords* (OpenType variation scalar)."""
    scalar = 1.0
    for axis, (start, peak, end) in enumerate(region):
        if peak == 0.0:
            continue  # this axis does not participate in the region
        value = coords[axis] if axis < len(coords) else 0.0
        if value == peak:
            continue
        if value <= start or value >= end:
            return 0.0
        if value < peak:
            scalar *= (value - start) / (peak - start) if peak != start else 0.0
        else:
            scalar *= (end - value) / (end - peak) if end != peak else 0.0
    return scalar
