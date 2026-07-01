"""Heuristic auto-tagging of existing page content for PDF/UA structure.

Wraps the text and image content of a page in marked-content sequences so it can
be reflected in the structure tree -- turning untagged content into a real (if
coarse) tag tree instead of an empty catalog shell.  Text objects (``BT`` ...
``ET``) and image paints (``/Name Do``) are located together with their page
position, sorted into reading order (top-to-bottom, then left-to-right) and
grouped so consecutive body-text lines collapse into a single paragraph
(``/P``).  Headings are inferred from font size relative to the page's dominant
body size; each heading and each figure is its own structure element.

This is a heuristic *aid*, not certified accessibility: reading order is derived
from geometry rather than semantics, paragraph grouping is proximity-based (it
cannot see columns, lists or tables), and images are described with a
placeholder ``/Alt``.  Pages that already carry marked content are left
untouched.

The rewrite is a pure byte splice -- ``BDC``/``EMC`` are inserted around the
existing operators without re-serializing them -- so the original content is
preserved exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

__all__ = [
    "TextObject",
    "LayoutElement",
    "find_text_objects",
    "find_xobject_invocations",
    "find_layout_elements",
    "assign_reading_order",
    "group_into_paragraphs",
    "has_marked_content",
    "choose_tags",
    "build_tagged_content",
]

# Whitespace and delimiter bytes that end a regular token (PDF 32000-1 §7.2).
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_ENDERS = _WS + _DELIM

_HEADING_RATIO = 1.4

# Paragraph-grouping heuristics (see :func:`group_into_paragraphs`).
_PARA_SIZE_MIN = 0.8      # min ratio of consecutive line font sizes to still group
_PARA_SIZE_MAX = 1.25     # max ratio ...
_LINE_TOL_RATIO = 0.35    # |Δbaseline| within this * font size == same line
_PARA_GAP_RATIO = 1.6     # baseline step up to this * font size stays in-paragraph

Matrix = Tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass
class TextObject:
    """A ``BT`` ... ``ET`` text object located in a content stream."""

    start: int  # byte offset of 'B' in 'BT'
    end: int  # byte offset just past 'T' in 'ET'
    max_font_size: float
    text_length: int  # total bytes of strings shown (a body-vs-heading weight)


@dataclass
class LayoutElement:
    """A positioned piece of page content (a text object or an image paint).

    *x*/*y* are the element's page-space anchor: the baseline origin of the
    first shown glyph for text, and the centre of the placed unit square for an
    image.  *tag* and *alt* are filled in by the caller before ordering/grouping.
    """

    kind: str  # "text" or "xobject"
    start: int
    end: int
    x: float = 0.0
    y: float = 0.0
    font_size: float = 0.0
    text_length: int = 0
    name: Optional[str] = None  # xobject resource name, with leading slash
    tag: Optional[str] = None
    alt: Optional[str] = None


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


def _apply(m: Matrix, x: float, y: float) -> Tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _tokens(content: bytes) -> Iterator[Tuple[Optional[str], int, int]]:
    """Yield ``(token, start, end)`` for a content stream.

    ``token`` is the operator/operand/name text, or ``None`` for a literal or
    hex string (whose span is still reported so callers can measure it).
    Strings and comments are consumed so their bytes are never mistaken for
    operators (e.g. a ``(BT)`` literal is not a text object).
    """
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x25:  # '%' comment to end of line
            while i < n and content[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x28:  # '(' literal string
            start = i
            depth = 1
            i += 1
            while i < n and depth > 0:
                ch = content[i]
                if ch == 0x5C:  # backslash escape
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                i += 1
            yield (None, start, i)
            continue
        if c == 0x3C:  # '<'
            if i + 1 < n and content[i + 1] == 0x3C:  # '<<' dict open
                yield ("<<", i, i + 2)
                i += 2
                continue
            start = i
            i += 1
            while i < n and content[i] != 0x3E:  # up to '>'
                i += 1
            i += 1
            yield (None, start, i)
            continue
        if c == 0x3E:  # '>'
            if i + 1 < n and content[i + 1] == 0x3E:
                yield (">>", i, i + 2)
                i += 2
                continue
            i += 1
            continue
        if c in b"[]{}":
            yield (chr(c), i, i + 1)
            i += 1
            continue
        if c == 0x2F:  # '/' name
            start = i
            i += 1
            while i < n and content[i] not in _ENDERS:
                i += 1
            yield (content[start:i].decode("latin-1"), start, i)
            continue
        start = i
        while i < n and content[i] not in _ENDERS:
            i += 1
        if i == start:  # defensive: never stall on a stray delimiter
            i += 1
            continue
        token = content[start:i].decode("latin-1")
        yield (token, start, i)
        if token == "ID":  # inline image: skip the raw binary up to 'EI'
            i = _skip_inline_image(content, i, n)


def _skip_inline_image(content: bytes, i: int, n: int) -> int:
    """Return the offset past the ``EI`` that ends an inline image's data.

    The bytes between ``ID`` and ``EI`` are arbitrary image samples and must not
    be tokenized (they could otherwise masquerade as operators or strings).
    """
    if i < n and content[i] in _WS:
        i += 1
    j = i
    while j + 1 < n:
        if content[j] == 0x45 and content[j + 1] == 0x49:  # 'EI'
            prev_ws = j == 0 or content[j - 1] in _WS
            after = content[j + 2] if j + 2 < n else 0x20
            if prev_ws and (j + 2 >= n or after in _ENDERS):
                return j + 2
        j += 1
    return n


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _to_float(token: str) -> Optional[float]:
    try:
        return float(token)
    except ValueError:
        return None


def has_marked_content(content: bytes) -> bool:
    """Return ``True`` if the stream already contains marked-content operators."""
    for token, _start, _end in _tokens(content):
        if token in ("BDC", "BMC", "DP", "MP"):
            return True
    return False


def find_layout_elements(content: bytes) -> List[LayoutElement]:
    """Locate positioned text objects and image paints in *content*, in stream order.

    Tracks the CTM (``q``/``Q``/``cm``) and text matrix (``Tm``/``Td``/``TD``/
    ``T*``) so each element carries a page-space anchor for reading-order
    sorting.  Text objects also carry their maximum font size and shown-text
    length; image paints carry the invoked resource name.
    """
    elements: List[LayoutElement] = []
    ctm: Matrix = _IDENTITY
    ctm_stack: List[Matrix] = []
    tm: Matrix = _IDENTITY
    tlm: Matrix = _IDENTITY
    leading = 0.0

    in_text = False
    start = 0
    max_size = 0.0
    text_length = 0
    anchor: Optional[Tuple[float, float]] = None

    nums: List[float] = []
    last_name: Optional[Tuple[str, int]] = None

    def record_show() -> None:
        nonlocal anchor
        if in_text and anchor is None:
            anchor = _apply(_mul(tm, ctm), 0.0, 0.0)

    for token, tok_start, tok_end in _tokens(content):
        if token is None:  # a string literal / hex string operand
            if in_text:
                text_length += tok_end - tok_start
            continue
        if token in ("[", "]", "{", "}", "<<", ">>"):
            continue  # array / dict punctuation: not an operator, keep operands
        if token.startswith("/"):
            last_name = (token, tok_start)
            continue
        val = _to_float(token)
        if val is not None:
            nums.append(val)
            continue

        # A bare keyword: an operator. Dispatch, then clear pending operands.
        op = token
        if op == "q":
            ctm_stack.append(ctm)
        elif op == "Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
        elif op == "cm":
            if len(nums) >= 6:
                ctm = _mul(tuple(nums[-6:]), ctm)  # type: ignore[arg-type]
        elif op == "BT":
            in_text = True
            start = tok_start
            max_size = 0.0
            text_length = 0
            anchor = None
            tm = tlm = _IDENTITY
        elif op == "ET":
            if in_text:
                ax, ay = anchor if anchor is not None else _apply(_mul(tm, ctm), 0.0, 0.0)
                elements.append(
                    LayoutElement(
                        "text", start, tok_end, ax, ay, max_size, text_length
                    )
                )
            in_text = False
        elif op == "Tf":
            if nums:
                max_size = max(max_size, abs(nums[-1]))
        elif op == "TL":
            if nums:
                leading = nums[-1]
        elif op in ("Td", "TD"):
            if len(nums) >= 2:
                tx, ty = nums[-2], nums[-1]
                if op == "TD":
                    leading = -ty
                tlm = _mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                tm = tlm
        elif op == "Tm":
            if len(nums) >= 6:
                tm = tlm = tuple(nums[-6:])  # type: ignore[assignment]
        elif op == "T*":
            tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            tm = tlm
        elif op == "Tj" or op == "TJ":
            record_show()
        elif op in ("'", '"'):
            tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            tm = tlm
            record_show()
        elif op == "Do":
            if last_name is not None:
                cx, cy = _apply(ctm, 0.5, 0.5)  # centre of the placed unit square
                elements.append(
                    LayoutElement(
                        "xobject",
                        last_name[1],
                        tok_end,
                        cx,
                        cy,
                        name=last_name[0],
                    )
                )

        nums = []
        last_name = None

    return elements


def find_text_objects(content: bytes) -> List[TextObject]:
    """Return the ``BT`` ... ``ET`` text objects in *content*, in stream order."""
    return [
        TextObject(e.start, e.end, e.font_size, e.text_length)
        for e in find_layout_elements(content)
        if e.kind == "text"
    ]


def find_xobject_invocations(content: bytes) -> List[Tuple[str, int, int]]:
    """Return ``(name, start, end)`` for each ``/Name Do`` in stream order.

    *name* keeps its leading slash; the span ``[start, end)`` covers the name
    operand through the ``Do`` operator, so it can be wrapped as marked content.
    The caller filters these to the names that are actually *image* XObjects
    (form XObjects share the ``Do`` operator).
    """
    return [
        (e.name, e.start, e.end)
        for e in find_layout_elements(content)
        if e.kind == "xobject" and e.name is not None
    ]


def assign_reading_order(elements: List[LayoutElement]) -> List[LayoutElement]:
    """Sort *elements* into reading order: top-to-bottom, then left-to-right.

    Elements whose baselines are within a small tolerance are treated as one
    line and ordered left-to-right; lines are stacked from the top of the page
    down.  This recovers the intended order even when the stream order differs.
    """
    if not elements:
        return []
    ordered = sorted(elements, key=lambda e: (-e.y, e.x))
    result: List[LayoutElement] = []
    line: List[LayoutElement] = [ordered[0]]
    for e in ordered[1:]:
        ref = line[0]
        tol = max(1.0, _LINE_TOL_RATIO * (ref.font_size or e.font_size or 10.0))
        if abs(e.y - ref.y) <= tol:
            line.append(e)
        else:
            result.extend(sorted(line, key=lambda el: el.x))
            line = [e]
    result.extend(sorted(line, key=lambda el: el.x))
    return result


def _same_paragraph(prev: LayoutElement, cur: LayoutElement) -> bool:
    """Whether *cur* continues the paragraph ended by *prev* (both body text)."""
    if prev.kind != "text" or cur.kind != "text":
        return False
    if prev.tag != "P" or cur.tag != "P":
        return False
    fp, fc = prev.font_size, cur.font_size
    if fp > 0 and fc > 0:
        ratio = fc / fp
        if not (_PARA_SIZE_MIN <= ratio <= _PARA_SIZE_MAX):
            return False
    fs = fc or fp or 10.0
    dy = prev.y - cur.y  # positive going down the page
    line_tol = _LINE_TOL_RATIO * fs
    if abs(dy) <= line_tol:
        return True  # same visual line (wrapped chunks)
    return line_tol < dy <= _PARA_GAP_RATIO * fs


def group_into_paragraphs(ordered: List[LayoutElement]) -> List[List[LayoutElement]]:
    """Group reading-ordered *ordered* elements into structure groups.

    Consecutive body-text (``/P``) elements that are close in size and vertical
    spacing collapse into one paragraph; headings and figures each form their
    own single-element group.  Each returned group becomes one structure element
    (a paragraph spans several marked-content sequences, one per line).
    """
    groups: List[List[LayoutElement]] = []
    current: List[LayoutElement] = []
    for e in ordered:
        if current and _same_paragraph(current[-1], e):
            current.append(e)
        else:
            if current:
                groups.append(current)
            current = [e]
    if current:
        groups.append(current)
    return groups


def choose_tags(objects: List[TextObject]) -> List[str]:
    """Pick a structure tag (``H1`` or ``P``) for each text object by font size.

    The body size is the font size carrying the most shown text; objects whose
    size is at least :data:`_HEADING_RATIO` times that are treated as headings.
    """
    weight: dict[float, int] = {}
    for obj in objects:
        if obj.max_font_size > 0:
            weight[obj.max_font_size] = (
                weight.get(obj.max_font_size, 0) + obj.text_length + 1
            )
    body = max(weight, key=lambda size: (weight[size], -size)) if weight else 0.0
    tags: List[str] = []
    for obj in objects:
        is_heading = body > 0 and obj.max_font_size >= body * _HEADING_RATIO
        tags.append("H1" if is_heading else "P")
    return tags


def build_tagged_content(
    content: bytes, marks: List[Tuple[int, int, str, int]]
) -> bytes:
    """Splice ``BDC``/``EMC`` around *marks* ``(start, end, tag, mcid)``.

    Insertions are applied from the highest offset down so earlier offsets stay
    valid; the original operator bytes are never rewritten.
    """
    out = bytearray(content)
    for start, end, tag, mcid in sorted(marks, key=lambda m: m[0], reverse=True):
        out[end:end] = b"\nEMC\n"
        prefix = b"\n/" + tag.encode("latin-1") + b" <</MCID "
        prefix += str(mcid).encode("ascii") + b">> BDC\n"
        out[start:start] = prefix
    return bytes(out)
