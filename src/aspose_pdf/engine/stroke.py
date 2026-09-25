"""User-space dash splitting and stroke outlines (ISO 32000-1, 8.4.3)."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.load_limits import _LoadBudget

Point = tuple[float, float]


class Subpath(list[Point]):
    """Keep an explicit closepath distinct from a line back to the start."""

    closed = False


@dataclass
class StrokeRun:
    points: list[Point]
    closed: bool = False
    # A zero-length dash still has the direction of its underlying segment.
    direction: Point | None = None


def dash_runs(
    points: list[Point],
    closed: bool,
    pattern: tuple[float, ...],
    phase: float,
    budget: _LoadBudget,
) -> list[StrokeRun]:
    """Split one subpath, restarting the dash phase at its first point."""
    clean = [p for i, p in enumerate(points) if not i or p != points[i - 1]]
    if not clean:
        return []
    if len(clean) == 1:
        return [StrokeRun(clean)] if closed or len(points) > 1 else []
    if not pattern:
        return [StrokeRun(clean, closed)]

    lengths = [math.dist(p, q) for p, q in pairwise(clean)]
    period = sum(pattern)
    work = sum(lengths) / period * len(pattern)
    if not math.isfinite(work):
        raise PdfResourceLimitException("Stroke dash expansion exceeds finite geometry")
    budget.check(math.floor(work), "max_container_items", "stroke dash expansion")
    budget.check(
        math.ceil(work + len(clean)) * 128,
        "max_codec_work_bytes",
        "stroke dash working set",
    )
    phase %= period
    index = 0
    while phase > 0 and phase >= pattern[index]:
        phase -= pattern[index]
        index = (index + 1) % len(pattern)
    remaining = pattern[index] - phase
    runs: list[StrokeRun] = []
    current: list[Point] = []
    steps = 0

    def advance() -> None:
        nonlocal index, remaining, steps
        steps += 1
        budget.check(steps, "max_container_items", "stroke dash transitions")
        index = (index + 1) % len(pattern)
        remaining = pattern[index]

    for p, q, length in zip(clean, clean[1:], lengths):
        direction = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
        position = 0.0
        cursor = p
        while True:
            if position >= length:
                break
            # Zero-length on entries paint a cap pair at the current point.
            while remaining == 0:
                if index % 2 == 0:
                    runs.append(StrokeRun([cursor], direction=direction))
                advance()
            distance = min(remaining, length - position)
            end = position + distance
            if end == position:
                raise PdfResourceLimitException(
                    "Stroke dash is too small for path precision"
                )
            endpoint = (
                q
                if end >= length
                else (p[0] + direction[0] * end, p[1] + direction[1] * end)
            )
            if index % 2 == 0:
                if not current:
                    current.append(cursor)
                if endpoint != current[-1]:
                    current.append(endpoint)
            position, cursor = end, endpoint
            remaining -= distance
            if remaining <= 0:
                if current:
                    runs.append(StrokeRun(current, direction=direction))
                    current = []
                advance()
    if current:
        runs.append(StrokeRun(current, direction=direction))
    # Join across the seam only when the final dash continues through it.
    # A dash ending exactly at the corner gets its cap before the turn.
    if closed and current and runs:
        first, last = runs[0], runs[-1]
        if (
            len(first.points) > 1
            and len(last.points) > 1
            and first.points[0] == clean[0] == last.points[-1]
        ):
            if first is last:
                first.closed = True
            else:
                runs[0] = StrokeRun(last.points + first.points[1:])
                runs.pop()
    return runs


def stroke_polygons(
    run: StrokeRun,
    radius: float,
    cap: int,
    join: int,
    miter_limit: float,
    device_scale: float,
) -> Iterator[list[Point]]:
    """Yield overlapping pen regions; callers must union them before painting."""
    points = [p for i, p in enumerate(run.points) if not i or p != run.points[i - 1]]

    def circle(p: Point) -> list[Point]:
        # Bound the approximation error to roughly a tenth of a device pixel.
        size = radius * device_scale
        count = max(16, min(1024, math.ceil(math.pi * math.sqrt(max(1.0, size / 0.2)))))
        return [
            (
                p[0] + radius * math.cos(i * math.tau / count),
                p[1] + radius * math.sin(i * math.tau / count),
            )
            for i in range(count)
        ]

    def offset(p: Point, d: Point, amount: float) -> Point:
        return p[0] + d[0] * amount, p[1] + d[1] * amount

    def cap_polygon(p: Point, direction: Point, side: float) -> list[Point]:
        normal = (-direction[1], direction[0])
        tip = offset(p, direction, side * radius)
        return [
            offset(p, normal, radius),
            offset(tip, normal, radius),
            offset(tip, normal, -radius),
            offset(p, normal, -radius),
        ]

    if len(points) == 1:
        if cap == 1:
            yield circle(points[0])
        elif cap == 2 and run.direction is not None:
            yield cap_polygon(points[0], run.direction, -1)
            yield cap_polygon(points[0], run.direction, 1)
        return

    directions: list[Point] = []
    for p, q in pairwise(points):
        length = math.dist(p, q)
        direction = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
        directions.append(direction)
        normal = (-direction[1], direction[0])
        yield [
            offset(p, normal, radius),
            offset(q, normal, radius),
            offset(q, normal, -radius),
            offset(p, normal, -radius),
        ]

    vertices = [
        (points[i], directions[i - 1], directions[i]) for i in range(1, len(points) - 1)
    ]
    if run.closed:
        vertices.append((points[0], directions[-1], directions[0]))
    else:
        for point, direction, side in (
            (points[0], directions[0], -1),
            (points[-1], directions[-1], 1),
        ):
            if cap == 1:
                yield circle(point)
            elif cap == 2:
                yield cap_polygon(point, direction, side)

    for point, incoming, outgoing in vertices:
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        if join == 1:
            yield circle(point)
            continue
        if abs(cross) < 1e-12:
            continue
        side = -1 if cross > 0 else 1
        p = offset(point, (-incoming[1], incoming[0]), side * radius)
        q = offset(point, (-outgoing[1], outgoing[0]), side * radius)
        if join == 0:
            distance = (
                (q[0] - p[0]) * outgoing[1] - (q[1] - p[1]) * outgoing[0]
            ) / cross
            tip = offset(p, incoming, distance)
            if math.dist(tip, point) <= radius * miter_limit:
                yield [point, p, tip, q]
                continue
        yield [point, p, q]
