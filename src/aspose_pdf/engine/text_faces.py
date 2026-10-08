"""Measured faces: how wide text is in a font, and how it is written.

One place for the two kinds of font this package can author with, because more
than one thing needs them: a table measures a column's text with them (see
:mod:`.tables`) and a flowing block measures a line (:mod:`.text_blocks`).

* :class:`_StandardFace` is one of the fourteen standard fonts, measured by the
  advances in :mod:`.std_metrics` and written in its own base encoding.
* :class:`_EmbeddedFace` is an authored Type0 font, measured by the CID widths
  its subset carries and written as two-byte codes.

Both answer :meth:`_Face.wrap`, which is the whole reason this is shared: the
measuring is what turns a width into lines, and it needs no optional
dependency. The complex-text path (`Page.add_text(layout=...)`) is a different
thing -- it shapes with HarfBuzz and needs an embedded font -- and is not what
this is for.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..exceptions import PdfValidationException
from .cos import PdfName

#: How tall a line is, as a multiple of the font size, when nothing says.
DEFAULT_LINE_HEIGHT = 1.18


class _Face:
    """A measured face: how wide text is in it, and how it is written."""

    def width(self, text: str, size: float) -> float:
        raise NotImplementedError

    def wrap(self, text: str, max_width: float, size: float) -> list[str]:
        """*text* broken into lines no wider than *max_width*.

        Explicit newlines are hard breaks; inside a line words are packed
        greedily and a word too long to fit is broken so that nothing overflows
        the measure -- a table's column or a text block's width, which is the
        same question. The same rules ``field_appearance._wrap_text`` follows
        for a multiline form field -- stated again here because that one
        measures by single-byte code and this has to measure an embedded font's
        text too.
        """
        if max_width <= 0 or size <= 0:
            return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines: list[str] = []
        for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            current = ""
            for word in paragraph.split():
                remaining = word
                while remaining and self.width(remaining, size) > max_width:
                    if current:
                        lines.append(current)
                        current = ""
                    cut = 1
                    while (
                        cut < len(remaining)
                        and self.width(remaining[: cut + 1], size) <= max_width
                    ):
                        cut += 1
                    lines.append(remaining[:cut])
                    remaining = remaining[cut:]
                if not remaining:
                    continue
                candidate = remaining if not current else f"{current} {remaining}"
                if self.width(candidate, size) <= max_width:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = remaining
            lines.append(current)
        return lines or [""]


    def show_segments(self, text: str) -> bytes:
        """*text* as the codes this face's show strings are written in."""
        raise NotImplementedError

    @property
    def hex_show_strings(self) -> bool:
        """Whether a show string is written as hex rather than as a literal."""
        raise NotImplementedError

    def justified_content(
        self,
        words: Sequence[str],
        x: float,
        y: float,
        resource: str,
        size: float,
        color: Any,
        measure: float,
    ) -> bytes:
        """One line whose words are spread to fill *measure* points.

        Each word keeps its own trailing space, so its natural advance is still
        there, and the slack is divided equally among the gaps and written as
        ``TJ`` adjustments. A line of one word has no gap to open and is drawn
        as it is -- justifying it would mean stretching the word itself.
        """
        from .content_authoring import build_adjusted_text_stream

        if len(words) < 2:
            return self.content(" ".join(words), x, y, resource, size, color)
        natural = self.width(" ".join(words), size)
        slack = max(0.0, measure - natural)
        extra = slack / (len(words) - 1)
        segments = [
            self.show_segments(word + " ") for word in words[:-1]
        ] + [self.show_segments(words[-1])]
        return build_adjusted_text_stream(
            segments,
            [extra] * (len(words) - 1),
            x,
            y,
            resource,
            size,
            color,
            hex_strings=self.hex_show_strings,
        )


