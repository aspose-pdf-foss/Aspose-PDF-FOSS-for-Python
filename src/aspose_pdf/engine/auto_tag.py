"""Heuristic auto-tagging of existing page content for PDF/UA structure.

Wraps the text and image content of a page in marked-content sequences so it can
be reflected in the structure tree -- turning untagged content into a real (if
coarse) tag tree instead of an empty catalog shell.  Text objects (``BT`` ...
``ET``) and image paints (``/Name Do``) are located together with their page
position, split into left-to-right column bands (so a two-column page is not
read straight across), sorted into reading order within each column
(top-to-bottom, then left-to-right) and grouped so consecutive body-text lines
collapse into a single paragraph (``/P``).  Headings are inferred from font size
relative to the page's dominant body size and ranked into levels (``/H1`` for
the largest tier, then ``/H2``, ``/H3``); each heading and each figure is its
own structure element.

This is a heuristic *aid*, not certified accessibility: reading order is derived
from geometry rather than semantics, column detection is a whitespace-gutter
heuristic (a banner spanning the columns may be mis-assigned), paragraph
grouping is proximity-based (it cannot see lists or tables), heading levels come
from font size alone, and images are described with a placeholder ``/Alt``.
Pages that already carry marked content are left untouched.

The rewrite is a pure byte splice -- ``BDC``/``EMC`` are inserted around the
existing operators without re-serializing them -- so the original content is
preserved exactly.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

__all__ = [
    "LayoutElement",
    "TextObject",
    "assign_list_depths",
    "assign_reading_order",
    "attach_image_bullets",
    "build_tagged_content",
    "choose_tags",
    "detect_columns",
    "detect_tables",
    "find_image_placements",
    "find_layout_elements",
    "find_mcids",
    "find_text_objects",
    "find_xobject_invocations",
    "find_xobject_placements",
    "group_into_paragraphs",
    "group_rows",
    "has_marked_content",
    "is_list_item",
    "list_marker",
]

# Whitespace and delimiter bytes that end a regular token (PDF 32000-1 §7.2).
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_ENDERS = _WS + _DELIM

_HEADING_RATIO = 1.4       # size >= this * body size is a heading
_HEADING_LEVEL_RATIO = 1.05  # heading sizes within this ratio share a level
_MAX_HEADING_LEVEL = 3     # deepest heading tier (/H1../H3)

# Paragraph-grouping heuristics (see :func:`group_into_paragraphs`).
_PARA_SIZE_MIN = 0.8      # min ratio of consecutive line font sizes to still group
_PARA_SIZE_MAX = 1.25     # max ratio ...
_LINE_TOL_RATIO = 0.35    # |Δbaseline| within this * font size == same line
_PARA_GAP_RATIO = 1.6     # baseline step up to this * font size stays in-paragraph

# Column-detection heuristics (see :func:`detect_columns`).
_COL_MIN_ELEMENTS = 4     # too few elements to infer columns reliably
_COL_GUTTER_EM = 3.0      # a column gutter is at least this * font size wide
_COL_GUTTER_MIN = 18.0    # ... and at least this many user units
_COL_OVERLAP_RATIO = 0.5  # column bands must share this fraction of their y-span
_COL_MIN_SPAN_EM = 0.5    # each band must span more than a single line vertically
_MAX_COL_DEPTH = 3        # recursion cap for nested vertical cuts
# When this fraction of lines has content on both sides of a candidate gutter,
# and the lines fill less than _COL_MIN_FILL of the band leading up to it, the
# gap is between a table's columns rather than between the page's.
_COL_PAIRED_ROW_RATIO = 0.8
# Deliberately low: a candidate gutter is already at least 3 em wide, and this
# asks whether the text before it is narrower than a third of the band. Prose
# fills more than that even with a ragged edge, so the benefit of the doubt
# goes to the column reading -- the one that was there before.
_COL_MIN_FILL = 0.3
# A line's width is estimated from the bytes it shows; 0.5 em per byte is the
# usual rough average for Latin text. It is only ever used to ask whether a
# line *crosses* a candidate gutter, so being approximate is fine -- the answer
# only changes for a line that ends within half an em of the gutter.
_WIDTH_PER_BYTE_EM = 0.5

# Table-detection heuristics (see :func:`detect_tables`).
_TABLE_MIN_ROWS = 2       # a table needs at least this many aligned rows
_TABLE_MIN_COLS = 2       # ... each with at least this many cells
_TABLE_COL_TOL_EM = 0.6   # cell x-alignment tolerance as a fraction of font size
_TABLE_COL_TOL_MIN = 4.0  # ... with this floor in user units
_TABLE_ANCHOR_ROWS = 3    # rows sampled to find the grid's full column set

# List-detection heuristics (see :func:`list_marker`).
_HEAD_CAP = 32            # bytes of leading shown text kept for marker sniffing
# Common bullet glyphs, including the WinAnsi bullet (byte 0x95) and dashes.
_BULLET_CHARS = set("•·◦‣▪●■\u2043-\u2013—*\x95")
# An ordered marker: an optional "(", then digits / a letter / a small roman
# numeral, then "." or ")" -- e.g. "1.", "(a)", "iv)".
_ORDERED_RE = re.compile(r"^\(?(?:[0-9]{1,3}|[ivxlcdmIVXLCDM]{1,5}|[A-Za-z])[.)]$")
# Two list markers within this much of each other sit at the same indent stop.
_LIST_INDENT_EM = 0.9
_LIST_INDENT_MIN = 6.0
_LIST_MAX_DEPTH = 4  # deeper nesting is flattened rather than tracked
# An image bullet is glyph-sized and sits just left of the line it marks.
_BULLET_MAX_EM = 1.5
_BULLET_GAP_EM = 2.5
_BULLET_BASELINE_EM = 0.4  # how far below the image the baseline may sit

Matrix = tuple[float, float, float, float, float, float]
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
    name: str | None = None  # xobject resource name, with leading slash
    tag: str | None = None
    alt: str | None = None
    text_head: str = ""  # leading shown text of the object, for list sniffing
    width: float = 0.0  # placed width of an image, in page units
    height: float = 0.0  # ... and its height
    column: int = 0  # table cell: index of the column anchor it sits on
    span: int = 1  # table cell: how many columns it covers
    depth: int = 0  # list item: nesting level, from its indentation
    bullet: LayoutElement | None = None  # the image standing in for a marker


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


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _resolve_scan_budget(
    limits: PdfLoadLimits | None,
    budget: _LoadBudget | None,
) -> _LoadBudget:
    """Return a validated budget for one content-scanning operation."""
    if budget is None:
        return _LoadBudget(_coerce_limits(limits))
    if not isinstance(budget, _LoadBudget):
        raise TypeError("budget must be a _LoadBudget instance or None")
    if limits is not None and limits != budget.limits:
        raise ValueError("limits must match budget.limits")
    return budget


def _tokens(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> Iterator[tuple[str | None, int, int]]:
    """Yield ``(token, start, end)`` for a content stream.

    ``token`` is the operator/operand/name text, or ``None`` for a literal or
    hex string (whose span is still reported so callers can measure it).
    Strings and comments are consumed so their bytes are never mistaken for
    operators (e.g. a ``(BT)`` literal is not a text object).
    """
    active_budget = _resolve_scan_budget(limits, budget)
    active_budget.check(
        len(content),
        "max_content_stream_bytes",
        "auto-tag content stream bytes",
    )

    token_count = 0
    container_stack: list[str] = []

    def count_token() -> None:
        nonlocal token_count
        token_count += 1
        active_budget.check(
            token_count,
            "max_content_tokens",
            "auto-tag content stream tokens",
        )

    def push_container(kind: str) -> None:
        depth = len(container_stack) + 1
        active_budget.check(
            depth,
            "max_container_items",
            "auto-tag content structure stack items",
        )
        active_budget.check(
            depth,
            "max_nesting_depth",
            "auto-tag content structure nesting",
        )
        container_stack.append(kind)

    def close_container(kind: str) -> None:
        if container_stack and container_stack[-1] == kind:
            container_stack.pop()

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
            active_budget.check(
                depth,
                "max_nesting_depth",
                "auto-tag literal string nesting",
            )
            i += 1
            while i < n and depth > 0:
                ch = content[i]
                if ch == 0x5C:  # backslash escape
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                    active_budget.check(
                        depth,
                        "max_nesting_depth",
                        "auto-tag literal string nesting",
                    )
                elif ch == 0x29:
                    depth -= 1
                i += 1
            count_token()
            yield (None, start, i)
            continue
        if c == 0x3C:  # '<'
            if i + 1 < n and content[i + 1] == 0x3C:  # '<<' dict open
                push_container("<<")
                count_token()
                yield ("<<", i, i + 2)
                i += 2
                continue
            start = i
            i += 1
            while i < n and content[i] != 0x3E:  # up to '>'
                i += 1
            i += 1
            count_token()
            yield (None, start, i)
            continue
        if c == 0x3E:  # '>'
            if i + 1 < n and content[i + 1] == 0x3E:
                close_container("<<")
                count_token()
                yield (">>", i, i + 2)
                i += 2
                continue
            i += 1
            continue
        if c in b"[]{}":
            punctuation = chr(c)
            if punctuation in ("[", "{"):
                push_container(punctuation)
            elif punctuation == "]":
                close_container("[")
            else:
                close_container("{")
            count_token()
            yield (punctuation, i, i + 1)
            i += 1
            continue
        if c == 0x2F:  # '/' name
            start = i
            i += 1
            while i < n and content[i] not in _ENDERS:
                i += 1
            count_token()
            yield (content[start:i].decode("latin-1"), start, i)
            continue
        start = i
        while i < n and content[i] not in _ENDERS:
            i += 1
        if i == start:  # defensive: never stall on a stray delimiter
            i += 1
            continue
        token = content[start:i].decode("latin-1")
        count_token()
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


def _to_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _unescape_literal(raw: bytes) -> str:
    """Decode a PDF literal-string body's leading bytes (escapes handled)."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n and len(out) < _HEAD_CAP * 2:
        b = raw[i]
        if b == 0x5C and i + 1 < n:  # backslash escape
            nxt = raw[i + 1]
            simple = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if 0x30 <= nxt <= 0x37:  # octal escape (1-3 digits)
                j, digits = i + 1, bytearray()
                while j < n and len(digits) < 3 and 0x30 <= raw[j] <= 0x37:
                    digits.append(raw[j])
                    j += 1
                out.append(int(digits.decode("ascii"), 8) & 0xFF)
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(b)
        i += 1
    return out.decode("latin-1", "replace")


