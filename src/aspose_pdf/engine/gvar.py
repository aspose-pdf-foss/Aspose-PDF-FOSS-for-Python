"""``gvar``: the per-point deltas a variable TrueType font varies its outlines by.

A variable ``glyf`` font stores one set of outlines and, in ``gvar``, the
*deltas* that move their points for each region of the design space (OpenType
1.9, "gvar"). Drawing an instance means, per glyph, scaling each region's deltas
by how much that region applies at the requested coordinates and adding them up.

Two things make it more than a table read. A tuple may list *which* points it
moves, leaving the rest to be inferred from their neighbours -- the rule
OpenType calls IUP, and without it a sparse tuple would tear the outline apart.
And a composite glyph's deltas move its *components*, one delta each, not the
points those components are built from.

The deltas of the four phantom points every glyph carries (the two side bearings
and the two vertical ones) are parsed so the point numbering stays right, and
then ignored: they move metrics, not outlines.
"""

from __future__ import annotations

import struct

from .font_variations import region_scalar

#: A tuple whose own peak follows its header rather than naming a shared one.
_EMBEDDED_PEAK = 0x8000
#: A tuple that names where its region starts and ends, not only its peak.
_INTERMEDIATE_REGION = 0x4000
#: A tuple that lists the points it moves, rather than using the shared list.
_PRIVATE_POINT_NUMBERS = 0x2000
_TUPLE_INDEX_MASK = 0x0FFF

#: The glyph's tuples all move the same points, listed once before them.
_SHARED_POINT_NUMBERS = 0x8000
_TUPLE_COUNT_MASK = 0x0FFF

_DELTAS_ARE_ZERO = 0x80
_DELTAS_ARE_WORDS = 0x40
_DELTA_RUN_COUNT_MASK = 0x3F

_POINTS_ARE_WORDS = 0x80
_POINT_RUN_COUNT_MASK = 0x7F

#: Every glyph carries these four beyond its own points (OpenType, "gvar").
PHANTOM_POINTS = 4


