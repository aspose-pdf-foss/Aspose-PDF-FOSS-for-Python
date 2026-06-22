"""Synthesise variable-text appearance streams for AcroForm fields.

When a form field's value (``/V``) changes, its on-page appearance (``/AP /N``)
must be regenerated for viewers that do not honour the AcroForm
``/NeedAppearances`` flag (and for flattening). This module builds the content
stream for text and choice fields from the field value and its default
appearance string (``/DA``).

It is pure (no COS / engine imports): the caller resolves the font object and
wraps the returned bytes in a form XObject. Content is emitted in the widget's
local coordinate space (origin lower-left, spanning ``(0, 0)`` to ``(w, h)``).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# Standard-14 Helvetica is ~0.5em average, but this engine's width table is a
# flat 600/1000 unit advance, so mirror that for consistent centring estimates.
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
    line: str, width: float, font_size: float, quadding: int, padding: float
) -> float:
    """Left-edge x for *line* honouring the quadding (0 left, 1 centre, 2 right)."""
    if quadding not in (1, 2):
        return padding
    est_width = len(line) * _CHAR_WIDTH_EM * font_size
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
) -> bytes:
    """Build a ``/Tx``-marked variable-text appearance content stream.

    Multiline fields break on explicit newlines in *value* (no automatic word
    wrapping); single-line fields collapse newlines and are vertically centred.
    """
    text = value if value is not None else ""
    if multiline:
        lines = _split_lines(text)
    else:
        lines = [text.replace("\r", " ").replace("\n", " ")]

    fs = font_size if font_size > 0 else auto_font_size(height, multiline=multiline)
    leading = fs * 1.15

    body = ["/Tx BMC", "q", "BT", f"/{font_name} {_fmt(fs)} Tf", color_op]
    if multiline:
        ly = height - padding - fs
    else:
        ly = max(padding, (height - fs) / 2.0 + fs * 0.2)

    for line in lines:
        tx = _quad_x(line, width, fs, quadding, padding)
        body.append(f"1 0 0 1 {_fmt(tx)} {_fmt(ly)} Tm")
        body.append(f"{_pdf_literal(line)} Tj")
        ly -= leading

    body += ["ET", "Q", "EMC"]
    return ("\n".join(body) + "\n").encode("latin-1", "replace")
