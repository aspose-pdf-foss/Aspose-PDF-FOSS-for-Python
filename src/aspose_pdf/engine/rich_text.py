"""Parse and render the XHTML rich text of ``/RC`` (annotations) and ``/RV``.

Rich text is a small XHTML fragment (``<body>`` with ``<p>``/``<span>``/
``<b>``/``<i>`` and ``style`` attributes). We parse it into styled runs and lay
them out into an appearance content stream, honouring per-run font size, colour,
bold/italic and paragraph text alignment. Fonts are the Standard-14 Helvetica
family (regular/bold/oblique/bold-oblique), measured with the bundled
metric-compatible substitute so wrapping and alignment use real advances.

Parsing uses :mod:`html.parser` (no DTD/entity expansion, so no XXE risk) and is
forgiving of the not-quite-XML markup writers emit. Unsupported tags degrade to
their text content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from .field_appearance import _fmt, _pdf_literal

# (bold, italic) -> (resource name, Standard-14 BaseFont).
_FONT_TABLE = {
    (False, False): ("Helv", "Helvetica"),
    (True, False): ("HeBo", "Helvetica-Bold"),
    (False, True): ("HeOb", "Helvetica-Oblique"),
    (True, True): ("HeBO", "Helvetica-BoldOblique"),
}

_NAMED_COLORS = {
    "black": "0 g",
    "white": "1 1 1 rg",
    "red": "1 0 0 rg",
    "green": "0 0.5 0 rg",
    "blue": "0 0 1 rg",
    "gray": "0.5 g",
    "grey": "0.5 g",
    "yellow": "1 1 0 rg",
}

_LINE_LEADING = 1.16   # baseline-to-baseline as a multiple of the line font size
_CHAR_WIDTH_EM = 0.6   # flat fallback advance when metrics are unavailable


@dataclass(frozen=True)
class RichStyle:
    """The resolved style of a text run."""

    size: float = 12.0
    color: str = "0 g"   # a nonstroking-colour operator, e.g. "1 0 0 rg"
    bold: bool = False
    italic: bool = False
    align: int = 0        # 0 left, 1 centre, 2 right


@dataclass
class RichRun:
    text: str
    style: RichStyle


def _font_spec(base: str) -> Dict[str, str]:
    return {"Subtype": "Type1", "BaseFont": base, "Encoding": "WinAnsiEncoding"}


def _parse_color(value: str) -> Optional[str]:
    """Convert a CSS colour to a nonstroking-colour operator, or ``None``."""
    v = value.strip().lower()
    if v.startswith("#"):
        hexv = v[1:]
        if len(hexv) == 3:
            hexv = "".join(c * 2 for c in hexv)
        if len(hexv) == 6:
            try:
                r, g, b = (int(hexv[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                return None
            return f"{_fmt(r)} {_fmt(g)} {_fmt(b)} rg"
        return None
    m = re.match(r"rgb\(\s*(\d+)\D+(\d+)\D+(\d+)", v)
    if m:
        r, g, b = (int(m.group(i)) / 255.0 for i in (1, 2, 3))
        return f"{_fmt(r)} {_fmt(g)} {_fmt(b)} rg"
    return _NAMED_COLORS.get(v)


def _apply_css(style: RichStyle, css: str) -> RichStyle:
    """Apply a CSS ``style`` declaration string to *style*."""
    changes: Dict[str, object] = {}
    for decl in css.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop, value = prop.strip().lower(), value.strip()
        if prop == "font-size":
            m = re.match(r"([0-9]+(?:\.[0-9]+)?)", value)
            if m:
                changes["size"] = max(1.0, float(m.group(1)))
        elif prop == "color":
            col = _parse_color(value)
            if col is not None:
                changes["color"] = col
        elif prop == "font-weight":
            v = value.lower()
            changes["bold"] = v == "bold" or (v.isdigit() and int(v) >= 600)
        elif prop == "font-style":
            changes["italic"] = value.lower() in ("italic", "oblique")
        elif prop == "text-align":
            changes["align"] = {"center": 1, "right": 2, "left": 0}.get(value.lower(), style.align)
    return replace(style, **changes) if changes else style


def _apply_tag(style: RichStyle, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> RichStyle:
    new = style
    if tag in ("b", "strong"):
        new = replace(new, bold=True)
    elif tag in ("i", "em"):
        new = replace(new, italic=True)
    css = dict(attrs).get("style")
    if css:
        new = _apply_css(new, css)
    return new


class _RichTextParser(HTMLParser):
    """Collect styled runs grouped into paragraphs from an XHTML fragment."""

    def __init__(self, default: RichStyle) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: List[RichStyle] = [default]
        self.paragraphs: List[List[RichRun]] = []
        self._current: List[RichRun] = []

    def _flush_paragraph(self) -> None:
        if self._current:
            self.paragraphs.append(self._current)
            self._current = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "br":
            self._flush_paragraph()
            return
        style = _apply_tag(self._stack[-1], tag, attrs)
        if tag == "p":
            self._flush_paragraph()
        self._stack.append(style)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "br":
            self._flush_paragraph()

    def handle_endtag(self, tag: str) -> None:
        if tag == "br":
            return
        if tag == "p":
            self._flush_paragraph()
        if len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.replace("\r", "").replace("\n", " ")
        if text.strip() == "" and not self._current:
            return  # ignore leading/inter-tag whitespace
        self._current.append(RichRun(text, self._stack[-1]))

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush_paragraph()


def parse_rich_text(rc: str, default: RichStyle) -> List[List[RichRun]]:
    """Parse an XHTML rich-text string into paragraphs of styled runs."""
    parser = _RichTextParser(default)
    try:
        parser.feed(rc or "")
        parser.close()
    except (ValueError, AssertionError):
        return []
    return parser.paragraphs


def _style_width_fn(style: RichStyle):
    from .text_metrics import substitute_width_fn

    return substitute_width_fn(
        "Helvetica",
        font_weight=700.0 if style.bold else None,
        italic_angle=-12.0 if style.italic else 0.0,
    )


def _measure(text: str, style: RichStyle) -> float:
    fn = _style_width_fn(style)
    if fn is None:
        return len(text) * _CHAR_WIDTH_EM * style.size
    total = 0.0
    for ch in text:
        code = ord(ch)
        total += fn(code if code < 256 else 0x3F)
    return total / 1000.0 * style.size


@dataclass
class _Line:
    words: List[Tuple[str, RichStyle]]
    align: int
    size: float  # dominant (max) font size on the line


def _wrap_paragraph(runs: List[RichRun], max_width: float) -> List[_Line]:
    """Greedy word-wrap a paragraph's runs into lines of ``(word, style)``."""
    align = runs[0].style.align if runs else 0
    tokens: List[Tuple[str, RichStyle]] = []
    for run in runs:
        for word in run.text.split():
            tokens.append((word, run.style))
    if not tokens:
        return [_Line([], align, 12.0)]

    lines: List[_Line] = []
    cur: List[Tuple[str, RichStyle]] = []
    cur_w = 0.0
    for word, style in tokens:
        word_w = _measure(word, style)
        space_w = _measure(" ", style) if cur else 0.0
        if cur and cur_w + space_w + word_w > max_width:
            lines.append(_Line(cur, align, max(s.size for _t, s in cur)))
            cur, cur_w = [(word, style)], word_w
        else:
            cur.append((word, style))
            cur_w += space_w + word_w
    lines.append(_Line(cur, align, max(s.size for _t, s in cur) if cur else 12.0))
    return lines


