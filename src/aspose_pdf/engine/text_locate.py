"""Locate the page-space rectangles of text matches in a content stream.

This is a best-effort text-position tracker used to draw redaction overlay
boxes. It walks the content stream tracking the CTM, text matrix and text
state, resolving advance widths from the page's simple fonts. For each match of
the search string (matched the same way as the redactor -- across ``TJ`` element
boundaries and across consecutive show operators joined into one logical run) it
returns a quadrilateral in default user space.

It is deliberately conservative: only single-byte simple fonts are handled, and
whenever the pen position cannot be tracked confidently (an unresolved or
multi-byte font, or a UTF-16BE operand) overlay emission is suspended until the
next absolute text-position reset (``BT``/``Tm``/``Td``/``TD``/``T*``). Because
the matched text has already been removed from the content, a skipped box only
means a missing cosmetic mark, never leaked text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .text_edit import _ALL_SHOW_OPS, _NEUTRAL_OPS, _decode_operand, _find_matches, _lex

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


def _mul(m: Matrix, n: Matrix) -> Matrix:
    """Compose affines: apply *m* then *n* (PDF row-vector convention)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _apply(m: Matrix, x: float, y: float) -> Point:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def _nums(operands: List[tuple]) -> List[float]:
    return [v for (kind, *rest) in operands if kind == "num" for v in (rest[0],)]


def _operand_matrix(operands: List[tuple]) -> Optional[Matrix]:
    vals = _nums(operands)
    if len(vals) < 6:
        return None
    return tuple(vals[-6:])  # type: ignore[return-value]


def _last_num(operands: List[tuple], default: float) -> float:
    vals = _nums(operands)
    return vals[-1] if vals else default


def _trailing_nums(operands: List[tuple], k: int) -> Optional[List[float]]:
    vals = _nums(operands)
    return vals[-k:] if len(vals) >= k else None


def _leading_nums(operands: List[tuple], k: int) -> Optional[List[float]]:
    vals = _nums(operands)
    return vals[:k] if len(vals) >= k else None


def _last_name(operands: List[tuple]) -> Optional[str]:
    for entry in reversed(operands):
        if entry[0] == "name":
            return entry[1]
    return None


def _last_string(operands: List[tuple]) -> Optional[tuple]:
    for entry in reversed(operands):
        if entry[0] == "str":
            return entry
    return None


def _last_array(operands: List[tuple]) -> Optional[List[tuple]]:
    for entry in reversed(operands):
        if entry[0] == "arr":
            return entry[1]
    return None


@dataclass
class _TextState:
    ctm: Matrix
    tm: Matrix = _IDENTITY
    tlm: Matrix = _IDENTITY
    font: Optional[SimpleFontMetric] = None
    size: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    h_scale: float = 1.0
    leading: float = 0.0
    rise: float = 0.0
    valid: bool = True  # pen position is currently known


def _show(
    state: _TextState,
    segments: List[tuple],
    search: str,
    quads: List[Quad],
    *,
    case_sensitive: bool,
    remaining: int,
) -> bool:
    """Advance the text matrix over *segments*; append match quads.

    Returns whether the run was tracked confidently. When it was not (no usable
    font, or a UTF-16BE operand), the text matrix is left unchanged and the
    caller suspends overlay emission until the next position reset.
    """
    font = state.font
    if font is None or not state.valid:
        return False

    size = state.size
    chars: List[str] = []
    char_x: List[float] = []
    pen = 0.0
    for seg in segments:
        if seg[0] == "num":
            pen += -seg[1] / 1000.0 * size * state.h_scale
            continue
        text, encoding = _decode_operand(seg[1])
        if encoding != "latin-1":
            return False  # UTF-16BE operand -> not a single-byte simple font
        for ch in text:
            code = ord(ch) & 0xFF
            chars.append(ch)
            char_x.append(pen)
            glyph = font.width_of(code) / 1000.0 * size
            extra = state.char_spacing + (state.word_spacing if code == 32 else 0.0)
            pen += (glyph + extra) * state.h_scale
    char_x.append(pen)  # sentinel: end of the last glyph's advance

    new_tm = _mul((1.0, 0.0, 0.0, 1.0, pen, 0.0), state.tm)
    full = "".join(chars)
    if full:
        spans = _find_matches(full, search, case_sensitive, remaining)
        if spans:
            trm = _mul(state.tm, state.ctm)
            y0 = font.descent / 1000.0 * size + state.rise
            y1 = font.ascent / 1000.0 * size + state.rise
            for ms, me in spans:
                x0 = char_x[ms]
                x1 = char_x[me]
                quads.append(
                    (
                        _apply(trm, x0, y0),
                        _apply(trm, x1, y0),
                        _apply(trm, x1, y1),
                        _apply(trm, x0, y1),
                    )
                )
    state.tm = new_tm
    return True