def _decode_string_span(content: bytes, start: int, end: int) -> str:
    """Best-effort decode of a string operand span to text (for marker sniffing).

    Literals are unescaped and hex strings are decoded, both as Latin-1. This is
    approximate (composite-font codes are not glyph characters), which is fine:
    a non-matching decode simply yields no list marker.
    """
    if start >= end:
        return ""
    lead = content[start:start + 1]
    if lead == b"(":
        return _unescape_literal(content[start + 1:end - 1])
    if lead == b"<":
        hex_body = content[start + 1:end - 1].decode("latin-1").strip()
        try:
            return bytes.fromhex(hex_body).decode("latin-1", "replace")
        except ValueError:
            return ""
    return ""


def list_marker(head: str) -> str | None:
    """Classify a line's leading text as a list marker: ``"ul"``, ``"ol"`` or None.

    Unordered when it starts with a bullet/dash glyph standing alone or followed
    by whitespace; ordered when the first token is an optionally-parenthesised
    number, letter or small roman numeral closed by ``.`` or ``)``.
    """
    s = head.strip()
    if not s:
        return None
    if s[0] in _BULLET_CHARS:
        if len(s) == 1 or s[1].isspace():
            return "ul"
        return None
    token = s.split(None, 1)[0]
    if _ORDERED_RE.match(token):
        return "ol"
    return None


