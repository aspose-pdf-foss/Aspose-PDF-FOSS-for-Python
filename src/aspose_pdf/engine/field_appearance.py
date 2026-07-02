"""Synthesise variable-text appearance streams for AcroForm fields.

When a form field's value (``/V``) changes, its on-page appearance (``/AP /N``)
must be regenerated for viewers that do not honour the AcroForm
``/NeedAppearances`` flag (and for flattening). This module builds the content
stream for text and choice fields from the field value and its default
appearance string (``/DA``).

It is pure (no COS / engine imports): the caller resolves the font object and
wraps the returned bytes in a form XObject. Content is emitted in the widget's
local coordinate space (origin lower-left, spanning ``(0, 0)`` to ``(w, h)``).

Text is measured with an optional ``width_fn`` (``code -> advance`` in
1000-unit glyph space, resolved by the caller from the font's ``/Widths`` or a
bundled substitute — see ``text_metrics.py``); without one, a flat 0.6 em
estimate is used.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

# code (single-byte, cp1252 domain) -> advance width in 1000-unit glyph space.
WidthFn = Callable[[int], float]

# Fallback estimate when no glyph metrics are available: Standard-14 Helvetica
# is ~0.5em average; 0.6 keeps a safety margin so estimates rarely overflow.
_CHAR_WIDTH_EM = 0.6


def _fmt(value: float) -> str:
    """Format a coordinate compactly (trim trailing zeros, avoid ``-0``)."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def parse_default_appearance(da: Optional[str]) -> Tuple[Optional[str], float, str]:
    """Parse a ``/DA`` string into ``(font_name, font_size, fill_colour_op)``.

    ``font_name`` is the resource name without the leading slash (or ``None`` when
    the string has no ``Tf``); ``font_size`` is ``0.0`` for auto-size; the colour
    operator defaults to ``"0 g"`` (black) when ``/DA`` sets none.
    """
    font_name: Optional[str] = None
    font_size = 0.0
    color = "0 g"
    if not da:
        return font_name, font_size, color

    operands: List[str] = []
    for token in str(da).replace("\n", " ").replace("\r", " ").split():
        if token == "Tf":
            if len(operands) >= 2:
                name_tok = operands[-2]
                if name_tok.startswith("/"):
                    font_name = name_tok[1:]
                if _is_number(operands[-1]):
                    font_size = float(operands[-1])
            operands = []
        elif token in ("g", "rg", "k"):
            need = {"g": 1, "rg": 3, "k": 4}[token]
            if len(operands) >= need and all(_is_number(o) for o in operands[-need:]):
                color = " ".join(operands[-need:]) + " " + token
            operands = []
        elif token in ("G", "RG", "K", "cs", "CS", "sc", "scn"):
            operands = []  # stroke / colour-space operators: reset, ignore
        else:
            operands.append(token)
    return font_name, font_size, color


def auto_font_size(height: float, *, multiline: bool, padding: float = 2.0) -> float:
    """Pick a font size for an auto-sized (``/DA`` size 0) field."""
    if multiline:
        return 10.0
    return max(4.0, min(12.0, height - 2.0 * padding))


def _split_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _char_code(ch: str) -> int:
    """The single-byte code *ch* is emitted as (mirrors ``_pdf_literal``)."""
    code = ord(ch)
    return code if code < 256 else 0x3F  # non-Latin-1 becomes "?"


def _text_width(
    text: str, font_size: float, width_fn: Optional[WidthFn] = None
) -> float:
    """Measure *text* with *width_fn* glyph metrics (flat estimate without)."""
    if width_fn is None:
        return len(text) * _CHAR_WIDTH_EM * font_size
    return sum(width_fn(_char_code(ch)) for ch in text) / 1000.0 * font_size


def _wrap_text(
    text: str,
    max_width: float,
    font_size: float,
    width_fn: Optional[WidthFn] = None,
) -> List[str]:
    """Greedily wrap *text* into lines no wider than *max_width*.

    Explicit newlines are honoured as hard paragraph breaks; within a paragraph,
    words are packed greedily and a single word too long for the line is
    hard-broken so it never overflows. Widths come from *width_fn* glyph metrics
    when given (falling back to the flat ``_CHAR_WIDTH_EM`` estimate), matching
    the quadding origin calculation. At least one (possibly empty) line is
    always returned.
    """
    if max_width <= 0 or font_size <= 0:
        return _split_lines(text)

    def measure(s: str) -> float:
        return _text_width(s, font_size, width_fn)

    lines: List[str] = []
    for paragraph in _split_lines(text):
        current = ""
        for word in paragraph.split():
            while word and measure(word) > max_width:  # word longer than a line
                if current:
                    lines.append(current)
                    current = ""
                # Longest prefix that fits (always >= 1 char so we progress).
                k = 1
                while k < len(word) and measure(word[: k + 1]) <= max_width:
                    k += 1
                lines.append(word[:k])
                word = word[k:]
            if not word:
                continue
            candidate = word if current == "" else current + " " + word
            if measure(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)  # flush the paragraph's trailing line (may be "")
    return lines or [""]


def _pdf_literal(text: str) -> str:
    """Escape *text* as a PDF literal string ``(...)`` (Latin-1 byte domain)."""
    out = ["("]
    for ch in text:
        if ch in ("(", ")", "\\"):
            out.append("\\" + ch)
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) > 126:
            code = ord(ch)
            out.append(f"\\{code & 0xFF:03o}" if code < 256 else "?")
        else:
            out.append(ch)
    out.append(")")
    return "".join(out)


def _quad_x(
    line: str,
    width: float,
    font_size: float,
    quadding: int,
    padding: float,
    width_fn: Optional[WidthFn] = None,
) -> float:
    """Left-edge x for *line* honouring the quadding (0 left, 1 centre, 2 right)."""
    if quadding not in (1, 2):
        return padding
    est_width = _text_width(line, font_size, width_fn)
    if quadding == 1:  # centred
        return max(padding, (width - est_width) / 2.0)
    return max(padding, width - padding - est_width)  # right-aligned


def build_text_appearance(
    value: str,
    width: float,
    height: float,
    *,
    font_name: str,
    font_size: float,
    color_op: str = "0 g",
    quadding: int = 0,
    multiline: bool = False,
    padding: float = 2.0,
    width_fn: Optional[WidthFn] = None,
) -> bytes:
    """Build a ``/Tx``-marked variable-text appearance content stream.

    Multiline fields wrap *value* to the field width (greedy word wrap, honouring
    explicit newlines as hard breaks); single-line fields collapse newlines and
    are vertically centred. *width_fn* supplies glyph-metric advances for the
    wrap and quadding maths (flat estimate without).
    """
    text = value if value is not None else ""
    fs = font_size if font_size > 0 else auto_font_size(height, multiline=multiline)
    leading = fs * 1.15

    if multiline:
        lines = _wrap_text(text, width - 2.0 * padding, fs, width_fn)
    else:
        lines = [text.replace("\r", " ").replace("\n", " ")]

    body = ["/Tx BMC", "q", "BT", f"/{font_name} {_fmt(fs)} Tf", color_op]
    if multiline:
        ly = height - padding - fs
    else:
        ly = max(padding, (height - fs) / 2.0 + fs * 0.2)

    for line in lines:
        tx = _quad_x(line, width, fs, quadding, padding, width_fn)
        body.append(f"1 0 0 1 {_fmt(tx)} {_fmt(ly)} Tm")
        body.append(f"{_pdf_literal(line)} Tj")
        ly -= leading

    body += ["ET", "Q", "EMC"]
    return ("\n".join(body) + "\n").encode("latin-1", "replace")
