"""Build a table of contents page from the document's own headings.

:mod:`~aspose_pdf.engine.headings` already answers *what the headings are and
where*, which is what a bookmark tree is made of. A contents **page** is the
same answer written onto paper: the headings down the page, indented by level,
each with the page it is on and a link to it.

The one thing a bookmark tree never has to deal with is that **the page
numbers move**. Putting the contents at the front pushes every page it points
at further back, so the numbers cannot be read off the document as it stands.
They are not circular, though: how many lines there are does not depend on what
the numbers say, so the page count is settled first, the pages are inserted,
and only then is each heading's new place worked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS

from .headings import Heading, find_headings

__all__ = ["Entry", "build", "plan"]

#: Line height as a multiple of the entry font size.
LINE_HEIGHT = 1.6
#: Space between an entry's text and its leader dots, and between them and the
#: page number, in points. Without it the dots touch the words.
LEADER_GAP = 4.0
#: What a truncated entry ends with.
ELLIPSIS = "..."


@dataclass(frozen=True)
class Entry:
    """One line of the contents: what it says, and where it points."""

    text: str
    level: int
    #: The page the heading is on, **after** the contents pages were inserted.
    page_index: int
    #: The number as a reader sees it: the page's label where the document has
    #: labels, and its position otherwise.
    label: str
    #: The heading's own place on its page, for a link that lands on it rather
    #: than on the top of the sheet.
    x: float | None
    y: float | None


@dataclass(frozen=True)
class Plan:
    """What the contents will be, before anything is written."""

    headings: tuple[Heading, ...]
    #: How many pages the entries and the title need.
    page_count: int
    #: Lines that fit on the first page (which carries the title) and after.
    first_page_lines: int
    other_page_lines: int
    size: tuple[float, float]


def plan(
    pdf: Any,
    *,
    max_level: int = 3,
    prefer_structure: bool = True,
    at: int = 0,
    size: tuple[float, float] | None = None,
    margin: float = 72.0,
    title: str | None = "Contents",
    title_size: float = 20.0,
    entry_size: float = 11.0,
) -> Plan:
    """Work out how many pages the contents needs, and on what sheet.

    Nothing is written. The caller inserts that many pages and hands the plan
    back to :func:`draw`, which is what keeps the page numbers right: they can
    only be known once the pages that shift them exist.
    """
    headings = tuple(
        find_headings(pdf, max_level=max_level, prefer_structure=prefer_structure)
    )
    sheet = size or _sheet_of(pdf, at)
    usable = max(0.0, sheet[1] - 2.0 * margin)
    line = entry_size * LINE_HEIGHT
    other = max(1, int(usable // line)) if line > 0 else 1
    # The title takes its own line and the space under it.
    title_block = (title_size * LINE_HEIGHT * 2.0) if title else 0.0
    first = max(1, int(max(0.0, usable - title_block) // line)) if line > 0 else 1
    if not headings:
        return Plan(headings, 0, first, other, sheet)
    remaining = len(headings) - first
    pages = 1 + (0 if remaining <= 0 else -(-remaining // other))
    return Plan(headings, pages, first, other, sheet)


def entries_for(pdf: Any, planned: Plan, *, at: int) -> list[Entry]:
    """The plan's headings as lines, with the pages they now land on.

    A heading on a page at or after *at* has been pushed back by however many
    pages the contents took; one before it has not moved. Both happen: contents
    usually go at the front, but a report with a cover page puts them second.
    """
    out: list[Entry] = []
    for heading in planned.headings:
        index = heading.page_index
        moved = index + planned.page_count if index >= at else index
        label = pdf.page_label(moved)
        out.append(
            Entry(
                text=heading.text,
                level=heading.level,
                page_index=moved,
                label=label if label else str(moved + 1),
                x=heading.x,
                y=heading.y,
            )
        )
    return out


def draw(
    pdf: Any,
    document: Any,
    planned: Plan,
    entries: list[Entry],
    *,
    at: int,
    title: str | None,
    margin: float,
    font_name: str,
    title_size: float,
    entry_size: float,
    indent: float,
    leader: str,
    links: bool,
    page_numbers: bool,
    tag: bool,
) -> None:
    """Write the contents onto the pages already inserted at *at*."""
    from .text_faces import _StandardFace

    face = _StandardFace(pdf, font_name)
    before = _structure_count(document) if tag else 0
    width, height = planned.size
    right = width - margin
    index = 0
    for offset in range(planned.page_count):
        page = document.pages[at + offset]
        y = height - margin
        if offset == 0 and title:
            page.add_text(
                title,
                margin,
                y - title_size,
                font_size=title_size,
                font_name=font_name,
                tag="H1" if tag else None,
            )
            y -= title_size * LINE_HEIGHT * 2.0
        capacity = planned.first_page_lines if offset == 0 else planned.other_page_lines
        for entry in entries[index : index + capacity]:
            y -= entry_size * LINE_HEIGHT
            _draw_entry(
                page,
                entry,
                face=face,
                left=margin + indent * (entry.level - 1),
                right=right,
                baseline=y,
                font_name=font_name,
                entry_size=entry_size,
                leader=leader,
                links=links,
                page_numbers=page_numbers,
                tag=tag,
            )
        index += capacity
    if tag:
        _nest_under_toc(document, before)


def _structure_count(document: Any) -> int:
    """How many elements the structure tree's root holds, before drawing."""
    try:
        return len(document.tagged_content.root_elements)
    except PDF_OPERATION_ERRORS:
        return 0