def is_list_item(group: list[LayoutElement]) -> bool:
    """Whether a paragraph *group* is a list item.

    Either a ``/P`` line whose text begins with a marker, or one an image
    bullet was attached to by :func:`attach_image_bullets`.
    """
    if not group:
        return False
    head = group[0]
    if head.kind != "text" or head.tag != "P":
        return False
    return head.bullet is not None or list_marker(head.text_head) is not None


def attach_image_bullets(flow: list[LayoutElement]) -> None:
    """Pair a small image with the text line it bullets, in place.

    A list whose markers are drawn rather than typed carries no marker *text*,
    so the leading-character test never fires and the items read as loose
    paragraphs with a picture between them. What identifies the image is its
    company: a glyph-sized figure immediately to the left of a line, on that
    line's own baseline. The line then knows its own marker, which becomes the
    item's ``/Lbl``.
    """
    figures = [e for e in flow if e.kind == "xobject" and e.tag == "Figure"]
    if not figures:
        return
    taken: set[int] = set()
    for text in flow:
        if text.kind != "text" or text.tag != "P" or text.bullet is not None:
            continue
        size = text.font_size or 10.0
        for bullet in figures:
            if id(bullet) in taken:
                continue
            if (
                bullet.width <= 0
                or bullet.width > _BULLET_MAX_EM * size
                or bullet.height > _BULLET_MAX_EM * size
            ):
                continue
            # A drawn bullet sits on the line's baseline, so the baseline falls
            # inside the image's vertical span (a descender's worth below it at
            # the most). Row grouping is no help here: the anchor recorded for
            # an image is its centre, half a bullet above where the text sits.
            bottom = bullet.y - bullet.height / 2.0
            top = bullet.y + bullet.height / 2.0
            if not bottom - _BULLET_BASELINE_EM * size <= text.y <= top:
                continue
            right_edge = bullet.x + bullet.width / 2.0
            if not right_edge <= text.x <= right_edge + _BULLET_GAP_EM * size:
                continue
            text.bullet = bullet
            taken.add(id(bullet))
            break