def locate_matches(
    content: bytes,
    search: str,
    font_for_name: Callable[[str], Optional[SimpleFontMetric]],
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
    base_ctm: Matrix = _IDENTITY,
) -> List[Quad]:
    """Return user-space quads covering each match of *search* in *content*."""
    tokens = _lex(content)
    quads: List[Quad] = []
    state = _TextState(ctm=base_ctm)
    gstack: List[tuple] = []
    operands: List[tuple] = []
    pending: List[tuple] = []  # segments of the current logical show run

    def remaining() -> int:
        return 0 if max_count == 0 else max(max_count - len(quads), 0)

    def flush() -> None:
        """Show the accumulated run as one unit, then reset it.

        Consecutive show operators separated only by neutral operators share the
        same font, spacing and pen, so the whole run is positioned in a single
        ``_show`` pass -- a match spanning the boundary yields one box.
        """
        if pending:
            state.valid = _show(
                state, pending, search, quads,
                case_sensitive=case_sensitive, remaining=remaining(),
            )
            pending.clear()

    i = 0
    n = len(tokens)
    while i < n:
        if max_count and len(quads) >= max_count:
            break
        tok = tokens[i]
        kind = tok.kind
        if kind == "string":
            operands.append(("str", tok.value, tok.style))
            i += 1
            continue
        if kind == "name":
            operands.append(("name", tok.value.lstrip("/")))
            i += 1
            continue
        if kind == "array_start":
            arr: List[tuple] = []
            i += 1
            while i < n and tokens[i].kind != "array_end":
                inner = tokens[i]
                if inner.kind == "string":
                    arr.append(("str", inner.value, inner.style))
                elif inner.kind == "word":
                    val = _to_float(inner.value)
                    if val is not None:
                        arr.append(("num", val))
                i += 1
            i += 1  # skip array_end
            operands.append(("arr", arr))
            continue
        if kind == "array_end":
            i += 1
            continue
        if kind != "word":
            i += 1
            continue

        val = _to_float(tok.value)
        if val is not None:
            operands.append(("num", val))
            i += 1
            continue

        op = tok.value
        if op == "Tj":
            seg = _last_string(operands)
            if seg is not None:
                pending.append(seg)  # accumulate; positioned at the next flush
        elif op == "TJ":
            arr = _last_array(operands)
            if arr is not None:
                pending.extend(arr)
        elif op in ("'", '"'):
            # A line-moving show operator ends the previous run, then begins a
            # new one on the next line that following operators can extend.
            flush()
            if op == '"':
                aw_ac = _leading_nums(operands, 2)
                if aw_ac is not None:
                    state.word_spacing, state.char_spacing = aw_ac
            state.tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), state.tlm)
            state.tm = state.tlm
            state.valid = True
            seg = _last_string(operands)
            if seg is not None:
                pending.append(seg)
        elif op in _NEUTRAL_OPS:
            pass  # keeps the current run open
        else:
            flush()  # any other operator ends the run before it takes effect
            if op == "q":
                gstack.append(
                    (
                        state.ctm, state.font, state.size, state.char_spacing,
                        state.word_spacing, state.h_scale, state.leading, state.rise,
                    )
                )
            elif op == "Q":
                if gstack:
                    (
                        state.ctm, state.font, state.size, state.char_spacing,
                        state.word_spacing, state.h_scale, state.leading, state.rise,
                    ) = gstack.pop()
            elif op == "cm":
                cm = _operand_matrix(operands)
                if cm is not None:
                    state.ctm = _mul(cm, state.ctm)
            elif op == "BT":
                state.tm = state.tlm = _IDENTITY
                state.valid = True
            elif op == "Tf":
                name = _last_name(operands)
                state.size = _last_num(operands, state.size)
                state.font = font_for_name(name) if name is not None else None
            elif op == "Tc":
                state.char_spacing = _last_num(operands, state.char_spacing)
            elif op == "Tw":
                state.word_spacing = _last_num(operands, state.word_spacing)
            elif op == "Tz":
                state.h_scale = _last_num(operands, state.h_scale * 100.0) / 100.0
            elif op == "TL":
                state.leading = _last_num(operands, state.leading)
            elif op == "Ts":
                state.rise = _last_num(operands, state.rise)
            elif op in ("Td", "TD"):
                pair = _trailing_nums(operands, 2)
                if pair is not None:
                    tx, ty = pair
                    if op == "TD":
                        state.leading = -ty
                    state.tlm = _mul((1.0, 0.0, 0.0, 1.0, tx, ty), state.tlm)
                    state.tm = state.tlm
                    state.valid = True
            elif op == "Tm":
                m = _operand_matrix(operands)
                if m is not None:
                    state.tm = state.tlm = m
                    state.valid = True
            elif op == "T*":
                state.tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), state.tlm)
                state.tm = state.tlm
                state.valid = True

        operands = []
        i += 1

    flush()  # position any run still open at end of content
    return quads