class GlyphVariations:
    """The ``gvar`` table of one font, read on demand per glyph."""

    __slots__ = ("_axis_count", "_data", "_glyph_count", "_long_offsets",
                 "_offsets_at", "_ok", "_shared_tuples", "_variation_data_at")

    def __init__(self, table: bytes | None, axis_count: int) -> None:
        self._data = bytes(table or b"")
        self._axis_count = int(axis_count)
        self._ok = False
        self._glyph_count = 0
        self._long_offsets = False
        self._offsets_at = 0
        self._variation_data_at = 0
        self._shared_tuples: list[tuple[float, ...]] = []
        if len(self._data) >= 20 and self._axis_count > 0:
            try:
                self._parse_header()
            except (struct.error, IndexError, ValueError):
                self._ok = False

    @property
    def ok(self) -> bool:
        """``True`` when a usable ``gvar`` table was read."""
        return self._ok

    def _parse_header(self) -> None:
        data = self._data
        (
            major,
            _minor,
            axis_count,
            shared_count,
            shared_at,
            self._glyph_count,
            flags,
            self._variation_data_at,
        ) = struct.unpack_from(">HHHHIHHI", data, 0)
        if major != 1 or axis_count != self._axis_count:
            return
        self._long_offsets = bool(flags & 1)
        self._offsets_at = 20
        for index in range(shared_count):
            start = shared_at + index * 2 * self._axis_count
            if start + 2 * self._axis_count > len(data):
                break
            self._shared_tuples.append(
                tuple(
                    value / 16384.0
                    for value in struct.unpack_from(
                        f">{self._axis_count}h", data, start
                    )
                )
            )
        self._ok = self._glyph_count > 0

    def _glyph_range(self, gid: int) -> tuple[int, int] | None:
        if not self._ok or gid < 0 or gid >= self._glyph_count:
            return None
        data = self._data
        if self._long_offsets:
            at = self._offsets_at + gid * 4
            if at + 8 > len(data):
                return None
            first, second = struct.unpack_from(">II", data, at)
        else:
            at = self._offsets_at + gid * 2
            if at + 4 > len(data):
                return None
            first, second = struct.unpack_from(">HH", data, at)
            first, second = first * 2, second * 2
        start = self._variation_data_at + first
        end = self._variation_data_at + second
        # Equal offsets mean the glyph does not vary; the bytes at that point
        # belong to the glyph after it. The read below would refuse them anyway,
        # since its own data cannot begin outside the range it was given -- this
        # says so at the boundary rather than leaving it to be noticed there.
        if second <= first or end > len(data):
            return None
        return start, end

    def deltas(
        self,
        gid: int,
        point_count: int,
        coordinates: tuple[float, ...],
        contour_ends: list[int] | None,
        points: list[tuple[float, float]] | None,
    ) -> list[tuple[float, float]] | None:
        """The total movement of each of *gid*'s points at *coordinates*.

        *point_count* counts the glyph's own points; the four phantom points are
        read and discarded. *contour_ends* and *points* are what inference needs
        -- the contour a point belongs to, and where it started -- and may be
        ``None`` for a composite glyph, whose deltas move whole components and
        are never inferred.

        ``None`` means this glyph does not vary, which is not the same as
        varying by nothing.
        """
        span = self._glyph_range(gid)
        if span is None or point_count <= 0:
            return None
        start, end = span
        data = self._data
        total_points = point_count + PHANTOM_POINTS
        try:
            header = struct.unpack_from(">HH", data, start)
        except struct.error:
            return None
        # The count shares its word with a flag, so it has to be masked out of
        # it. A count read with the flag still in it would be far too large,
        # and the loop would run out of readable tuples and stop -- but on a
        # table laid out differently it would read whatever followed instead.
        tuple_count = header[0] & _TUPLE_COUNT_MASK
        shares_points = bool(header[0] & _SHARED_POINT_NUMBERS)
        cursor = start + 4
        serialized = start + header[1]
        if tuple_count == 0 or serialized > end:
            return None

        shared_points: list[int] | None = None
        if shares_points:
            shared_points, serialized = _read_point_numbers(
                data, serialized, end, total_points
            )
            if shared_points is None:
                return None

        result = [(0.0, 0.0)] * total_points
        varied = False
        for _ in range(tuple_count):
            try:
                size, tuple_index = struct.unpack_from(">HH", data, cursor)
            except struct.error:
                break
            cursor += 4
            peak, cursor = self._peak_of(tuple_index, cursor, end)
            if peak is None:
                break
            if tuple_index & _INTERMEDIATE_REGION:
                region, cursor = self._intermediate_region(peak, cursor, end)
            else:
                region = [
                    (min(0.0, value), value, max(0.0, value)) for value in peak
                ]
            if region is None:
                break
            scalar = region_scalar(region, coordinates)
            body_end = min(serialized + size, end)
            # A region that does not reach these coordinates contributes deltas
            # multiplied by nothing, so skipping it is an economy rather than a
            # decision: most of a font's tuples are for other parts of the
            # design space, and reading them would be the bulk of the work.
            if scalar:
                deltas = self._tuple_deltas(
                    data,
                    serialized,
                    body_end,
                    tuple_index,
                    shared_points,
                    total_points,
                    contour_ends,
                    points,
                )
                if deltas is not None:
                    varied = True
                    result = [
                        (x + scalar * dx, y + scalar * dy)
                        for (x, y), (dx, dy) in zip(result, deltas)
                    ]
            serialized = body_end
        # Nothing applied here means this glyph does not vary at these
        # coordinates, which the caller reads as "leave the outline alone". The
        # zeros would leave it alone too; saying so costs the caller nothing and
        # saves it adding them point by point.
        return result[:point_count] if varied else None

    def _peak_of(
        self, tuple_index: int, cursor: int, end: int
    ) -> tuple[tuple[float, ...] | None, int]:
        if tuple_index & _EMBEDDED_PEAK:
            width = 2 * self._axis_count
            if cursor + width > end:
                return None, cursor
            peak = tuple(
                value / 16384.0
                for value in struct.unpack_from(
                    f">{self._axis_count}h", self._data, cursor
                )
            )
            return peak, cursor + width
        index = tuple_index & _TUPLE_INDEX_MASK
        if index >= len(self._shared_tuples):
            return None, cursor
        return self._shared_tuples[index], cursor

    def _intermediate_region(
        self, peak: tuple[float, ...], cursor: int, end: int
    ) -> tuple[list[tuple[float, float, float]] | None, int]:
        width = 2 * self._axis_count
        if cursor + 2 * width > end:
            return None, cursor
        starts = struct.unpack_from(f">{self._axis_count}h", self._data, cursor)
        ends = struct.unpack_from(
            f">{self._axis_count}h", self._data, cursor + width
        )
        region = [
            (starts[axis] / 16384.0, peak[axis], ends[axis] / 16384.0)
            for axis in range(self._axis_count)
        ]
        return region, cursor + 2 * width

    def _tuple_deltas(
        self,
        data: bytes,
        cursor: int,
        end: int,
        tuple_index: int,
        shared_points: list[int] | None,
        total_points: int,
        contour_ends: list[int] | None,
        points: list[tuple[float, float]] | None,
    ) -> list[tuple[float, float]] | None:
        """One tuple's movement of every point, inferring the ones it omits."""
        if tuple_index & _PRIVATE_POINT_NUMBERS:
            numbers, cursor = _read_point_numbers(data, cursor, end, total_points)
            if numbers is None:
                return None
        else:
            numbers = shared_points
            if numbers is None:
                return None
        count = len(numbers) if numbers else total_points
        xs, cursor = _read_packed_deltas(data, cursor, end, count)
        ys, _cursor = _read_packed_deltas(data, cursor, end, count)
        if xs is None or ys is None:
            return None
        if not numbers:  # every point is listed, so nothing is left to infer
            return list(zip(xs, ys))[:total_points]

        sparse: dict[int, tuple[float, float]] = {}
        for index, number in enumerate(numbers):
            if 0 <= number < total_points:
                sparse[number] = (float(xs[index]), float(ys[index]))
        if contour_ends is None or points is None:
            # A composite's deltas move its components; there is nothing
            # between them to interpolate.
            return [sparse.get(index, (0.0, 0.0)) for index in range(total_points)]
        return _infer_unreferenced(sparse, total_points, contour_ends, points)