def _line_width(line: _Line) -> float:
    total = 0.0
    for i, (word, style) in enumerate(line.words):
        if i:
            total += _measure(" ", style)
        total += _measure(word, style)
    return total


def build_rich_text_content(
    rc: str,
    width: float,
    height: float,
    *,
    default_style: RichStyle,
    padding: float = 2.0,
    default_align: int = 0,
) -> Optional[Tuple[List[str], Dict[str, Dict[str, str]]]]:
    """Lay out rich text into ``(BT…ET body lines, fonts)``, or ``None`` if empty.

    *default_style* seeds size/colour/alignment for text outside any styled span
    (from the ``/DA`` or ``/DS`` default). Returns the content operators for the
    text block (the caller wraps them in ``q``/``Q`` and any background/border)
    plus the font resources the block references.
    """
    seed = replace(default_style, align=default_align)
    paragraphs = parse_rich_text(rc, seed)
    if not paragraphs:
        return None

    max_width = max(1.0, width - 2.0 * padding)
    lines: List[_Line] = []
    for para in paragraphs:
        lines.extend(_wrap_paragraph(para, max_width))
    if not any(line.words for line in lines):
        return None

    body: List[str] = ["BT"]
    fonts: Dict[str, Dict[str, str]] = {}
    cur_font: Optional[Tuple[str, float]] = None
    cur_color: Optional[str] = None
    y = height - padding
    for line in lines:
        y -= line.size
        if line.words:
            total = _line_width(line)
            if line.align == 1:
                x = max(padding, (width - total) / 2.0)
            elif line.align == 2:
                x = max(padding, width - padding - total)
            else:
                x = padding
            for i, (word, style) in enumerate(line.words):
                if i:
                    x += _measure(" ", style)
                fname, base = _FONT_TABLE[(style.bold, style.italic)]
                fonts[fname] = _font_spec(base)
                if (fname, style.size) != cur_font:
                    body.append(f"/{fname} {_fmt(style.size)} Tf")
                    cur_font = (fname, style.size)
                if style.color != cur_color:
                    body.append(style.color)
                    cur_color = style.color
                body.append(f"1 0 0 1 {_fmt(x)} {_fmt(y)} Tm")
                body.append(f"{_pdf_literal(word)} Tj")
                x += _measure(word, style)
        y -= line.size * (_LINE_LEADING - 1.0)
    body.append("ET")
    return body, fonts
