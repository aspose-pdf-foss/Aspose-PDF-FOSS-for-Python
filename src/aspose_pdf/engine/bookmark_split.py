"""Plan chapter boundaries from current outline targets, then import each range."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from typing import Any

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.interactive import Destination, GoToAction
from aspose_pdf.outlines import OutlineItem

from .cos import PdfArray, PdfIndirectReference, PdfName


def validate_options(level: int, include_preamble: bool) -> None:
    """Reject invalid options before reading or changing any document state."""
    if not isinstance(level, int) or isinstance(level, bool):
        raise TypeError("bookmark level must be an integer")
    if level < 1:
        raise ValueError("bookmark level must be at least 1")
    if not isinstance(include_preamble, bool):
        raise TypeError("include_preamble must be a boolean")


def _page_index(value: Any, count: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count:
        return value
    return None


def _snapshot(pdf: Any, roots: list, level: int) -> tuple[list[dict], dict[int, str]]:
    """Bound the whole tree and resolve targets without trusting cached indices."""
    from .simple_pdf import loaded_target_page_reference

    budget = pdf._load_budget
    budget.check(len(roots), "max_container_items", "bookmark split roots")
    pdf._ensure_cos()
    pages = {
        number: index for index, number in enumerate(pdf._page_reference_numbers())
    }
    named = pdf._named_destinations()
    snapshot: list[dict] = []
    starts: dict[int, str] = {}
    seen = set()
    stack = [(item, snapshot, 1) for item in reversed(roots)]
    while stack:
        item, output, depth = stack.pop()
        budget.check(depth, "max_nesting_depth", "bookmark split depth")
        if not isinstance(item, (OutlineItem, dict)):
            raise PdfParseException("Invalid bookmark item")
        if id(item) in seen:
            raise PdfParseException("Bookmark tree contains a cycle or repeated item")
        budget.check(len(seen) + 1, "max_container_items", "bookmark split items")
        seen.add(id(item))
        if isinstance(item, OutlineItem):
            entry = {
                "title": item.title,
                "page_index": item.page_index,
                "target": item.destination,
                "loaded_target": item._loaded_target,
                "is_bold": item.is_bold,
                "is_italic": item.is_italic,
                "color": item.color,
                "open": item.open,
            }
            children = item.children
        else:
            entry = dict(item)
            children = item.get("children", [])
        title = entry.get("title", "")
        if not isinstance(title, str):
            raise TypeError("Bookmark titles must be strings")
        budget.check(len(title), "max_object_bytes", "bookmark split title")
        try:
            title_bytes = title.encode("utf-16-be")
        except UnicodeError as exc:
            raise PdfValidationException(
                "Bookmark title contains invalid Unicode"
            ) from exc
        budget.check(len(title_bytes), "max_object_bytes", "bookmark split title")
        if not isinstance(children, (list, tuple)):
            raise PdfParseException("Bookmark children must be a list or tuple")
        budget.check(len(children), "max_container_items", "bookmark split children")
        loaded = entry.get("loaded_target")
        target = entry.get("target")
        index = None
        if loaded is None and target is not None:
            local = target.destination if isinstance(target, GoToAction) else target
            if (
                isinstance(local, Destination)
                and _page_index(local.page, len(pages)) is None
            ):
                loaded = None
            else:
                raw, key = pdf._interactive_target_cos(target)
                loaded = (key, raw)
        elif loaded is None:
            index = _page_index(entry.get("page_index"), len(pages))
            if index is not None:
                loaded = ("Dest", PdfArray([pdf._make_page_ref(index), PdfName("Fit")]))
        if loaded is not None:
            ref = loaded_target_page_reference(loaded, pdf._resolve, named)
            if isinstance(ref, PdfIndirectReference) and pdf._resolve(ref) is not None:
                index = pages.get(ref.object_number)
        entry.update(page_index=index, loaded_target=loaded, target=None, children=[])
        output.append(entry)
        if depth == level and index is not None:
            # Several section headings can share one page. That page belongs to
            # one output, named by the first such bookmark in outline order.
            starts.setdefault(index, title)
        budget.check(
            len(stack) + len(children), "max_container_items", "bookmark split stack"
        )
        stack.extend(
            (child, entry["children"], depth + 1) for child in reversed(children)
        )
    return snapshot, starts


def _range_outlines(items: list[dict], start: int, stop: int) -> list[dict]:
    """Keep local targets in this chapter, promoting children of excluded parents."""
    output: list[dict] = []
    stack = [(item, output) for item in reversed(items)]
    while stack:
        item, destination = stack.pop()
        page = item["page_index"]
        if page is None or start <= page < stop:
            copied = dict(item, children=[])
            destination.append(copied)
            destination = copied["children"]
        stack.extend((child, destination) for child in reversed(item["children"]))
    return output


def split_by_bookmarks(
    pdf: Any,
    *,
    level: int = 1,
    include_preamble: bool = True,
    outlines: list | None = None,
) -> Iterator[Any]:
    """Yield independent chapter PDFs; restore the source before every yield."""
    validate_options(level, include_preamble)
    pdf._ensure_not_disposed()
    pdf._load_budget.check(level, "max_nesting_depth", "bookmark split level")
    roots = pdf._outlines_data if outlines is None else outlines
    snapshot, starts = _snapshot(pdf, roots, level)
    if not starts:
        raise PdfValidationException(f"No local bookmark destinations at level {level}")
    boundaries = sorted(starts)
    if include_preamble and boundaries[0] > 0:
        starts[0] = "Preamble"
        boundaries.insert(0, 0)
    boundaries.append(len(pdf.pages))
    for start, stop in pairwise(boundaries):
        original = pdf._outlines_data
        try:
            # The shared page importer remaps destination references, fonts,
            # widgets and layers. Supplying a chapter's tree also retains its
            # bookmarks when their ancestors point outside this page range.
            pdf._outlines_data = _range_outlines(snapshot, start, stop)
            result = pdf.extract_pages(range(start, stop))
        finally:
            pdf._outlines_data = original
        result.metadata["Title"] = starts[start]
        yield result
