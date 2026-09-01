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
import re
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException

from .auto_tag import (
    LayoutElement,
    TextObject,
    choose_tags,
    detect_columns,
    detect_tables,
    find_layout_elements,
    group_into_paragraphs,
    group_rows,
    is_list_item,
    list_marker,
)

__all__ = ["Block", "page_blocks", "to_html", "to_markdown"]

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


def _figure_png(pdf: Any, page_index: int, name: str) -> bytes | None:
    """PNG bytes for the image XObject *name* on the page, or ``None``.

    Reuses the same reconstruction :meth:`SimplePdf.save_image` performs, so an
    exported figure is the image that extraction would have written -- colour
    conversion, palettes and ``/Decode`` included.
    """
    from .image_export import reconstruct_image_file

    key = _image_key(pdf, page_index, name)
    if key is None:
        return None
    try:
        data, produced = reconstruct_image_file(
            pdf._image_meta.get(key), pdf.images[key], ".png", None
        )
    except PdfResourceLimitException:
        raise
    except (KeyError, AttributeError, *PDF_OPERATION_ERRORS):
        return None
    return data if produced == "png" else None


def _image_key(pdf: Any, page_index: int, name: str) -> str | None:
    """Resolve a page-local XObject name to a key in ``pdf.images``.

    Resource names are page-local -- two pages routinely both call their image
    ``/Im0`` -- so a loaded document stores them under a document-wide key and
    an authored one under the resource name itself.
    """
    images = getattr(pdf, "images", None)
    if not images:
        return None
    if name in images:
        return name
    suffix = f"_{name}"
    for key in images:
        if key == name or key.endswith(suffix):
            return key
    return None


# ---------------------------------------------------------------------------
# Page -> blocks
# ---------------------------------------------------------------------------
def page_blocks(
    pdf: Any,
    page_index: int,
    *,
    include_images: bool = True,
) -> list[Block]:
    """Infer *page_index*'s structure and return it as :class:`Block` objects."""
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
    elements = find_layout_elements(content, limits=limits, budget=budget)
    text_elements = [e for e in elements if e.kind == "text"]
    tags = choose_tags(
        [
            TextObject(e.start, e.end, e.font_size, e.text_length)
            for e in text_elements
        ]
    )
    for element, tag in zip(text_elements, tags):
        element.tag = tag

    image_names: set[str] = set()
    if include_images:
        try:
            image_names = set(pdf._image_xobject_names(page_index))
        except PDF_OPERATION_ERRORS:
            image_names = set()
        for element in elements:
            if (
                element.kind == "xobject"
                and element.name
                and element.name.lstrip("/") in image_names
            ):
                element.tag = "Figure"

    tagged = [e for e in elements if e.tag is not None]
    if not tagged:
        return []

    resources = _plain_resources(pdf, page_index)
    texts: dict[int, str] = {}
    for element in tagged:
        if element.kind == "text":
            texts[element.start] = _element_text(
                content, element, resources, limits, budget
            )

    blocks: list[Block] = []
    for column in detect_columns(tagged):
        for kind, rows in detect_tables(group_rows(column)):
            if kind == "table":
                blocks.append(_table_block(rows, texts))
            else:
                flow = [element for row in rows for element in row]
                blocks.extend(
                    _flow_blocks(pdf, page_index, flow, texts, include_images)
                )
    return [block for block in blocks if _has_content(block)]


def _plain_resources(pdf: Any, page_index: int) -> dict:
    from .cos import PdfDictionary, PdfName

    try:
        page = pdf._get_page_dict(page_index)
        resources = pdf._resolve(page.mapping.get(PdfName("Resources")))
        if isinstance(resources, PdfDictionary) and hasattr(
            pdf, "_convert_cos_to_dict"
        ):
            return pdf._convert_cos_to_dict(resources) or {}
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return {}
    except AttributeError:
        return {}
    return {}


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
                line[element.column] = texts.get(element.start, "")[:_MAX_CELL_CHARS]
                row_spans[element.column] = element.span
        cells.append(line)
        spans.append(row_spans)
    return Block(kind="table", rows=cells, spans=spans)


def _flow_blocks(
    pdf: Any,
    page_index: int,
    flow: list[LayoutElement],
    texts: dict[int, str],
    include_images: bool,
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

    for group in group_into_paragraphs(flow):
        if group[0].kind == "xobject":
            flush_list()
            if include_images:
                blocks.append(_figure_block(pdf, page_index, group[0]))
            continue
        text = " ".join(
            part for part in (texts.get(e.start, "") for e in group) if part
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


def _figure_block(pdf: Any, page_index: int, element: LayoutElement) -> Block:
    name = (element.name or "").lstrip("/")
    return Block(
        kind="figure",
        alt=element.alt or name,
        image=_figure_png(pdf, page_index, name) if name else None,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
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
) -> str:
    """Render exported blocks as one HTML document.

    Pages are separated by a horizontal rule rather than kept apart: HTML has
    no page model, and a document that reads as one flow is the point of
    converting to it.
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
            parts.append(_html_block(block, embed_images))
    parts.append("</body>\n</html>\n")
    return "\n".join(part for part in parts if part)


def _html_block(block: Block, embed_images: bool) -> str:
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
            return (
                f'<figure><img src="{_data_uri(block.image)}" alt="{alt}">'
                "</figure>"
            )
        return f"<figure><figcaption>{escape(block.alt)}</figcaption></figure>"
    return ""


def to_markdown(
    pages: list[list[Block]],
    *,
    title: str = "",
    embed_images: bool = True,
) -> str:
    """Render exported blocks as one Markdown (GFM) document."""
    parts: list[str] = []
    if title and not _starts_with_heading(pages):
        # A document whose first block is already a heading states its own
        # title; repeating the metadata one above it just doubles it.
        parts.append(f"# {_md_escape(title)}")
    for index, blocks in enumerate(pages):
        if index:
            parts.append("---")
        for block in blocks:
            rendered = _markdown_block(block, embed_images)
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


def _markdown_block(block: Block, embed_images: bool) -> str:
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
            return f"![{alt}]({_data_uri(block.image)})"
        return f"*{alt}*" if alt else ""
    return ""


def _md_cell(value: str) -> str:
    """A table cell: pipes escaped, newlines flattened (GFM rows are one line)."""
    return _md_escape(value).replace("|", "\\|").replace("\n", " ")
