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
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union

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
    """Advance metrics for a composite (Type0) font.

    ``width_of`` resolves a CID to its horizontal advance from the CIDFont's
    ``/W`` array (falling back to ``/DW``). ``code_to_text`` is the font's
    code-bytes -> text mapping (ToUnicode, or reconstructed from an embedded
    CIDFontType2 cmap) used to index match positions over the same decoded
    text the redactor edits. ``code_to_cid`` maps a show-string code (of any
    byte length) to its CID; ``None`` means the identity mapping where the code
    *is* the two-byte CID (Identity-H/V). ``vertical`` selects vertical writing
    (Identity-V or an embedded CMap with ``WMode 1``), where glyphs stack down
    a column at a uniform one-em advance.
    """

    width_of: Callable[[int], float]
    code_to_text: Mapping[bytes, str]
    code_to_cid: Optional[Callable[[bytes], Optional[int]]] = None
    vertical: bool = False
    ascent: float = 800.0
    descent: float = -200.0


FontMetric = Union[SimpleFontMetric, CompositeFontMetric]


def _cid_of(metric: Any, code: bytes) -> Optional[int]:
    """Resolve a composite show-string code to its CID."""
    fn = getattr(metric, "code_to_cid", None)
    if fn is not None:
        return fn(code)
    if len(code) == 2:  # identity: the two-byte code is the CID
        return int.from_bytes(code, "big")
    return None  # a truncated/odd-length identity code is untrackable


def _apply(m: Matrix, x: float, y: float) -> Point:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _char_geometry(run, infos) -> Optional[List[Tuple[float, float, float, float, float]]]:
    """Per-char ``(bx0, bx1, by0, by1, line_key)`` boxes in the run's text space.

    Each entry is an axis-aligned box plus a grouping key identifying its
    baseline (horizontal writing) or column (vertical writing); chars of a
    multi-char code unit (a ligature) share the unit's whole extent and
    synthesized gap chars cover the gap. Vertical composite runs stack glyphs
    down a one-em-wide column at a uniform one-em advance. Returns ``None`` when
    any segment cannot be measured (the caller then draws no boxes for the run).
    """
    geometry: List[Tuple[float, float, float, float, float]] = []
    for seg, (kind, text, units) in zip(run.segments, infos):
        if seg.pen is None:
            return None
        pen_x, pen_y = seg.pen
        y = pen_y + seg.rise
        metric = seg.metric
        size = seg.size
        ascent = getattr(metric, "ascent", 800.0) / 1000.0 * size
        descent = getattr(metric, "descent", -200.0) / 1000.0 * size
        vertical = bool(getattr(metric, "vertical", False))
        if kind == "virtual":
            for _ch in text:
                if vertical:
                    box = (pen_x - 0.5 * size, pen_x + 0.5 * size,
                           y - seg.gap_width, y, round(pen_x, 3))
                else:
                    box = (pen_x, pen_x + seg.gap_width,
                           y + descent, y + ascent, round(y, 3))
                geometry.append(box)
            continue
        if metric is None:
            return None
        raw = seg.token.value
        if kind == "cid":
            if vertical:
                col0, col1 = pen_x - 0.5 * size, pen_x + 0.5 * size
                key = round(pen_x, 3)
                yy = y
                for _off, _length, unit_text in units:
                    advance = size + seg.char_spacing  # uniform one-em stack
                    for _ch in unit_text:
                        geometry.append((col0, col1, yy - advance, yy, key))
                    yy -= advance
            else:
                x = pen_x
                for off, length, unit_text in units:
                    cid = _cid_of(metric, raw[off : off + length])
                    if cid is None:
                        return None
                    glyph = metric.width_of(cid) / 1000.0 * size
                    advance = (glyph + seg.char_spacing) * seg.h_scale
                    for _ch in unit_text:
                        geometry.append(
                            (x, x + advance, y + descent, y + ascent, round(y, 3))
                        )
                    x += advance
        elif kind == "latin-1":
            x = pen_x
            for ch in text:
                code = ord(ch) & 0xFF
                glyph = metric.width_of(code) / 1000.0 * size
                extra = seg.char_spacing + (
                    seg.word_spacing if code == 32 else 0.0
                )
                advance = (glyph + extra) * seg.h_scale
                geometry.append(
                    (x, x + advance, y + descent, y + ascent, round(y, 3))
                )
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
    """One quad per baseline/column covered by the matched char range."""
    quads: List[Quad] = []
    i = start
    while i < end:
        bx0, bx1, by0, by1, key = geometry[i]
        j = i + 1
        while j < end and geometry[j][4] == key:
            gx0, gx1, gy0, gy1, _ = geometry[j]
            bx0 = min(bx0, gx0)
            bx1 = max(bx1, gx1)
            by0 = min(by0, gy0)
            by1 = max(by1, gy1)
            j += 1
        quads.append(
            (
                _apply(trm, bx0, by0),
                _apply(trm, bx1, by0),
                _apply(trm, bx1, by1),
                _apply(trm, bx0, by1),
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
