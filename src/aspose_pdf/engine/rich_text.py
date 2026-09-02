"""Parse and render the XHTML rich text of ``/RC`` (annotations) and ``/RV``.

Rich text is a small XHTML fragment (``<body>`` with ``<p>``/``<span>``/
``<b>``/``<i>``/``<tt>`` and ``style`` attributes). We parse it into runs and lay
them out into an appearance content stream, honouring per-run font size, colour,
font family, bold/italic and paragraph text alignment. Fonts are the Standard-14
text faces -- Helvetica, Times and Courier, each in four styles -- chosen by
``font-family`` and measured with the bundled metric-compatible substitute for
*that* family, so wrapping and alignment use real advances rather than one
family's advances for all three.

Parsing uses :mod:`html.parser` (no DTD/entity expansion, so no XXE risk) and is
forgiving of the not-quite-XML markup writers emit. Unsupported tags degrade to
their text content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any

from .field_appearance import _fmt, _pdf_literal

# (family, bold, italic) -> (resource name, Standard-14 BaseFont). The short
# resource names are the ones Acrobat uses for these faces in an AcroForm /DR,
# so a viewer that already carries them recognises what it is being handed.
_FONT_TABLE = {
    ("sans", False, False): ("Helv", "Helvetica"),
    ("sans", True, False): ("HeBo", "Helvetica-Bold"),
    ("sans", False, True): ("HeOb", "Helvetica-Oblique"),
    ("sans", True, True): ("HeBO", "Helvetica-BoldOblique"),
    ("serif", False, False): ("TiRo", "Times-Roman"),
    ("serif", True, False): ("TiBo", "Times-Bold"),
    ("serif", False, True): ("TiIt", "Times-Italic"),
    ("serif", True, True): ("TiBI", "Times-BoldItalic"),
    ("mono", False, False): ("Cour", "Courier"),
    ("mono", True, False): ("CoBo", "Courier-Bold"),
    ("mono", False, True): ("CoOb", "Courier-Oblique"),
    ("mono", True, True): ("CoBO", "Courier-BoldOblique"),
}

# The face each family is measured against. Times and Courier are metrically
# very different from Helvetica -- Courier is fixed-pitch -- so wrapping and
# alignment need the family's own advances, not one family's for all three.
_FAMILY_BASE = {"sans": "Helvetica", "serif": "Times-Roman", "mono": "Courier"}

# HTML gives these tags a monospace default; a rich-text writer that emits them
# means it, and there is a Standard-14 face to honour it with.
_MONOSPACE_TAGS = frozenset({"tt", "code", "kbd", "samp"})

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

# "no width function was supplied", which is different from "there is none":
# a document font may legitimately have no /Widths to measure with.
_UNSET = object()


@dataclass(frozen=True)
class RichStyle:
    """The resolved style of a text run."""

    size: float = 12.0
    color: str = "0 g"   # a nonstroking-colour operator, e.g. "1 0 0 rg"
    bold: bool = False
    italic: bool = False
    align: int = 0        # 0 left, 1 centre, 2 right
    family: str = "sans"  # "sans", "serif" or "mono" -- a _FONT_TABLE key


@dataclass
class RichRun:
    text: str
    style: RichStyle


@dataclass(frozen=True)
class DocumentFont:
    """The document's own font for a rich-text block, when there is one.

    A form field names its font in ``/DA``, and that font may be embedded --
    a brand face nothing in the Standard 14 resembles. Runs asking for no other
    face are drawn with it, by *reference*: the resource already exists in the
    form's ``/DR``, so nothing has to be synthesised and nothing is re-encoded.

    A run that asks for bold, italic, or a different family cannot be: an
    arbitrary embedded face has no bold sibling to reach for. Those fall back
    to the Standard 14, which is why *family* is recorded -- the fallback
    should be the family the document font belongs to, not a default.
    """

    resource: str                      # the /DR key, used verbatim in Tf
    family: str = "sans"               # what the Standard 14 falls back to
    width_of: Any = None               # code -> advance in 1/1000 em, or None


def _font_spec(base: str) -> dict[str, str]:
    return {"Subtype": "Type1", "BaseFont": base, "Encoding": "WinAnsiEncoding"}


def _parse_color(value: str) -> str | None:
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
    changes: dict[str, object] = {}
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
        elif prop == "font-family":
            family = _css_family(value)
            if family is not None:
                changes["family"] = family
    return replace(style, **changes) if changes else style


def _css_family(value: str) -> str | None:
    """Resolve a CSS ``font-family`` list to a Standard-14 family, or ``None``.

    A font stack is a list of preferences ending in a generic name, so the
    first entry that names something recognisable wins and the rest are the
    fallbacks it was written to avoid. When nothing in the list is recognised
    the answer is ``None`` -- the run keeps the family it inherited, which is a
    better guess than the default.
    """
    from .std_font_data import family_from_name

    for candidate in value.split(","):
        name = candidate.strip().strip("'\"")
        if not name:
            continue
        family = family_from_name(name)
        if family is not None:
            return family
    return None


def _apply_tag(style: RichStyle, tag: str, attrs: list[tuple[str, str | None]]) -> RichStyle:
    new = style
    if tag in ("b", "strong"):
        new = replace(new, bold=True)
    elif tag in ("i", "em"):
        new = replace(new, italic=True)
    elif tag in _MONOSPACE_TAGS:
        new = replace(new, family="mono")
    css = dict(attrs).get("style")
    if css:
        new = _apply_css(new, css)
    return new


class _RichTextParser(HTMLParser):
    """Collect styled runs grouped into paragraphs from an XHTML fragment."""

    def __init__(self, default: RichStyle) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[RichStyle] = [default]
        self.paragraphs: list[list[RichRun]] = []
        self._current: list[RichRun] = []

    def _flush_paragraph(self) -> None:
        if self._current:
            self.paragraphs.append(self._current)
            self._current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._flush_paragraph()
            return
        style = _apply_tag(self._stack[-1], tag, attrs)
        if tag == "p":
            self._flush_paragraph()
        self._stack.append(style)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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


def parse_rich_text(rc: str, default: RichStyle) -> list[list[RichRun]]:
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
        _FAMILY_BASE.get(style.family, "Helvetica"),
        font_weight=700.0 if style.bold else None,
        italic_angle=-12.0 if style.italic else 0.0,
    )


def _face_for(
    style: RichStyle, default: RichStyle, document_font: DocumentFont | None
) -> tuple[str, dict[str, str] | None, Any]:
    """``(resource name, font spec to synthesise or None, width function)``.

    The document's own font is used only for a run that asks for exactly the
    face the field declares -- no bold, no italic, and the same family. Anything
    else needs a face that font cannot provide, so it falls back to the
    Standard 14.
    """
    if (
        document_font is not None
        and not style.bold
        and not style.italic
        and style.family == default.family
    ):
        return document_font.resource, None, document_font.width_of
    name, base = _FONT_TABLE[(style.family, style.bold, style.italic)]
    return name, _font_spec(base), _style_width_fn(style)


def _measure(text: str, style: RichStyle, fn: Any = _UNSET) -> float:
    if fn is _UNSET:
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
    words: list[tuple[str, RichStyle]]
    align: int
    size: float  # dominant (max) font size on the line


def _wrap_paragraph(
    runs: list[RichRun], max_width: float, widths: Any = None
) -> list[_Line]:
    """Greedy word-wrap a paragraph's runs into lines of ``(word, style)``.

    *widths* maps a style to the advance function that style is drawn with, so
    a run using the document's own font is wrapped on *its* metrics rather than
    on a substitute's.
    """
    def width_fn(style: RichStyle) -> Any:
        return widths(style) if widths is not None else _UNSET

    align = runs[0].style.align if runs else 0
    tokens: list[tuple[str, RichStyle]] = []
    for run in runs:
        for word in run.text.split():
            tokens.append((word, run.style))
    if not tokens:
        return [_Line([], align, 12.0)]

    lines: list[_Line] = []
    cur: list[tuple[str, RichStyle]] = []
    cur_w = 0.0
    for word, style in tokens:
        fn = width_fn(style)
        word_w = _measure(word, style, fn)
        space_w = _measure(" ", style, fn) if cur else 0.0
        if cur and cur_w + space_w + word_w > max_width:
            lines.append(_Line(cur, align, max(s.size for _t, s in cur)))
            cur, cur_w = [(word, style)], word_w
        else:
            cur.append((word, style))
            cur_w += space_w + word_w
    lines.append(_Line(cur, align, max(s.size for _t, s in cur) if cur else 12.0))
    return lines


def _line_width(line: _Line, widths: Any = None) -> float:
    total = 0.0
    for i, (word, style) in enumerate(line.words):
        fn = widths(style) if widths is not None else _UNSET
        if i:
            total += _measure(" ", style, fn)
        total += _measure(word, style, fn)
    return total


def build_rich_text_content(
    rc: str,
    width: float,
    height: float,
    *,
    default_style: RichStyle,
    padding: float = 2.0,
    default_align: int = 0,
    document_font: DocumentFont | None = None,
) -> tuple[list[str], dict[str, dict[str, str] | None]] | None:
    """Lay out rich text into ``(BT…ET body lines, fonts)``, or ``None`` if empty.

    *default_style* seeds size/colour/alignment for text outside any styled span
    (from the ``/DA`` or ``/DS`` default). *document_font*, when given, is the
    field's own font: runs asking for exactly the face it declares are drawn
    with it by reference. Returns the content operators for the text block (the
    caller wraps them in ``q``/``Q`` and any background/border) plus the font
    resources the block references. A resource whose spec is ``None`` is the
    document font: it is in the mapping because the layout used it, and the
    value is empty because the caller -- not this function -- holds the object
    to point at.
    """
    seed = replace(default_style, align=default_align)
    paragraphs = parse_rich_text(rc, seed)
    if not paragraphs:
        return None

    def widths(style: RichStyle) -> Any:
        return _face_for(style, seed, document_font)[2]

    max_width = max(1.0, width - 2.0 * padding)
    lines: list[_Line] = []
    for para in paragraphs:
        lines.extend(_wrap_paragraph(para, max_width, widths))
    if not any(line.words for line in lines):
        return None

    body: list[str] = ["BT"]
    fonts: dict[str, dict[str, str] | None] = {}
    cur_font: tuple[str, float] | None = None
    cur_color: str | None = None
    y = height - padding
    for line in lines:
        y -= line.size
        if line.words:
            total = _line_width(line, widths)
            if line.align == 1:
                x = max(padding, (width - total) / 2.0)
            elif line.align == 2:
                x = max(padding, width - padding - total)
            else:
                x = padding
            for i, (word, style) in enumerate(line.words):
                fname, spec, fn = _face_for(style, seed, document_font)
                if i:
                    x += _measure(" ", style, fn)
                fonts[fname] = spec
                if (fname, style.size) != cur_font:
                    body.append(f"/{fname} {_fmt(style.size)} Tf")
                    cur_font = (fname, style.size)
                if style.color != cur_color:
                    body.append(style.color)
                    cur_color = style.color
                body.append(f"1 0 0 1 {_fmt(x)} {_fmt(y)} Tm")
                body.append(f"{_pdf_literal(word)} Tj")
                x += _measure(word, style, fn)
        y -= line.size * (_LINE_LEADING - 1.0)
    body.append("ET")
    return body, fonts