def _read_point_numbers(
    data: bytes, cursor: int, end: int, total_points: int
) -> tuple[list[int] | None, int]:
    """The packed point numbers at *cursor*; an empty list means "all of them"."""
    if cursor >= end:
        return None, cursor
    count = data[cursor]
    cursor += 1
    if count & _POINTS_ARE_WORDS:
        if cursor >= end:
            return None, cursor
        count = ((count & _POINT_RUN_COUNT_MASK) << 8) | data[cursor]
        cursor += 1
    if count == 0:
        # The format's way of saying "every point", and the empty list is how
        # that is passed on. The loop below would also stop at once and return
        # the same thing; this states the meaning rather than arriving at it.
        return [], cursor
    numbers: list[int] = []
    value = 0
    while len(numbers) < count:
        if cursor >= end:
            return None, cursor
        control = data[cursor]
        cursor += 1
        run = (control & _POINT_RUN_COUNT_MASK) + 1
        if control & _POINTS_ARE_WORDS:
            if cursor + 2 * run > end:
                return None, cursor
            for step in range(run):
                value += struct.unpack_from(">H", data, cursor + 2 * step)[0]
                numbers.append(value)
            cursor += 2 * run
        else:
            if cursor + run > end:
                return None, cursor
            for step in range(run):
                value += data[cursor + step]
                numbers.append(value)
            cursor += run
    return numbers[:count], cursor