class _StandardFace(_Face):
    """One of the fourteen standard fonts, measured by its own advances."""

    def __init__(self, pdf: Any, font_name: str) -> None:
        from .std_fonts import StandardFonts
        from .std_metrics import WIDTHS

        self.name = str(font_name or "Helvetica").lstrip("/")
        if WIDTHS.get(self.name) is None:
            allowed = ", ".join(sorted(WIDTHS))
            raise PdfValidationException(
                f"The font is one of the standard 14 fonts, or an embedded "
                f"one through font=: {allowed}"
            )
        self.encoding = StandardFonts.get_default_encoding(self.name)
        self._pdf = pdf

    def width(self, text: str, size: float) -> float:
        from .agl import encode_with_base_encoding
        from .std_metrics import text_width

        codes = encode_with_base_encoding(text, self.encoding)
        if codes is None:
            # A character this encoding has no code for: the draw refuses it, and
            # measuring it as the widest thing that could stand there keeps the
            # layout from being the thing that hides the problem.
            return len(text) * 0.6 * size
        return text_width(codes, self.name, size) or 0.0

    def line_height(self, size: float) -> float:
        from .std_metrics import bounds

        top, bottom = bounds(self.name)
        return (top - bottom) / 1000.0 * size

    def resource(self, page_index: int) -> str:
        return self._pdf._register_standard_font_resource(page_index, self.name)

    def content(
        self, text: str, x: float, y: float, resource: str, size: float, color: Any
    ) -> bytes:
        from .content_authoring import build_text_stream

        return build_text_stream(text, x, y, resource, size, color, self.encoding)

    def show_segments(self, text: str) -> bytes:
        from .content_authoring import encode_simple_text

        return encode_simple_text(text, self.encoding)

    @property
    def hex_show_strings(self) -> bool:
        return False

    def justified_content(
        self,
        words: Sequence[str],
        x: float,
        y: float,
        resource: str,
        size: float,
        color: Any,
        measure: float,
    ) -> bytes:
        """A justified line, spread with ``Tw`` rather than with ``TJ``.

        A simple font's space *is* the single-byte code 32, so widening it is
        the whole job -- and it leaves the line with no positioning adjustments
        for an extractor to read as a second space. See
        :func:`.content_authoring.build_word_spaced_text_stream`.
        """
        from .content_authoring import build_word_spaced_text_stream

        text = " ".join(words)
        gaps = text.count(" ")
        if gaps < 1:
            return self.content(text, x, y, resource, size, color)
        slack = max(0.0, measure - self.width(text, size))
        return build_word_spaced_text_stream(
            text, slack / gaps, x, y, resource, size, color, self.encoding
        )


class _EmbeddedFace(_Face):
    """An embedded Type0 font, measured by the CID advances it carries."""

    def __init__(self, pdf: Any, font: Any) -> None:
        from .font_authoring import prepare_authored_font

        self._pdf = pdf
        self.authored = prepare_authored_font(font, limits=pdf._load_limits)
        self._widths = self.authored.cid_widths()
        self._resource: str | None = None

    def _encode(self, text: str) -> bytes:
        return self.authored.encode(text)

    def width(self, text: str, size: float) -> float:
        """How wide *text* is, after making sure the subset holds its glyphs.

        A subset font grows as it is *encoded*: a glyph nothing has asked for yet
        has no CID and no advance. Measuring from a table taken before the text
        was encoded therefore answered zero for every character -- so the text is
        encoded first, and the table re-read the moment a CID is missing from it.
        """
        if not text:
            return 0.0
        encoded = self._encode(text)
        total = 0
        for index in range(0, len(encoded) - 1, 2):
            cid = (encoded[index] << 8) | encoded[index + 1]
            advance = self._widths.get(cid)
            if advance is None:
                self._widths = self.authored.cid_widths()
                advance = self._widths.get(cid, 0)
            total += advance
        return total / 1000.0 * size

    def line_height(self, size: float) -> float:
        metrics = dict(self.authored.descriptor_metrics)
        box = metrics.get("FontBBox")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            return (float(box[3]) - float(box[1])) / 1000.0 * size
        top = float(metrics.get("Ascent", 750) or 750)
        bottom = float(metrics.get("Descent", -250) or -250)
        return (top - bottom) / 1000.0 * size

    def resource(self, page_index: int) -> str:
        # One font graph per table, named in each page's resources it reaches.
        type0_ref, _parts = self._pdf._build_type0_font_graph(self.authored)
        fonts = self._pdf._ensure_resource_subdict(page_index, "Font")
        name = self._pdf._unique_resource_name(fonts, "F")
        fonts.mapping[PdfName(name)] = type0_ref
        return name

    def content(
        self, text: str, x: float, y: float, resource: str, size: float, color: Any
    ) -> bytes:
        from .content_authoring import build_cid_text_stream

        return build_cid_text_stream(self._encode(text), x, y, resource, size, color)

    def show_segments(self, text: str) -> bytes:
        return self._encode(text)

    @property
    def hex_show_strings(self) -> bool:
        return True
