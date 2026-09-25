"""Export a page's *structure* as HTML or Markdown.

Rendering a PDF page is a question about geometry; exporting it as HTML or
Markdown is a question about meaning -- which runs are a heading, where a
paragraph ends, which rows form a table. This library already answers that
question for :meth:`Document.auto_tag`, which infers a structure tree from a
page's layout, so this module reuses exactly that analysis rather than
inventing a second, differently-wrong one: the same column split, the same
reading order, the same size-based heading tiers, the same table detection.

What it adds is the *text*. ``auto_tag`` works in byte ranges and never needs
to decode a string; an export does. Each structure element's byte range is
handed back to :class:`~aspose_pdf.engine.content_stream_parser.ContentStreamParser`,
which decodes it through the font's ``/ToUnicode`` and encoding the same way
:meth:`Document.extract_text` does.

The result is a small document model -- headings, paragraphs, lists, tables,
figures -- that :func:`to_html` and :func:`to_markdown` render. Anything the
model cannot express (exact positioning, colour, fonts) is deliberately not
carried over: this is a conversion to a *flowing document*, not a facsimile.
For a facsimile, export SVG.
"""

from __future__ import annotations

import base64
import html as html_module
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException

from .auto_tag import (
    LayoutElement,
    Matrix,
    TextObject,
    _apply,
    _mul,
    choose_tags,
    detect_columns,
    detect_tables,
    find_layout_elements,
    group_into_paragraphs,
    group_rows,
    is_list_item,
    list_marker,
)
from .content_stream_parser import _MAX_FORM_DEPTH
from .cos import PdfDictionary, PdfName, PdfStream

__all__ = ["Block", "markdown_format", "page_blocks", "to_html", "to_markdown"]

_MAX_CELL_CHARS = 4096
_WHITESPACE = re.compile(r"[ \t\u00a0]+")


@dataclass
class Block:
    """One piece of exported structure."""

    kind: str
    """``heading``, ``paragraph``, ``list``, ``table`` or ``figure``."""

    text: str = ""
    """The block's text, for a heading or paragraph."""

    level: int = 0
    """Heading level, 1-3."""

    items: list[str] = field(default_factory=list)
    """List item texts."""

    rows: list[list[str]] = field(default_factory=list)
    #: Per-cell column span, parallel to :attr:`rows`; 1 unless cells merge.
    spans: list[list[int]] = field(default_factory=list)
    """Table cells, row-major."""

    ordered: bool = False
    """Whether a list is numbered rather than bulleted."""

    alt: str = ""
    """A figure's alternate text."""

    image: bytes | None = None
    """A figure's PNG bytes, when images are being carried over."""


# ---------------------------------------------------------------------------
# Text for a structure element
# ---------------------------------------------------------------------------
def _tf_before(content: bytes, offset: int) -> bytes:
    """The last ``Tf`` operator before *offset*, or ``b""``.

    A text object may inherit its font from before its ``BT``. Decoding the
    object's byte range on its own would then have no font to decode *with*, so
    the operator is carried into the slice.
    """
    window = content[:offset]
    matches = list(re.finditer(rb"/[^\s/<>\[\]()]+\s+[-\d.]+\s+Tf", window))
    return matches[-1].group(0) + b" " if matches else b""


def _element_text(
    content: bytes,
    element: LayoutElement,
    resources: dict,
    limits: Any,
    budget: Any,
) -> str:
    """Decode the text a single ``BT`` … ``ET`` object shows."""
    from .content_stream_parser import ContentStreamParser

    snippet = content[element.start : element.end]
    if not snippet.startswith(b"BT"):
        return ""
    if b"Tf" not in snippet:
        snippet = b"BT " + _tf_before(content, element.start) + snippet[2:]
    try:
        parser = ContentStreamParser(
            snippet, resources, limits=limits, budget=budget
        )
        text = parser.extract_text()
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return ""
    return _WHITESPACE.sub(" ", text.replace("\n", " ")).strip()


