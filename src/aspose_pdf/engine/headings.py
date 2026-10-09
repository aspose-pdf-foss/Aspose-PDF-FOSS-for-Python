"""Finding a document's headings, so a bookmark tree can be built from them.

Two sources, and the order matters. A **tagged** document has already been told
what its headings are: the structure tree names them ``/H1``..``/H6``, or ``/H``
with the nesting saying the level, and that is the author's own answer. An
untagged one has to be read, which is the same size-tier analysis
:func:`~aspose_pdf.engine.text_export.page_blocks` performs for the HTML and
Markdown exports -- a heuristic, and labelled one.

What a bookmark needs beyond the text is a *place*: the structure path gets it
from the marked-content sequence the element owns (:func:`.auto_tag.mcid_spans`
bounds it, and the text objects inside carry their coordinates), the layout path
from the block's own anchor. Without a position a bookmark can only point at the
top of a page, which for the third heading on it is the wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException
from .cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString

#: The deepest heading level ISO 32000-1 Table 333 names.
MAX_HEADING_LEVEL = 6

#: Structure types that enclose a section of a document, and so give a bare
#: ``/H`` its level: one ``/H`` per ``/Sect``, nested as the sections are
#: (ISO 14289-1 7.4's unnumbered-heading form).
_SECTION_TYPES = frozenset({"Sect", "Part", "Art", "Div"})


@dataclass(frozen=True)
class Heading:
    """One heading: what it says, how deep it is, and where it is."""

    level: int
    text: str
    page_index: int
    #: The baseline origin of the heading's first glyph, in default user space.
    #: ``None`` for a heading whose place could not be told -- a structure
    #: element whose marked content is not on its own page, say -- which a
    #: bookmark then points at by page alone.
    x: float | None
    y: float | None
    #: ``"structure"`` when the document said so itself, ``"layout"`` when the
    #: size tiers were read. Worth reporting: one is an author's answer and the
    #: other is a guess.
    source: str


def find_headings(
    pdf: Any,
    *,
    max_level: int = MAX_HEADING_LEVEL,
    prefer_structure: bool = True,
) -> list[Heading]:
    """Every heading in *pdf*, in reading order.

    With *prefer_structure* the structure tree is used when it has headings in
    it, and the size-tier analysis only otherwise -- so a tagged document is
    read as it was written. ``prefer_structure=False`` reads the pages whatever
    the tree says, which is what a document whose tagging is a shell wants.
    """
    level_cap = max(1, min(int(max_level), MAX_HEADING_LEVEL))
    if prefer_structure:
        from_tree = _from_structure(pdf, level_cap)
        if from_tree:
            return from_tree
    return _from_layout(pdf, level_cap)


# ---------------------------------------------------------------------------
# What the document says about itself
# ---------------------------------------------------------------------------
def _heading_level(kind: str, sections: int) -> int | None:
    """The level *kind* stands for, or ``None`` when it is not a heading.

    ``H1``..``H6`` say it themselves. A bare ``H`` does not, and takes the depth
    of the sections around it instead: ISO 14289-1 has one ``H`` per ``/Sect``
    with the nesting carrying the level, so an ``H`` inside two sections is a
    second-level heading. Outside any section it is the first.
    """
    if kind == "H":
        return max(1, sections)
    if len(kind) == 2 and kind[0] == "H" and kind[1].isdigit():
        level = int(kind[1])
        return level if 1 <= level <= MAX_HEADING_LEVEL else None
    return None


def _structure_text(element: PdfDictionary, pdf: Any) -> str | None:
    """``/ActualText`` or ``/Alt``, which say what an element reads as."""
    for key in ("ActualText", "Alt"):
        value = pdf._resolve(element.mapping.get(PdfName(key)))
        if isinstance(value, PdfString):
            from .cos import decode_pdf_text_string

            text = decode_pdf_text_string(value).strip()
            if text:
                return text
    return None


def _element_marks(pdf: Any, element: PdfDictionary) -> list[tuple[int, int]]:
    """``(page_index, mcid)`` for every marked sequence *element* owns directly.

    A kid may be a bare number -- on the element's own ``/Pg`` -- or a
    marked-content reference naming another page, which is how a heading split
    across a page break is written. Both are followed, because the position has
    to come from the page the content is actually on.
    """
    own_page = pdf._tagged_element_page_number(element)
    own_index = None if own_page is None else own_page - 1
    kids = pdf._resolve(element.mapping.get(PdfName("K")))
    items = kids.items if isinstance(kids, PdfArray) else [kids]
    marks: list[tuple[int, int]] = []
    for item in items:
        resolved = pdf._resolve(item)
        if isinstance(resolved, PdfNumber):
            if own_index is not None:
                marks.append((own_index, int(resolved.value)))
            continue
        if not isinstance(resolved, PdfDictionary):
            continue
        mcid = pdf._resolve(resolved.mapping.get(PdfName("MCID")))
        if not isinstance(mcid, PdfNumber):
            continue
        page = pdf._resolve(resolved.mapping.get(PdfName("Pg")))
        index = own_index
        if isinstance(page, PdfDictionary):
            for candidate in range(len(pdf.pages)):
                if pdf._get_page_dict(candidate) is page:
                    index = candidate
                    break
        if index is not None:
            marks.append((index, int(mcid.value)))
    return marks


def _marked_content(
    pdf: Any, page_index: int, mcids: list[int]
) -> tuple[str, float | None, float | None]:
    """The text and the top-left anchor of *mcids* on a page.

    The spans :func:`.auto_tag.mcid_spans` returns bound the content; the text
    objects :func:`.auto_tag.find_layout_elements` finds inside them carry the
    coordinates and decode to the text. The anchor is the **topmost** element's,
    which is where a reader's eye goes and so where a bookmark should land.
    """
    from .auto_tag import find_layout_elements, mcid_spans
    from .text_export import _ContentSource, _element_text

    try:
        content = pdf.get_page_content(page_index)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return "", None, None
    if not content:
        return "", None, None
    limits = getattr(pdf, "_load_limits", None)
    budget = getattr(pdf, "_load_budget", None)
    spans = mcid_spans(content, limits=limits, budget=budget)
    wanted = [spans[mcid] for mcid in mcids if mcid in spans]
    if not wanted:
        return "", None, None
    elements = [
        element
        for element in find_layout_elements(content, limits=limits, budget=budget)
        if element.kind == "text"
        and any(start <= element.start < end for start, end in wanted)
    ]
    if not elements:
        return "", None, None
    source = _ContentSource(content, pdf._cos_page_resources(page_index))
    resources = source.text_resources(pdf)
    parts = [
        _element_text(content, element, resources, limits, budget)
        for element in elements
    ]
    text = " ".join(part.strip() for part in parts if part.strip()).strip()
    top = max(elements, key=lambda element: element.y)
    return text, top.x, top.y


def _from_structure(pdf: Any, max_level: int) -> list[Heading]:
    """Headings the structure tree names, in the tree's own reading order."""
    if pdf._cos_doc is None:
        return []
    from . import conformance

    root_data = pdf._tagged_struct_tree_root()
    if root_data is None:
        return []
    role_map = conformance._build_role_map(pdf, root_data[0])
    headings: list[Heading] = []

    def walk(elements: list[PdfDictionary], sections: int, depth: int) -> None:
        if depth > 64:
            return  # a tree that deep is damaged; the base checks say so
        for element in elements:
            kind = conformance._resolved_struct_type(
                pdf._tagged_element_type(element) or "", role_map
            )
            level = _heading_level(kind, sections)
            if level is not None and level <= max_level:
                marks = _element_marks(pdf, element)
                text = _structure_text(element, pdf)
                x = y = None
                page_index = marks[0][0] if marks else None
                if marks:
                    by_page: dict[int, list[int]] = {}
                    for page, mcid in marks:
                        by_page.setdefault(page, []).append(mcid)
                    found, x, y = _marked_content(
                        pdf, page_index, by_page[page_index]
                    )
                    if text is None:
                        text = found
                if page_index is None:
                    page_index = pdf._tagged_element_page_number(element)
                    page_index = None if page_index is None else page_index - 1
                if text and page_index is not None:
                    headings.append(
                        Heading(level, text, page_index, x, y, "structure")
                    )
            deeper = sections + (1 if kind in _SECTION_TYPES else 0)
            try:
                children = pdf._tagged_children(element)
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                continue
            walk(children, deeper, depth + 1)

    try:
        walk(pdf._tagged_root_elements(), 0, 0)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return headings
    return headings


# ---------------------------------------------------------------------------
# What the pages look like
# ---------------------------------------------------------------------------
def _from_layout(pdf: Any, max_level: int) -> list[Heading]:
    """Headings read off the page: the size tiers the exports infer.

    A heuristic, and the same one, so a bookmark tree and a Markdown export of
    the same document agree about what its headings are.
    """
    from .text_export import page_blocks

    headings: list[Heading] = []
    for index in range(len(pdf.pages)):
        try:
            blocks = page_blocks(pdf, index, include_images=False)
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            continue
        for block in blocks:
            if block.kind != "heading" or not block.text.strip():
                continue
            level = max(1, min(int(block.level or 1), max_level))
            headings.append(
                Heading(level, block.text.strip(), index, block.x, block.y, "layout")
            )
    return headings
