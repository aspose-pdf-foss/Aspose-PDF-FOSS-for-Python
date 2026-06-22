"""Heuristic auto-tagging of existing page content for PDF/UA structure.

Wraps each text object (``BT`` ... ``ET``) in a page content stream as a
marked-content sequence so it can be reflected in the structure tree -- turning
untagged content into a real (if coarse) tag tree instead of an empty catalog
shell.  Headings are inferred from font size relative to the page's dominant
body size.

This is a heuristic *aid*, not certified accessibility: it groups by the
document's own text objects (one structure element per ``BT``/``ET``), does not
describe images, and does not infer fine-grained reading order.  Pages that
already carry marked content are left untouched.

The rewrite is a pure byte splice -- ``BDC``/``EMC`` are inserted around the
existing operators without re-serializing them -- so the original content is
preserved exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

__all__ = [
    "TextObject",
    "find_text_objects",
    "find_xobject_invocations",
    "has_marked_content",
    "choose_tags",
    "build_tagged_content",
]

# Whitespace and delimiter bytes that end a regular token (PDF 32000-1 §7.2).
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_ENDERS = _WS + _DELIM

_HEADING_RATIO = 1.4


@dataclass
class TextObject:
    """A ``BT`` ... ``ET`` text object located in a content stream."""

    start: int  # byte offset of 'B' in 'BT'
    end: int  # byte offset just past 'T' in 'ET'
    max_font_size: float
    text_length: int  # total bytes of strings shown (a body-vs-heading weight)


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


def has_marked_content(content: bytes) -> bool:
    """Return ``True`` if the stream already contains marked-content operators."""
    for token, _start, _end in _tokens(content):
        if token in ("BDC", "BMC", "DP", "MP"):
            return True
    return False


def find_text_objects(content: bytes) -> List[TextObject]:
    """Return the ``BT`` ... ``ET`` text objects in *content*, in stream order."""
    objects: List[TextObject] = []
    start: Optional[int] = None
    max_size = 0.0
    text_length = 0
    last_number: Optional[float] = None
    for token, tok_start, tok_end in _tokens(content):
        if token is None:  # a string literal/hex string
            if start is not None:
                text_length += tok_end - tok_start
            continue
        if token == "BT":
            start = tok_start
            max_size = 0.0
            text_length = 0
        elif token == "ET":
            if start is not None:
                objects.append(TextObject(start, tok_end, max_size, text_length))
                start = None
        elif token == "Tf":
            if last_number is not None:
                max_size = max(max_size, abs(last_number))
        elif _is_number(token):
            last_number = float(token)
    return objects


def find_xobject_invocations(content: bytes) -> List[Tuple[str, int, int]]:
    """Return ``(name, start, end)`` for each ``/Name Do`` in stream order.

    *name* keeps its leading slash; the span ``[start, end)`` covers the name
    operand through the ``Do`` operator, so it can be wrapped as marked content.
    The caller filters these to the names that are actually *image* XObjects
    (form XObjects share the ``Do`` operator).
    """
    results: List[Tuple[str, int, int]] = []
    last_name: Optional[Tuple[str, int]] = None
    for token, start, end in _tokens(content):
        if token is None:
            continue
        if token == "Do":
            if last_name is not None:
                results.append((last_name[0], last_name[1], end))
        elif token.startswith("/"):
            last_name = (token, start)
    return results


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