def _nest_under_toc(document: Any, before: int) -> None:
    """Put the entries just written inside a ``/TOC``, where they belong.

    ISO 32000-1 table 333: a ``/TOCI`` is an item *of* a table of contents, so
    a row of them at the root is not a table of contents -- it is a row of
    orphans. The elements the drawing added are the ones past where the tree
    ended before it started.
    """
    try:
        roots = document.tagged_content.root_elements
        positions = [
            index
            for index in range(before, len(roots))
            if roots[index].structure_type == "TOCI"
        ]
        if not positions:
            return
        added = [roots[index] for index in positions]
        # Where the entries start, not where the drawing did: the title was
        # written first and is an /H1 of its own, which belongs *before* the
        # list rather than after it.
        container = document.tagged_content.add_element("TOC", index=positions[0])
        for element in added:
            document.tagged_content.move(element, parent=container)
    except PDF_OPERATION_ERRORS:
        # A tree this cannot be attached to leaves the entries where they are:
        # flat TOCIs are poorer tagging than nested ones and better than none.
        return


def _draw_entry(
    page: Any,
    entry: Entry,
    *,
    face: Any,
    left: float,
    right: float,
    baseline: float,
    font_name: str,
    entry_size: float,
    leader: str,
    links: bool,
    page_numbers: bool,
    tag: bool,
) -> None:
    """One line: the heading, the leader dots, the number, and the link."""
    number = entry.label if page_numbers else ""
    number_width = face.width(number, entry_size) if number else 0.0
    available = right - left - number_width - (2.0 * LEADER_GAP if number else 0.0)
    text = _fit(face, entry.text, available, entry_size)
    page.add_text(
        text,
        left,
        baseline,
        font_size=entry_size,
        font_name=font_name,
        tag="TOCI" if tag else None,
    )
    text_width = face.width(text, entry_size)

    if number:
        page.add_text(
            number,
            right - number_width,
            baseline,
            font_size=entry_size,
            font_name=font_name,
        )
        if leader:
            dots = _leader(
                face,
                leader,
                left + text_width + LEADER_GAP,
                right - number_width - LEADER_GAP,
                entry_size,
            )
            if dots:
                page.add_text(
                    dots[0],
                    dots[1],
                    baseline,
                    font_size=entry_size,
                    font_name=font_name,
                )
    if links:
        from aspose_pdf.interactive import FitHDestination, XYZDestination

        target = (
            XYZDestination(page=entry.page_index, left=entry.x, top=entry.y)
            if entry.y is not None
            else FitHDestination(page=entry.page_index)
        )
        # The whole line is the link, as a reader expects -- the number as much
        # as the words, and the dots between them.
        page.add_link(
            (left, baseline - entry_size * 0.25, right, baseline + entry_size),
            target,
        )


def _fit(face: Any, text: str, available: float, size: float) -> str:
    """*text*, shortened until it fits *available* points.

    A heading longer than the line would otherwise run under its own page
    number and come out unreadable.
    """
    text = " ".join(text.split())
    if available <= 0:
        return ""
    if face.width(text, size) <= available:
        return text
    ellipsis_width = face.width(ELLIPSIS, size)
    if ellipsis_width > available:
        return ""
    cut = text
    while cut and face.width(cut, size) + ellipsis_width > available:
        cut = cut[:-1]
    return (cut.rstrip() + ELLIPSIS) if cut.strip() else ""


def _leader(
    face: Any, leader: str, start: float, end: float, size: float
) -> tuple[str, float] | None:
    """``(dots, x)`` filling *start* to *end*, right-aligned against the number.

    Right-aligned so the dots under one entry line up with the dots under the
    next, which is what makes a contents page look like one rather than like a
    list of rows that happen to have dots.
    """
    span = end - start
    unit = face.width(leader, size)
    if span <= 0 or unit <= 0:
        return None
    count = int(span // unit)
    if count <= 0:
        return None
    run = leader * count
    return run, end - face.width(run, size)


def _sheet_of(pdf: Any, at: int) -> tuple[float, float]:
    """The size to make the contents, taken from the page it goes in front of.

    A contents page the size of a different document's paper is nobody's
    intention; US Letter is the fallback the rest of the package uses when
    there is no page to ask.
    """
    count = len(pdf.pages)
    if count:
        index = min(max(0, at), count - 1)
        box = pdf._page_media_box(index)
        if box:
            width = abs(box[2] - box[0])
            height = abs(box[3] - box[1])
            if width > 0 and height > 0:
                return (width, height)
    return (612.0, 792.0)


def build(
    pdf: Any,
    document: Any,
    *,
    title: str | None = "Contents",
    max_level: int = 3,
    prefer_structure: bool = True,
    at: int = 0,
    size: tuple[float, float] | None = None,
    margin: float = 72.0,
    font_name: str = "Helvetica",
    title_size: float = 20.0,
    entry_size: float = 11.0,
    indent: float = 18.0,
    leader: str = ".",
    links: bool = True,
    page_numbers: bool = True,
    tag: bool = False,
) -> list[int]:
    """Insert a contents at *at* and return the indexes of the pages it took."""
    planned = plan(
        pdf,
        max_level=max_level,
        prefer_structure=prefer_structure,
        at=at,
        size=size,
        margin=margin,
        title=title,
        title_size=title_size,
        entry_size=entry_size,
    )
    if not planned.page_count:
        return []
    for offset in range(planned.page_count):
        document.pages.insert(at + offset, size=planned.size)
    entries = entries_for(pdf, planned, at=at)
    draw(
        pdf,
        document,
        planned,
        entries,
        at=at,
        title=title,
        margin=margin,
        font_name=font_name,
        title_size=title_size,
        entry_size=entry_size,
        indent=indent,
        leader=leader,
        links=links,
        page_numbers=page_numbers,
        tag=tag,
    )
    return list(range(at, at + planned.page_count))