@dataclass
class _ContentSource:
    """A content stream and the resource scope in which it executes."""

    content: bytes
    resources: PdfDictionary | None
    _fonts: dict | None = None

    def text_resources(self, pdf: Any) -> dict:
        """Convert fonts on demand without decoding unrelated Form streams."""
        if self._fonts is None:
            fonts = (
                self.resources.mapping.get(PdfName("Font"))
                if self.resources is not None else None
            )
            self._fonts = pdf._convert_cos_to_dict(
                PdfDictionary({PdfName("Font"): fonts} if fonts is not None else {})
            )
        return self._fonts

    def xobject(self, pdf: Any, name: str | None) -> tuple[Any, PdfStream | None]:
        if self.resources is None or name is None:
            return None, None
        xobjects = pdf._resolve(self.resources.mapping.get(PdfName("XObject")))
        if not isinstance(xobjects, PdfDictionary):
            return None, None
        ref = xobjects.mapping.get(PdfName(name.lstrip("/")))
        obj = pdf._resolve(ref)
        return ref, obj if isinstance(obj, PdfStream) else None


def _figure_png(pdf: Any, source: _ContentSource, name: str) -> bytes | None:
    """Reconstruct the image in this content stream's resource scope."""
    from .image_export import reconstruct_image_file
    from .simple_pdf import CosExtractor

    ref, obj = source.xobject(pdf, name)
    if obj is None:
        return None
    try:
        extractor = CosExtractor(
            pdf._cos_doc, b"", limits=pdf._load_limits, budget=pdf._load_budget
        )
        # A named colour space belongs to the same scope as the image paint.
        # Resolve it on a metadata-only copy; the live PDF stays unchanged.
        metadata_stream = obj
        colour_space = pdf._resolve(obj.mapping.get(PdfName("ColorSpace")))
        if isinstance(colour_space, PdfName) and source.resources is not None:
            spaces = pdf._resolve(
                source.resources.mapping.get(PdfName("ColorSpace"))
            )
            if isinstance(spaces, PdfDictionary) and colour_space in spaces.mapping:
                metadata_stream = PdfStream(
                    content=b"",
                    mapping={
                        **obj.mapping,
                        PdfName("ColorSpace"): spaces.mapping[colour_space],
                    },
                )
        data, produced = reconstruct_image_file(
            extractor._resolve_image_meta(metadata_stream),
            pdf._decode_cos_stream(obj, ref),
            ".png",
            limits=pdf._load_limits,
        )
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return None
    return data if produced == "png" else None


# ---------------------------------------------------------------------------
# Page -> blocks
# ---------------------------------------------------------------------------
def page_blocks(
    pdf: Any,
    page_index: int,
    *,
    include_images: bool = True,
    paragraph_gap: float | None = None,
    clip_to_page: bool = True,
) -> list[Block]:
    """Infer *page_index*'s structure and return it as :class:`Block` objects.

    *paragraph_gap* is the largest baseline-to-baseline step, in multiples of
    the font size, at which a line still continues the paragraph above it
    (``None``: the layout analysis's own 1.6). With *clip_to_page*, text and
    images anchored outside the page's visible area -- its crop box, within
    its media box -- are left out, as a viewer never shows them.
    """
    try:
        content = pdf.get_page_content(page_index)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return []
    if not content:
        return []

    limits = getattr(pdf, "_load_limits", None)
    budget = getattr(pdf, "_load_budget", None)
    source = _ContentSource(content, pdf._cos_page_resources(page_index))
    elements, sources = _expand_forms(
        pdf, source, limits=limits, budget=budget
    )
    if clip_to_page:
        x0, y0, x1, y1 = _visible_area(pdf, page_index)
        elements = [e for e in elements if x0 <= e.x <= x1 and y0 <= e.y <= y1]
    text_elements = [e for e in elements if e.kind == "text"]
    tags = choose_tags(
        [
            TextObject(e.start, e.end, e.font_size, e.text_length)
            for e in text_elements
        ]
    )
    for element, tag in zip(text_elements, tags):
        element.tag = tag

    if include_images:
        for element in elements:
            if element.kind == "xobject":
                _, obj = sources[id(element)].xobject(pdf, element.name)
                if (
                    obj is not None
                    and pdf._get_name(obj.mapping.get(PdfName("Subtype"))) == "Image"
                ):
                    element.tag = "Figure"

    tagged = [e for e in elements if e.tag is not None]
    if not tagged:
        return []

    # Keyed by the element, not by its byte offset: text from a form has an
    # offset in the *form's* stream, which would collide with the page's.
    texts: dict[int, str] = {}
    for element in tagged:
        if element.kind == "text":
            source = sources[id(element)]
            texts[id(element)] = _element_text(
                source.content, element, source.text_resources(pdf), limits, budget
            )

    blocks: list[Block] = []
    for column in detect_columns(tagged):
        for kind, rows in detect_tables(group_rows(column)):
            if kind == "table":
                blocks.append(_table_block(rows, texts))
            else:
                flow = [element for row in rows for element in row]
                blocks.extend(
                    _flow_blocks(
                        pdf, flow, texts, sources, include_images, paragraph_gap
                    )
                )
    return [block for block in blocks if _has_content(block)]


