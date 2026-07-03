"""Locate the page-space rectangles of text matches in a content stream.

This is a best-effort text-position tracker used to draw redaction overlay
boxes. It reuses the editor's geometric show-run walker
(:func:`aspose_pdf.engine.text_edit._walk_show_runs`), so matches are grouped,
decoded and filtered exactly like the redactor edits them -- across ``TJ``
element boundaries, consecutive show operators, font changes, and
same-baseline positioning operators. For each match it returns one
quadrilateral per baseline in default user space.

It is deliberately conservative: single-byte simple fonts and Identity-H
composite fonts (decoded through the font's ToUnicode CMap) are handled, and
a run whose pen cannot be tracked confidently (an unresolved font, a UTF-16BE
operand, or an odd-length CID string) emits no boxes. Because the matched
text has already been removed from the content, a skipped box only means a
missing cosmetic mark, never leaked text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Tuple, Union

from .text_edit import (
    _aligned_spans,
    _lex,
    _run_char_data,
    _walk_show_runs,
)

Matrix = Tuple[float, float, float, float, float, float]
Point = Tuple[float, float]
Quad = Tuple[Point, Point, Point, Point]

_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass(frozen=True)
class SimpleFontMetric:
    """Advance metrics for a single-byte simple font, in 1000-unit glyph space."""

    width_of: Callable[[int], float]
    ascent: float = 800.0
    descent: float = -200.0


@dataclass(frozen=True)
class CompositeFontMetric:
    """Advance metrics for an Identity-H composite (Type0) font.

    ``width_of`` resolves a two-byte CID to its advance from the CIDFont's
    ``/W`` array (falling back to ``/DW``); ``code_to_text`` is the font's
    ToUnicode mapping restricted to two-byte codes, used to index match
    positions over the same decoded text the redactor edits.
    """

    width_of: Callable[[int], float]
    code_to_text: Mapping[bytes, str]
    ascent: float = 800.0
    descent: float = -200.0


FontMetric = Union[SimpleFontMetric, CompositeFontMetric]


def _apply(m: Matrix, x: float, y: float) -> Point:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _char_geometry(run, infos) -> Optional[List[Tuple[float, float, float, float, float]]]:
    """Per-char ``(x0, x1, y, low, high)`` in the run origin's text space.

    Chars of a multi-char code unit (a ligature) share the unit's whole
    extent; synthesized gap chars cover the gap. Returns ``None`` when any
    segment cannot be measured (the caller then draws no boxes for the run).
    """
    geometry: List[Tuple[float, float, float, float, float]] = []
    for seg, (kind, text, units) in zip(run.segments, infos):
        if seg.pen is None:
            return None
        pen_x, pen_y = seg.pen
        y = pen_y + seg.rise
        metric = seg.metric
        ascent = getattr(metric, "ascent", 800.0) / 1000.0 * seg.size
        descent = getattr(metric, "descent", -200.0) / 1000.0 * seg.size
        if kind == "virtual":
            for _ch in text:
                geometry.append((pen_x, pen_x + seg.gap_width, y, descent, ascent))
            continue
        if metric is None:
            return None
        raw = seg.token.value
        if kind == "cid":
            x = pen_x
            for off, length, unit_text in units:
                if length != 2:
                    return None
                cid = int.from_bytes(raw[off : off + 2], "big")
                glyph = metric.width_of(cid) / 1000.0 * seg.size
                advance = (glyph + seg.char_spacing) * seg.h_scale
                for _ch in unit_text:
                    geometry.append((x, x + advance, y, descent, ascent))
                x += advance
        elif kind == "latin-1":
            x = pen_x
            for ch in text:
                code = ord(ch) & 0xFF
                glyph = metric.width_of(code) / 1000.0 * seg.size
                extra = seg.char_spacing + (
                    seg.word_spacing if code == 32 else 0.0
                )
                advance = (glyph + extra) * seg.h_scale
                geometry.append((x, x + advance, y, descent, ascent))
                x += advance
        else:
            return None  # UTF-16BE operand -> byte codes are not glyph codes
    return geometry


def _span_quads(
    trm: Matrix,
    geometry: List[Tuple[float, float, float, float, float]],
    start: int,
    end: int,
) -> List[Quad]:
    """One quad per baseline covered by the matched char range."""
    quads: List[Quad] = []
    i = start
    while i < end:
        x0, x1, y, low, high = geometry[i]
        y0 = y + low
        y1 = y + high
        j = i + 1
        while j < end and abs(geometry[j][2] - y) <= 1e-9:
            gx0, gx1, gy, glow, ghigh = geometry[j]
            x0 = min(x0, gx0)
            x1 = max(x1, gx1)
            y0 = min(y0, gy + glow)
            y1 = max(y1, gy + ghigh)
            j += 1
        quads.append(
            (
                _apply(trm, x0, y0),
                _apply(trm, x1, y0),
                _apply(trm, x1, y1),
                _apply(trm, x0, y1),
            )
        )
        i = j
    return quads


def locate_matches(
    content: bytes,
    search: str,
    font_for_name: Callable[[str], Optional[FontMetric]],
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
    base_ctm: Matrix = _IDENTITY,
) -> List[Quad]:
    """Return user-space quads covering each match of *search* in *content*.

    Runs, decoding and match filtering mirror the redactor exactly (the same
    walker and span alignment are used), so every returned box corresponds to
    text the redactor would remove. A match spanning several baselines (a
    line-moving ``'``/``"`` inside the run) yields one quad per baseline.
    """
    tokens = _lex(content)
    runs = _walk_show_runs(
        tokens, metric_for_name=font_for_name, base_ctm=base_ctm
    )
    quads: List[Quad] = []
    matches = 0
    for run in runs:
        if max_count and matches >= max_count:
            break
        if not run.geometry_ok or run.origin_trm is None:
            continue
        infos, full, entries, _seg_starts = _run_char_data(run)
        if not full:
            continue
        geometry = _char_geometry(run, infos)
        if geometry is None:
            continue
        remaining = 0 if max_count == 0 else max_count - matches
        spans = _aligned_spans(full, entries, search, case_sensitive, remaining)
        for start, end in spans:
            matches += 1
            quads.extend(_span_quads(run.origin_trm, geometry, start, end))
    return quads