def _read_packed_deltas(
    data: bytes, cursor: int, end: int, count: int
) -> tuple[list[int] | None, int]:
    """*count* packed deltas at *cursor*, runs of zeros and words expanded."""
    out: list[int] = []
    while len(out) < count:
        if cursor >= end:
            return None, cursor
        control = data[cursor]
        cursor += 1
        run = (control & _DELTA_RUN_COUNT_MASK) + 1
        if control & _DELTAS_ARE_ZERO:
            out.extend([0] * run)
        elif control & _DELTAS_ARE_WORDS:
            if cursor + 2 * run > end:
                return None, cursor
            out.extend(
                struct.unpack_from(">h", data, cursor + 2 * step)[0]
                for step in range(run)
            )
            cursor += 2 * run
        else:
            if cursor + run > end:
                return None, cursor
            out.extend(
                struct.unpack_from(">b", data, cursor + step)[0]
                for step in range(run)
            )
            cursor += run
    return out[:count], cursor


def _infer_unreferenced(
    sparse: dict[int, tuple[float, float]],
    total_points: int,
    contour_ends: list[int],
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Fill in the points a tuple left out (OpenType calls this IUP).

    A point between two the tuple moved follows them in proportion to where it
    sits between them; one beyond them all moves with the nearest. A contour
    with a single moved point moves with it entirely, and one with none does not
    move. The two coordinates are inferred independently, because a point's
    position along one axis says nothing about the other.
    """
    deltas = [sparse.get(index, None) for index in range(total_points)]
    start = 0
    for end in (*contour_ends, total_points - 1):
        last = min(end, total_points - 1)
        if last >= start:
            _infer_contour(deltas, sparse, start, last, points)
        start = last + 1
    return [value if value is not None else (0.0, 0.0) for value in deltas]


def _infer_contour(
    deltas: list[tuple[float, float] | None],
    sparse: dict[int, tuple[float, float]],
    start: int,
    end: int,
    points: list[tuple[float, float]],
) -> None:
    referenced = [index for index in range(start, end + 1) if index in sparse]
    if not referenced:
        return
    if len(referenced) == 1:
        only = sparse[referenced[0]]
        for index in range(start, end + 1):
            deltas[index] = only
        return
    for axis in (0, 1):
        for position, left in enumerate(referenced):
            right = referenced[(position + 1) % len(referenced)]
            gap = _between(left, right, start, end)
            if not gap:
                continue
            for index in gap:
                value = _interpolate(
                    points[index][axis] if index < len(points) else 0.0,
                    points[left][axis] if left < len(points) else 0.0,
                    points[right][axis] if right < len(points) else 0.0,
                    sparse[left][axis],
                    sparse[right][axis],
                )
                current = deltas[index] or (0.0, 0.0)
                deltas[index] = (
                    (value, current[1]) if axis == 0 else (current[0], value)
                )


def _between(left: int, right: int, start: int, end: int) -> list[int]:
    """The point numbers strictly between *left* and *right*, going round."""
    if left == right:
        return []
    out: list[int] = []
    index = left + 1 if left < end else start
    while index != right:
        out.append(index)
        index = index + 1 if index < end else start
        if len(out) > end - start:
            return []
    return out


def _interpolate(
    value: float,
    left_position: float,
    right_position: float,
    left_delta: float,
    right_delta: float,
) -> float:
    """Where a point between two moved ones ends up, along one coordinate."""
    if left_position > right_position:
        left_position, right_position = right_position, left_position
        left_delta, right_delta = right_delta, left_delta
    if value <= left_position:
        return left_delta
    if value >= right_position:
        return right_delta
    span = right_position - left_position
    if span <= 0:
        return left_delta
    ratio = (value - left_position) / span
    return left_delta + ratio * (right_delta - left_delta)