def _visible_area(pdf: Any, page_index: int) -> tuple[float, float, float, float]:
    """The page's crop box clipped to its media box (ISO 32000-1 14.11.2)."""
    media = _normalised(pdf.pages[page_index][:4])
    crop = pdf.get_page_crop_box(page_index)
    if crop is None:
        return media
    crop = _normalised(crop)
    area = (
        max(media[0], crop[0]),
        max(media[1], crop[1]),
        min(media[2], crop[2]),
        min(media[3], crop[3]),
    )
    # A crop box entirely off the media box shows nothing; fall back to the
    # media box rather than export an empty page from a malformed one.
    return area if area[0] < area[2] and area[1] < area[3] else media


def _normalised(box: Any) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _expand_forms(
    pdf: Any,
    source: _ContentSource,
    *,
    limits: Any,
    budget: Any,
) -> tuple[list[LayoutElement], dict[int, _ContentSource]]:
    """Visit painted forms, retaining each element's own resource scope.

    Only streams invoked by ``Do`` are decoded. An active-path set breaks
    cycles while allowing the same form to be painted at several positions.
    Form resources fall back to the caller's when absent or empty, matching
    the text reader. Positions and image dimensions are composed into page
    space before the usual reading-order and paragraph analysis.
    """
    expanded: list[LayoutElement] = []
    sources: dict[int, _ContentSource] = {}
    active: set[int] = set()
    visited_elements = 0

    def visit(current: _ContentSource, placement: Matrix, depth: int) -> None:
        nonlocal visited_elements
        if budget is not None:
            budget.check(depth, "max_nesting_depth", "export Form nesting")
        elements = find_layout_elements(current.content, limits=limits, budget=budget)
        for element in elements:
            visited_elements += 1
            if budget is not None:
                budget.check(
                    visited_elements, "max_container_items", "export layout traversal"
                )
            ref, obj = (
                current.xobject(pdf, element.name)
                if element.kind == "xobject" else (None, None)
            )
            if (
                obj is not None
                and pdf._get_name(obj.mapping.get(PdfName("Subtype"))) == "Form"
            ):
                if depth >= _MAX_FORM_DEPTH or id(obj) in active:
                    continue
                own = pdf._resolve(obj.mapping.get(PdfName("Resources")))
                resources = (
                    own if isinstance(own, PdfDictionary) and own.mapping
                    else current.resources
                )
                content = pdf._decode_cos_stream(obj, ref)
                if budget is not None:
                    budget.check(
                        len(content), "max_content_stream_bytes", "export Form content"
                    )
                matrix = _form_matrix(
                    pdf._convert_cos_to_dict(obj.mapping.get(PdfName("Matrix")))
                )
                transform = _mul(
                    _mul(matrix, element.ctm or _IDENTITY_MATRIX), placement
                )
                active.add(id(obj))
                try:
                    visit(_ContentSource(content, resources), transform, depth + 1)
                finally:
                    active.remove(id(obj))
                continue
            element.x, element.y = _apply(placement, element.x, element.y)
            if element.ctm is not None:
                element.ctm = _mul(element.ctm, placement)
                element.width = math.hypot(element.ctm[0], element.ctm[1])
                element.height = math.hypot(element.ctm[2], element.ctm[3])
            sources[id(element)] = current
            expanded.append(element)

    visit(source, _IDENTITY_MATRIX, 0)
    return expanded, sources