def assign_list_depths(items: list[list[LayoutElement]]) -> None:
    """Set each item's ``depth`` from how far its marker is indented.

    A nested list is not marked in the content stream -- it is drawn as items
    that start further right. Indents are clustered rather than measured
    against a fixed step, because the step varies by document and a sub-item's
    own continuation lines sit at yet another x.
    """
    stops: list[float] = []
    for item in items:
        if not item:
            continue
        head = item[0]
        tol = max(_LIST_INDENT_MIN, _LIST_INDENT_EM * (head.font_size or 10.0))
        for depth, stop in enumerate(stops):
            if abs(head.x - stop) <= tol:
                # Returning to a stop closes every level opened after it.
                del stops[depth + 1 :]
                break
        else:
            if stops and head.x < stops[-1]:
                # Further left than any known stop and not near one: treat it
                # as the outermost level rather than inventing a new one.
                depth = 0
                stops[:] = [head.x]
            else:
                stops.append(head.x)
                depth = len(stops) - 1
        for line in item:
            line.depth = min(depth, _LIST_MAX_DEPTH)


def has_marked_content(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> bool:
    """Return ``True`` if the stream already contains marked-content operators."""
    active_budget = _resolve_scan_budget(limits, budget)
    for token, _start, _end in _tokens(content, budget=active_budget):
        if token in ("BDC", "BMC", "DP", "MP"):
            return True
    return False


def find_layout_elements(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> list[LayoutElement]:
    """Locate positioned text objects and image paints in *content*, in stream order.

    Tracks the CTM (``q``/``Q``/``cm``) and text matrix (``Tm``/``Td``/``TD``/
    ``T*``) so each element carries a page-space anchor for reading-order
    sorting.  Text objects also carry their maximum font size and shown-text
    length; image paints carry the invoked resource name.
    """
    active_budget = _resolve_scan_budget(limits, budget)
    elements: list[LayoutElement] = []
    ctm: Matrix = _IDENTITY
    ctm_stack: list[Matrix] = []
    tm: Matrix = _IDENTITY
    tlm: Matrix = _IDENTITY
    leading = 0.0

    in_text = False
    start = 0
    max_size = 0.0
    text_length = 0
    text_head = ""
    anchor: tuple[float, float] | None = None

    nums: list[float] = []
    last_name: tuple[str, int] | None = None

    def record_show() -> None:
        nonlocal anchor
        if in_text and anchor is None:
            anchor = _apply(_mul(tm, ctm), 0.0, 0.0)

    for token, tok_start, tok_end in _tokens(content, budget=active_budget):
        if token is None:  # a string literal / hex string operand
            if in_text:
                text_length += tok_end - tok_start
                if len(text_head) < _HEAD_CAP:
                    seg = _decode_string_span(content, tok_start, tok_end)
                    text_head = (text_head + " " + seg if text_head else seg)[:_HEAD_CAP]
            continue
        if token in ("[", "]", "{", "}", "<<", ">>"):
            continue  # array / dict punctuation: not an operator, keep operands
        if token.startswith("/"):
            last_name = (token, tok_start)
            continue
        val = _to_float(token)
        if val is not None:
            active_budget.check(
                len(nums) + 1,
                "max_container_items",
                "auto-tag numeric operand buffer items",
            )
            nums.append(val)
            continue

        # A bare keyword: an operator. Dispatch, then clear pending operands.
        op = token
        if op == "q":
            stack_size = len(ctm_stack) + 1
            active_budget.check(
                stack_size,
                "max_container_items",
                "auto-tag graphics state stack items",
            )
            active_budget.check(
                stack_size,
                "max_nesting_depth",
                "auto-tag graphics state nesting",
            )
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
            text_head = ""
            anchor = None
            tm = tlm = _IDENTITY
        elif op == "ET":
            if in_text:
                ax, ay = anchor if anchor is not None else _apply(_mul(tm, ctm), 0.0, 0.0)
                active_budget.check(
                    len(elements) + 1,
                    "max_container_items",
                    "auto-tag layout results",
                )
                elements.append(
                    LayoutElement(
                        "text", start, tok_end, ax, ay, max_size, text_length,
                        text_head=text_head,
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
                # The CTM maps the unit square onto the page, so its
                # transformed edges are the placed image's size.
                x0, y0 = _apply(ctm, 0.0, 0.0)
                x1, _ = _apply(ctm, 1.0, 0.0)
                _, y1 = _apply(ctm, 0.0, 1.0)
                placed_w = math.hypot(x1 - x0, _apply(ctm, 1.0, 0.0)[1] - y0)
                placed_h = math.hypot(_apply(ctm, 0.0, 1.0)[0] - x0, y1 - y0)
                active_budget.check(
                    len(elements) + 1,
                    "max_container_items",
                    "auto-tag layout results",
                )
                elements.append(
                    LayoutElement(
                        "xobject",
                        last_name[1],
                        tok_end,
                        cx,
                        cy,
                        name=last_name[0],
                        width=placed_w,
                        height=placed_h,
                    )
                )

        nums = []
        last_name = None

    return elements


def find_image_placements(
    content: bytes,
    *,
    initial_ctm: Matrix | None = None,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> list[tuple[str, float, float]]:
    """Return ``(xobject_name, displayed_width, displayed_height)`` per ``Do``.

    The name keeps its leading slash. Sizes are in default user-space units
    (points): an XObject fills the unit square ``[0,1]²``, so its displayed axes
    are the lengths of the CTM's transformed ``(1,0)`` and ``(0,1)`` vectors.
    Form and image XObjects share ``Do``; the caller keeps only the image names.

    *initial_ctm* seeds the graphics state, which lets a caller measure a form
    XObject's own content in page space.
    """
    return [
        (name, (m[0] ** 2 + m[1] ** 2) ** 0.5, (m[2] ** 2 + m[3] ** 2) ** 0.5)
        for name, m in find_xobject_placements(
            content, initial_ctm=initial_ctm, limits=limits, budget=budget
        )
    ]


def find_xobject_placements(
    content: bytes,
    *,
    initial_ctm: Matrix | None = None,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> list[tuple[str, Matrix]]:
    """Return ``(xobject_name, ctm)`` for each ``Do``, in stream order.

    The full matrix — not just the axis lengths — is what a caller needs to
    descend into a form XObject and keep measuring in page space.
    """
    active_budget = _resolve_scan_budget(limits, budget)
    placements: list[tuple[str, Matrix]] = []
    ctm: Matrix = initial_ctm or _IDENTITY
    ctm_stack: list[Matrix] = []
    nums: list[float] = []
    last_name: str | None = None

    for token, _tok_start, _tok_end in _tokens(content, budget=active_budget):
        if token is None or token in ("[", "]", "{", "}", "<<", ">>"):
            continue
        if token.startswith("/"):
            last_name = token
            continue
        val = _to_float(token)
        if val is not None:
            active_budget.check(
                len(nums) + 1,
                "max_container_items",
                "image placement numeric operand buffer items",
            )
            nums.append(val)
            continue
        op = token
        if op == "q":
            stack_size = len(ctm_stack) + 1
            active_budget.check(
                stack_size,
                "max_container_items",
                "image placement graphics state stack items",
            )
            active_budget.check(
                stack_size,
                "max_nesting_depth",
                "image placement graphics state nesting",
            )
            ctm_stack.append(ctm)
        elif op == "Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
        elif op == "cm":
            if len(nums) >= 6:
                ctm = _mul(tuple(nums[-6:]), ctm)  # type: ignore[arg-type]
        elif op == "Do" and last_name is not None:
            active_budget.check(
                len(placements) + 1,
                "max_container_items",
                "image placement results",
            )
            placements.append((last_name, ctm))
        nums = []
        last_name = None
    return placements


def find_mcids(
    content: bytes,
    *,
    named_properties: Mapping[str, int] | None = None,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> set[int]:
    """Return the set of marked-content ``/MCID`` integers declared in *content*.

    Scans the inline property dictionary of each ``BDC``/``DP`` operator for an
    ``/MCID`` integer. The tokenizer skips strings, comments and inline-image
    data, so operators that merely *look* like marked content inside those are
    ignored. ``named_properties`` maps resource names from a page's
    ``/Properties`` dictionary to their resolved MCIDs, allowing forms such as
    ``/Tag /P1 BDC`` to be included as well.
    """
    active_budget = _resolve_scan_budget(limits, budget)
    mcids: set[int] = set()
    depth = 0
    current: int | None = None
    expect_value = False
    previous_name: str | None = None
    last_name: str | None = None
    inline_properties = False
    for token, _tok_start, _tok_end in _tokens(content, budget=active_budget):
        if token is None:
            if depth == 0:
                previous_name = None
                last_name = None
                inline_properties = False
            continue
        if token == "<<":
            depth += 1
            if depth == 1:
                current = None  # a fresh top-level BDC/DP property dict
                expect_value = False
                inline_properties = True
            continue
        if token == ">>":
            if depth > 0:
                depth -= 1
            continue
        if depth > 0:
            if expect_value:
                value = _to_float(token)
                if value is not None:
                    current = int(value)
                expect_value = False
            elif token == "/MCID":
                expect_value = True
            continue
        if token in ("BDC", "DP"):
            resolved = current
            if (
                resolved is None
                and not inline_properties
                and previous_name is not None
                and last_name is not None
                and named_properties
            ):
                resolved = named_properties.get(last_name)
                if resolved is None:
                    resolved = named_properties.get(f"/{last_name}")
            if resolved is not None:
                if resolved not in mcids:
                    active_budget.check(
                        len(mcids) + 1,
                        "max_container_items",
                        "marked-content ID results",
                    )
                mcids.add(int(resolved))
            current = None
            previous_name = None
            last_name = None
            inline_properties = False
        elif token.startswith("/"):
            inline_properties = False
            previous_name = last_name
            last_name = token.lstrip("/")
        else:
            inline_properties = False
            previous_name = None
            last_name = None
    return mcids


def find_text_objects(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> list[TextObject]:
    """Return the ``BT`` ... ``ET`` text objects in *content*, in stream order."""
    return [
        TextObject(e.start, e.end, e.font_size, e.text_length)
        for e in find_layout_elements(content, limits=limits, budget=budget)
        if e.kind == "text"
    ]


def find_xobject_invocations(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> list[tuple[str, int, int]]:
    """Return ``(name, start, end)`` for each ``/Name Do`` in stream order.

    *name* keeps its leading slash; the span ``[start, end)`` covers the name
    operand through the ``Do`` operator, so it can be wrapped as marked content.
    The caller filters these to the names that are actually *image* XObjects
    (form XObjects share the ``Do`` operator).
    """
    return [
        (e.name, e.start, e.end)
        for e in find_layout_elements(content, limits=limits, budget=budget)
        if e.kind == "xobject" and e.name is not None
    ]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _side_by_side(
    left: list[LayoutElement], right: list[LayoutElement], med_fs: float
) -> bool:
    """Whether two x-separated bands truly sit side by side (vertical overlap)."""
    ly0, ly1 = min(e.y for e in left), max(e.y for e in left)
    ry0, ry1 = min(e.y for e in right), max(e.y for e in right)
    overlap = min(ly1, ry1) - max(ly0, ry0)
    if overlap <= 0:
        return False
    smaller = min(ly1 - ly0, ry1 - ry0)
    if smaller < _COL_MIN_SPAN_EM * med_fs:
        return False  # a single-row pair is a line, not two columns
    return overlap >= _COL_OVERLAP_RATIO * smaller


def _estimated_right_edge(element: LayoutElement) -> float:
    """Where a text element probably ends, from what it shows and at what size."""
    if element.kind != "text" or element.text_length <= 0:
        return element.x
    size = element.font_size if element.font_size > 0 else 10.0
    return element.x + element.text_length * size * _WIDTH_PER_BYTE_EM


def _gutter_is_clear(elements: list[LayoutElement], at: float) -> bool:
    """Whether no line runs across *at*.

    A gutter is *whitespace*: content on one side, content on the other, and
    nothing spanning between. Judging that from anchors alone cannot tell a
    two-column page from a table with widely spaced cells, because both leave a
    gap between anchors -- but a table sits under full-width prose that runs
    straight through the supposed gutter, and two columns never do.
    """
    for element in elements:
        if element.x < at < _estimated_right_edge(element):
            return False
    return True


def _split_columns(
    elements: list[LayoutElement], med_fs: float, depth: int
) -> list[list[LayoutElement]]:
    """Recursively cut *elements* at whitespace gutters into column bands."""
    if depth >= _MAX_COL_DEPTH or len(elements) < _COL_MIN_ELEMENTS:
        return [elements]
    xs = sorted(e.x for e in elements)
    best_gap, best_at = 0.0, None
    for lo, hi in itertools.pairwise(xs):
        if hi - lo > best_gap:
            best_gap, best_at = hi - lo, (lo + hi) / 2.0
    if best_at is None or best_gap < max(_COL_GUTTER_MIN, _COL_GUTTER_EM * med_fs):
        return [elements]
    if not _gutter_is_clear(elements, best_at):
        return [elements]
    left = [e for e in elements if e.x < best_at]
    right = [e for e in elements if e.x >= best_at]
    if len(left) < 2 or len(right) < 2 or not _side_by_side(left, right, med_fs):
        return [elements]
    if _is_table_gap(left, right, best_at):
        return [elements]
    return (
        _split_columns(left, med_fs, depth + 1)
        + _split_columns(right, med_fs, depth + 1)
    )


def _is_table_gap(
    left: list[LayoutElement], right: list[LayoutElement], at: float
) -> bool:
    """Whether a gap separates a *table's* columns rather than the page's.

    Both look the same from the anchors alone, and reading a table as columns
    turns its rows inside out -- the whole first column, then the whole second.
    Two things tell them apart:

    *How much of the band the text fills.* A column of prose runs to the gutter,
    ragged edge and all; a table cell is a word or a number with the rest of the
    column empty behind it.

    *Whether the rows pair up.* Two columns are independent -- a line on the
    left has no counterpart beside it except by coincidence -- while a table's
    rows reach across every gap between its columns.

    Both are required, and a page whose elements carry no width to measure is
    left as columns: no evidence is a reason to keep the previous reading, not
    to overturn it.
    """
    widths = [
        _estimated_right_edge(e) - e.x
        for e in left
        if e.kind == "text" and e.text_length > 0
    ]
    if not widths:
        return False
    available = at - _median([e.x for e in left])
    if available <= 0 or _median(widths) / available > _COL_MIN_FILL:
        return False

    lines = group_rows(left + right)
    if len(lines) < _TABLE_MIN_ROWS:
        return False
    lefts = {id(e) for e in left}
    paired = sum(1 for line in lines if len({id(e) in lefts for e in line}) == 2)
    return paired >= _COL_PAIRED_ROW_RATIO * len(lines)


def detect_columns(elements: list[LayoutElement]) -> list[list[LayoutElement]]:
    """Partition *elements* into left-to-right column bands (one or more).

    A band boundary is a vertical whitespace gutter -- a horizontal gap between
    element x-anchors that is wide relative to the text size and straddled by
    content that coexists vertically (so a genuine two-column split, not a
    ragged margin or an indented block).  Pages without such a gutter yield a
    single band, so single-column content is ordered exactly as before.  The
    caller applies :func:`assign_reading_order` within each band, so columns are
    read fully top-to-bottom before moving right.
    """
    if len(elements) < _COL_MIN_ELEMENTS:
        return [list(elements)] if elements else []
    sizes = [e.font_size for e in elements if e.font_size > 0]
    med_fs = _median(sizes) or 10.0
    return _split_columns(list(elements), med_fs, 0)


def group_rows(elements: list[LayoutElement]) -> list[list[LayoutElement]]:
    """Group *elements* into rows of one visual line each, in reading order.

    Elements whose baselines are within a small tolerance form one row, ordered
    left-to-right; rows are stacked from the top of the page down.  This is the
    row model shared by reading-order flattening and table detection.
    """
    if not elements:
        return []
    ordered = sorted(elements, key=lambda e: (-e.y, e.x))
    rows: list[list[LayoutElement]] = []
    line: list[LayoutElement] = [ordered[0]]
    for e in ordered[1:]:
        ref = line[0]
        tol = max(1.0, _LINE_TOL_RATIO * (ref.font_size or e.font_size or 10.0))
        if abs(e.y - ref.y) <= tol:
            line.append(e)
        else:
            rows.append(sorted(line, key=lambda el: el.x))
            line = [e]
    rows.append(sorted(line, key=lambda el: el.x))
    return rows


def assign_reading_order(elements: list[LayoutElement]) -> list[LayoutElement]:
    """Sort *elements* into reading order: top-to-bottom, then left-to-right.

    Elements whose baselines are within a small tolerance are treated as one
    line and ordered left-to-right; lines are stacked from the top of the page
    down.  This recovers the intended order even when the stream order differs.
    """
    result: list[LayoutElement] = []
    for row in group_rows(elements):
        result.extend(row)
    return result


def _row_tol(row: list[LayoutElement]) -> float:
    """Column x-alignment tolerance for a table *row*, scaled by its font size."""
    fs = _median([e.font_size for e in row if e.font_size > 0]) or 10.0
    return max(_TABLE_COL_TOL_MIN, _TABLE_COL_TOL_EM * fs)


def _assign_columns(
    row: list[LayoutElement], anchors: list[float], tol: float
) -> list[int] | None:
    """Map each cell in *row* to a column anchor, or ``None`` if it does not fit.

    A row need not fill every column: a table with a blank cell simply has no
    element on that anchor. What it may not do is put two cells on one anchor,
    or a cell on none -- either means the row is not part of this grid.
    """
    used: set[int] = set()
    columns: list[int] = []
    for element in row:
        best, best_delta = None, tol
        for index, anchor in enumerate(anchors):
            delta = abs(element.x - anchor)
            if delta <= best_delta:
                best, best_delta = index, delta
        if best is None or best in used:
            return None
        used.add(best)
        columns.append(best)
    return columns


def _column_span(
    element: LayoutElement, column: int, anchors: list[float], tol: float
) -> int:
    """How many columns a cell covers, from where its text reaches.

    A merged cell starts on one anchor and runs past the next; an ordinary cell
    stops short of it. The estimate is the same rough one the gutter check
    uses, so the threshold is the alignment tolerance rather than an exact edge.
    """
    right = _estimated_right_edge(element)
    span = 1
    for index in range(column + 1, len(anchors)):
        if right > anchors[index] + tol:
            span += 1
        else:
            break
    return span


def _table_anchors(rows: list[list[LayoutElement]], start: int) -> list[float] | None:
    """Column anchors for a grid beginning at *start*, or ``None``.

    The widest of the first few rows defines the columns: a header row that
    merges two cells, or a first row with a blank, would otherwise fix the grid
    at too few columns and push every fuller row out of it.
    """
    window = rows[start : start + _TABLE_ANCHOR_ROWS]
    widest = max(window, key=len)
    if len(widest) < _TABLE_MIN_COLS:
        return None
    return [e.x for e in widest]


def _table_run(rows: list[list[LayoutElement]], start: int) -> list[list[LayoutElement]] | None:
    """Maximal grid of aligned rows starting at *start*, or ``None``.

    A grid is two or more consecutive rows whose cells all land on a shared set
    of column anchors. Rows may leave columns empty and may merge across them:
    each kept cell records the column it sits on and how many it covers, so the
    caller can emit the blanks and the spans it cannot see from the row alone.
    """
    # A table has to *begin* on a row that shows the grid. A single-cell row
    # before one is far more often the sentence introducing the table, and no
    # geometry distinguishes it from a merged title row.
    if len(rows[start]) < _TABLE_MIN_COLS:
        return None
    anchors = _table_anchors(rows, start)
    if anchors is None:
        return None
    tol = _row_tol(max(rows[start : start + _TABLE_ANCHOR_ROWS], key=len))
    run: list[list[LayoutElement]] = []
    j = start
    while j < len(rows):
        columns = _assign_columns(rows[j], anchors, tol)
        if columns is None:
            break
        for element, column in zip(rows[j], columns):
            element.column = column
            element.span = _column_span(element, column, anchors, tol)
        run.append(rows[j])
        j += 1
    # Trailing rows that show only one cell are far more often the prose under
    # a table than its last row, so they are handed back to the flow.
    while run and len(run[-1]) < _TABLE_MIN_COLS:
        run.pop()
    if len(run) < _TABLE_MIN_ROWS:
        return None
    # Allowing empty cells means a run of ordinary lines all starting at the
    # left margin would otherwise read as a one-column table with blanks. A
    # table has to *show* its grid: most of its rows, and at least two, span
    # more than one column.
    full = sum(1 for row in run if len(row) >= _TABLE_MIN_COLS)
    if full < _TABLE_MIN_ROWS or full * 2 < len(run):
        return None
    return run


def detect_tables(
    rows: list[list[LayoutElement]],
) -> list[tuple[str, list[list[LayoutElement]]]]:
    """Segment *rows* into ``("table", grid_rows)`` and ``("flow", rows)`` runs.

    Consecutive rows forming a regular aligned grid (see :func:`_table_run`)
    become a table segment; everything else is coalesced into flow segments for
    ordinary paragraph/list handling.
    """
    segments: list[tuple[str, list[list[LayoutElement]]]] = []
    flow: list[list[LayoutElement]] = []

    def flush_flow() -> None:
        if flow:
            segments.append(("flow", list(flow)))
            flow.clear()

    i, n = 0, len(rows)
    while i < n:
        grid = _table_run(rows, i)
        if grid is not None:
            flush_flow()
            segments.append(("table", grid))
            i += len(grid)
        else:
            flow.append(rows[i])
            i += 1
    flush_flow()
    return segments


def _same_paragraph(prev: LayoutElement, cur: LayoutElement) -> bool:
    """Whether *cur* continues the paragraph ended by *prev* (both body text)."""
    if prev.kind != "text" or cur.kind != "text":
        return False
    if prev.tag != "P" or cur.tag != "P":
        return False
    if list_marker(cur.text_head) is not None or cur.bullet is not None:
        return False  # a new marker begins a new item (its own group)
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


def group_into_paragraphs(ordered: list[LayoutElement]) -> list[list[LayoutElement]]:
    """Group reading-ordered *ordered* elements into structure groups.

    Consecutive body-text (``/P``) elements that are close in size and vertical
    spacing collapse into one paragraph; headings and figures each form their
    own single-element group.  Each returned group becomes one structure element
    (a paragraph spans several marked-content sequences, one per line).
    """
    groups: list[list[LayoutElement]] = []
    current: list[LayoutElement] = []
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


def _heading_levels(sizes_desc: list[float]) -> dict[float, str]:
    """Map heading font sizes (largest first) to ``H1``/``H2``/``H3`` tiers.

    Sizes within :data:`_HEADING_LEVEL_RATIO` of each other share a tier; the
    next distinctly smaller size drops a level, clamped at
    :data:`_MAX_HEADING_LEVEL`.
    """
    levels: dict[float, str] = {}
    level = 0
    prev: float | None = None
    for size in sizes_desc:
        if prev is None or prev / size > _HEADING_LEVEL_RATIO:
            level += 1
            prev = size
        levels[size] = "H" + str(min(level, _MAX_HEADING_LEVEL))
    return levels


def choose_tags(objects: list[TextObject]) -> list[str]:
    """Pick a structure tag (``H1``/``H2``/``H3`` or ``P``) per text object.

    The body size is the font size carrying the most shown text; objects whose
    size is at least :data:`_HEADING_RATIO` times that are headings, ranked into
    levels by size (the largest tier is ``H1``, then ``H2``, ``H3``).
    """
    weight: dict[float, int] = {}
    for obj in objects:
        if obj.max_font_size > 0:
            weight[obj.max_font_size] = (
                weight.get(obj.max_font_size, 0) + obj.text_length + 1
            )
    if not weight:
        return ["P" for _ in objects]
    body = max(weight, key=lambda size: (weight[size], -size))
    heading_sizes = sorted(
        (s for s in weight if s >= body * _HEADING_RATIO), reverse=True
    )
    levels = _heading_levels(heading_sizes)
    return [
        levels.get(obj.max_font_size, "P") if obj.max_font_size > 0 else "P"
        for obj in objects
    ]


def build_tagged_content(
    content: bytes, marks: list[tuple[int, int, str, int]]
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