_IDENTITY_MATRIX: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _form_matrix(raw: Any) -> Matrix:
    """A form's matrix, or identity where absent or malformed."""
    if isinstance(raw, (list, tuple)) and len(raw) == 6:
        try:
            matrix = tuple(float(v) for v in raw)
            if all(math.isfinite(v) for v in matrix):
                return matrix  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    return _IDENTITY_MATRIX


def _has_content(block: Block) -> bool:
    if block.kind == "figure":
        return bool(block.image) or bool(block.alt)
    if block.kind == "table":
        return any(any(cell for cell in row) for row in block.rows)
    if block.kind == "list":
        return any(block.items)
    return bool(block.text)


def _table_block(rows: list[list[LayoutElement]], texts: dict[int, str]) -> Block:
    """A detected grid becomes a table, laid out on its columns.

    Every row is padded to the table's full width: a column a row leaves blank
    becomes an empty string rather than being closed up, which would shift the
    cells after it into the wrong columns. A cell that merges several columns
    takes the first and leaves the rest empty, and carries its span so HTML can
    say ``colspan``.
    """
    width = max(
        (cell.column + cell.span for row in rows for cell in row), default=0
    )
    cells: list[list[str]] = []
    spans: list[list[int]] = []
    for row in rows:
        line = [""] * width
        row_spans = [1] * width
        for element in sorted(row, key=lambda e: e.x):
            if 0 <= element.column < width:
                line[element.column] = texts.get(id(element), "")[:_MAX_CELL_CHARS]
                row_spans[element.column] = element.span
        cells.append(line)
        spans.append(row_spans)
    return Block(kind="table", rows=cells, spans=spans)


def _flow_blocks(
    pdf: Any,
    flow: list[LayoutElement],
    texts: dict[int, str],
    sources: dict[int, _ContentSource],
    include_images: bool,
    paragraph_gap: float | None = None,
) -> list[Block]:
    blocks: list[Block] = []
    pending_list: list[str] = []
    pending_ordered = False

    def flush_list() -> None:
        if pending_list:
            blocks.append(
                Block(kind="list", items=list(pending_list), ordered=pending_ordered)
            )
            pending_list.clear()

    groups = (
        group_into_paragraphs(flow)
        if paragraph_gap is None
        else group_into_paragraphs(flow, paragraph_gap)
    )
    for group in groups:
        if group[0].kind == "xobject":
            flush_list()
            if include_images:
                blocks.append(_figure_block(pdf, sources[id(group[0])], group[0]))
            continue
        text = " ".join(
            part for part in (texts.get(id(e), "") for e in group) if part
        ).strip()
        if not text:
            continue
        if is_list_item(group):
            kind = list_marker(group[0].text_head)
            if pending_list and (kind == "ol") != pending_ordered:
                flush_list()  # the list changed kind: start a new one
            pending_ordered = kind == "ol"
            pending_list.append(_strip_marker(text, kind))
            continue
        flush_list()
        tag = group[0].tag or "P"
        if tag.startswith("H") and tag[1:].isdigit():
            blocks.append(Block(kind="heading", level=int(tag[1:]), text=text))
        else:
            blocks.append(Block(kind="paragraph", text=text))
    flush_list()
    return blocks


# ``auto_tag.list_marker`` classifies a line as "ul" or "ol"; these strip the
# marker itself, which the list markup supplies again on the other side.
_UL_MARKER = re.compile(r"^[\u2022\u2023\u25aa\u25cf\u25e6\u2043\u2219*+\-\u2013\u2014]\s*")
_OL_MARKER = re.compile(r"^\(?[0-9]{1,3}|^\(?[A-Za-z]|^\(?[ivxlcdmIVXLCDM]{1,7}")
_OL_FULL = re.compile(
    r"^\(?(?:[0-9]{1,3}|[A-Za-z]|[ivxlcdmIVXLCDM]{1,7})[.)]\s*"
)


def _strip_marker(text: str, kind: str | None) -> str:
    """Drop the bullet or number the list markup will supply itself."""
    stripped = text.lstrip()
    if kind == "ul":
        return _UL_MARKER.sub("", stripped, count=1).strip()
    if kind == "ol":
        return _OL_FULL.sub("", stripped, count=1).strip()
    return text


def _figure_block(pdf: Any, source: _ContentSource, element: LayoutElement) -> Block:
    name = (element.name or "").lstrip("/")
    return Block(
        kind="figure",
        alt=element.alt or name,
        image=_figure_png(pdf, source, name) if name else None,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
#: Where a figure's PNG is found: a ``data:`` URI, or a file's relative URL.
ImageSource = Callable[[bytes], str]


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_HTML_PREAMBLE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; line-height: 1.5; margin: 2rem auto;
        max-width: 46rem; padding: 0 1rem; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
td, th {{ border: 1px solid #999; padding: 0.3rem 0.6rem; text-align: left; }}
figure {{ margin: 1rem 0; }}
img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
"""


def to_html(
    pages: list[list[Block]],
    *,
    title: str = "",
    language: str = "en",
    embed_images: bool = True,
    image_src: ImageSource | None = None,
) -> str:
    """Render exported blocks as one HTML document.

    Pages are separated by a horizontal rule rather than kept apart: HTML has
    no page model, and a document that reads as one flow is the point of
    converting to it. *image_src* gives the URL a figure's PNG is found at;
    by default the PNG is embedded as a ``data:`` URI.
    """
    parts = [
        _HTML_PREAMBLE.format(
            lang=html_module.escape(language, quote=True),
            title=html_module.escape(title or "Exported PDF"),
        )
    ]
    for index, blocks in enumerate(pages):
        if index:
            parts.append("<hr>")
        for block in blocks:
            parts.append(_html_block(block, embed_images, image_src or _data_uri))
    parts.append("</body>\n</html>\n")
    return "\n".join(part for part in parts if part)


def _html_block(block: Block, embed_images: bool, image_src: ImageSource = _data_uri) -> str:
    escape = html_module.escape
    if block.kind == "heading":
        level = min(max(block.level, 1), 6)
        return f"<h{level}>{escape(block.text)}</h{level}>"
    if block.kind == "paragraph":
        return f"<p>{escape(block.text)}</p>"
    if block.kind == "list":
        items = "\n".join(f"<li>{escape(item)}</li>" for item in block.items)
        tag = "ol" if block.ordered else "ul"
        return f"<{tag}>\n{items}\n</{tag}>"
    if block.kind == "table":
        rows = []
        for index, row in enumerate(block.rows):
            name = "th" if index == 0 else "td"
            spans = block.spans[index] if index < len(block.spans) else [1] * len(row)
            cells = []
            column = 0
            while column < len(row):
                span = spans[column] if column < len(spans) else 1
                attribute = f' colspan="{span}"' if span > 1 else ""
                cells.append(
                    f"<{name}{attribute}>{escape(row[column])}</{name}>"
                )
                # The columns a merged cell covers are already spoken for.
                column += max(span, 1)
            rows.append(f"<tr>{''.join(cells)}</tr>")
        body = "\n".join(rows)
        return f"<table>\n{body}\n</table>"
    if block.kind == "figure":
        alt = escape(block.alt, quote=True)
        if block.image and embed_images:
            src = escape(image_src(block.image), quote=True)
            return f'<figure><img src="{src}" alt="{alt}"></figure>'
        return f"<figure><figcaption>{escape(block.alt)}</figcaption></figure>"
    return ""


#: The Markdown dialects :func:`to_markdown` writes, by lower-cased name.
MARKDOWN_FORMATS = {"gfm": "GFM", "commonmark": "CommonMark"}


def markdown_format(name: Any) -> str:
    """The canonical name of Markdown dialect *name*; ``ValueError`` if unknown."""
    canonical = MARKDOWN_FORMATS.get(str(name).lower()) if isinstance(name, str) else None
    if canonical is None:
        raise ValueError(
            f"Unknown Markdown format {name!r}; use one of {sorted(MARKDOWN_FORMATS.values())}"
        )
    return canonical


def to_markdown(
    pages: list[list[Block]],
    *,
    title: str = "",
    embed_images: bool = True,
    image_src: ImageSource | None = None,
    dialect: str = "GFM",
) -> str:
    """Render exported blocks as one Markdown document.

    *dialect* is ``"GFM"`` (GitHub Flavored Markdown, the default) or
    ``"CommonMark"``, which has no tables: a table is written as the HTML
    block CommonMark passes through. *image_src* is as for :func:`to_html`.
    """
    commonmark = markdown_format(dialect) == "CommonMark"
    parts: list[str] = []
    if title and not _starts_with_heading(pages):
        # A document whose first block is already a heading states its own
        # title; repeating the metadata one above it just doubles it.
        parts.append(f"# {_md_escape(title)}")
    for index, blocks in enumerate(pages):
        if index:
            parts.append("---")
        for block in blocks:
            if commonmark and block.kind == "table":
                rendered = _html_block(block, embed_images)
            else:
                rendered = _markdown_block(block, embed_images, image_src or _data_uri)
            if rendered:
                parts.append(rendered)
    return "\n\n".join(parts) + "\n"


# Inline Markdown only misreads a handful of characters anywhere in a line;
# the rest (``#``, ``-``, ``1.``) matter solely at the start of one. Escaping
# everything turns ordinary prose into a thicket of backslashes.
def _starts_with_heading(pages: list[list[Block]]) -> bool:
    for blocks in pages:
        for block in blocks:
            return block.kind == "heading"
    return False


_MD_INLINE = re.compile(r"([\\`*_\[\]<>])")
_MD_LINE_START = re.compile(r"^(\s*)([#>|]|[-+*](?=\s)|[0-9]{1,9}[.)](?=\s))")


def _md_escape(text: str) -> str:
    escaped = _MD_INLINE.sub(r"\\\1", text)
    return _MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), escaped)


def _markdown_block(block: Block, embed_images: bool, image_src: ImageSource = _data_uri) -> str:
    if block.kind == "heading":
        level = min(max(block.level, 1), 6)
        return "#" * level + " " + _md_escape(block.text)
    if block.kind == "paragraph":
        return _md_escape(block.text)
    if block.kind == "list":
        if block.ordered:
            return "\n".join(
                f"{index}. {_md_escape(item)}"
                for index, item in enumerate(block.items, 1)
            )
        return "\n".join(f"- {_md_escape(item)}" for item in block.items)
    if block.kind == "table":
        if not block.rows:
            return ""
        width = max(len(row) for row in block.rows)
        def line(values: list[str]) -> str:
            padded = list(values) + [""] * (width - len(values))
            return "| " + " | ".join(_md_cell(value) for value in padded) + " |"
        head = line(block.rows[0])
        rule = "| " + " | ".join("---" for _ in range(width)) + " |"
        body = [line(row) for row in block.rows[1:]]
        return "\n".join([head, rule, *body])
    if block.kind == "figure":
        alt = _md_escape(block.alt)
        if block.image and embed_images:
            return f"![{alt}]({image_src(block.image)})"
        return f"*{alt}*" if alt else ""
    return ""


def _md_cell(value: str) -> str:
    """A table cell: pipes escaped, newlines flattened (GFM rows are one line)."""
    return _md_escape(value).replace("|", "\\|").replace("\n", " ")
