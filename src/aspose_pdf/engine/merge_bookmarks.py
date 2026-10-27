"""Plan bounded source-document sections for PDF merging."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget
from aspose_pdf.outlines import OutlineItem

from .cos import PdfArray, PdfName, encode_pdf_text_string

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _text(value: Any, budget: _LoadBudget, *, nonempty: bool = False) -> int:
    if not isinstance(value, str):
        raise TypeError("Bookmark titles must be strings")
    budget.check(len(value), "max_object_bytes", "merge bookmark title")
    if nonempty and not value.strip():
        raise PdfValidationException("Section bookmark titles must not be blank")
    try:
        size = len(encode_pdf_text_string(value))
    except UnicodeError as exc:
        raise PdfValidationException("Bookmark title contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", "merge bookmark title")
    return size


def snapshot_titles(
    create_bookmarks: bool,
    titles: Iterable[str] | None,
    limits: PdfLoadLimits,
    *,
    count: int | None = None,
) -> tuple[str, ...] | None:
    """Validate options and freeze titles before reading or importing inputs."""
    if not isinstance(create_bookmarks, bool):
        raise TypeError("create_bookmarks must be a boolean")
    if titles is None:
        return None
    if not create_bookmarks:
        raise PdfValidationException("bookmark_titles requires create_bookmarks=True")
    if isinstance(titles, (str, bytes, bytearray, Mapping)):
        raise TypeError("bookmark_titles must be an iterable of strings")
    budget = _LoadBudget(limits)
    copied: list[str] = []
    retained = 0
    for title in titles:
        budget.check(len(copied) + 1, "max_container_items", "merge bookmark titles")
        retained += _text(title, budget, nonempty=True)
        budget.check(retained, "max_total_decoded_bytes", "merge bookmark text")
        copied.append(title)
    if count is not None and len(copied) != count:
        raise PdfValidationException("bookmark_titles must contain one title per input")
    return tuple(copied)


def default_title(
    document: Document, number: int, source_name: str | Path | None = None
) -> str:
    """Prefer a file/stream name, then PDF title, then a numbered fallback."""
    name = source_name if source_name is not None else document.file_name
    if name is not None:
        stem = Path(name).stem
        if stem.strip():
            return stem
    title = document.info.get("Title", "")
    if isinstance(title, str) and title.strip():
        return title
    return f"Document {number}"


def plan(
    document: Document,
    sources: Sequence[Document],
    titles: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Check the resulting bookmark tree before changing the target document."""
    budget = _LoadBudget(document.load_limits)
    budget.check(len(sources), "max_container_items", "merge bookmark inputs")
    resolved = (
        titles
        if titles is not None
        else tuple(
            default_title(source, index + 1) for index, source in enumerate(sources)
        )
    )
    count = retained = 0
    pages = document.page_count

    def check_tree(owner: Document, depth: int) -> None:
        nonlocal count, retained
        roots = (
            owner._outlines._items
            if owner._outlines is not None
            else owner._engine_pdf._outlines_data
        )
        budget.check(len(roots), "max_container_items", "merge bookmark roots")
        stack = [(item, depth) for item in reversed(roots)]
        seen = set()
        while stack:
            item, level = stack.pop()
            if not isinstance(item, (OutlineItem, dict)):
                raise PdfValidationException("Invalid bookmark item")
            if id(item) in seen:
                raise PdfValidationException(
                    "Bookmark tree contains a cycle or repeated item"
                )
            seen.add(id(item))
            count += 1
            budget.check(count, "max_container_items", "merged bookmark items")
            budget.check(level, "max_nesting_depth", "merged bookmark depth")
            title = (
                item.title if isinstance(item, OutlineItem) else item.get("title", "")
            )
            retained += _text(title, budget)
            budget.check(retained, "max_total_decoded_bytes", "merge bookmark text")
            children = (
                item.children
                if isinstance(item, OutlineItem)
                else item.get("children", [])
            )
            if not isinstance(children, (list, tuple)):
                raise PdfValidationException(
                    "Bookmark children must be a list or tuple"
                )
            budget.check(
                len(stack) + len(children),
                "max_container_items",
                "merge bookmark stack",
            )
            stack.extend((child, level + 1) for child in reversed(children))

    check_tree(document, 1)
    for source, title in zip(sources, resolved):
        retained += _text(title, budget, nonempty=True)
        budget.check(retained, "max_total_decoded_bytes", "merge bookmark text")
        source_pages = source.page_count
        pages += source_pages
        budget.check_pages(pages)
        if source_pages:
            count += 1
            budget.check(count, "max_container_items", "merged bookmark items")
            budget.check(1, "max_nesting_depth", "merged bookmark depth")
        check_tree(source, 2 if source_pages else 1)
    return resolved


def group_imported(
    document: Document, title: str, first_page: int, previous_roots: int
) -> None:
    """Wrap imported roots while retaining their targets and page identities."""
    pdf = document._engine_pdf
    roots = pdf._outlines_data
    section = OutlineItem(title, first_page)._to_dict()
    section["loaded_target"] = (
        "Dest",
        PdfArray([pdf._make_page_ref(first_page), PdfName("Fit")]),
    )
    section["children"] = roots[previous_roots:]
    roots[previous_roots:] = [section]


def import_source(document: Document, source: Document) -> None:
    """Resolve live bookmark targets while retaining the source's bookmark views."""
    from .bookmark_split import _snapshot

    pdf = source._engine_pdf
    original, view = pdf._outlines_data, source._outlines
    roots = original if view is None else view._items
    snapshot, _ = _snapshot(pdf, roots, 1)
    try:
        pdf._outlines_data, source._outlines = snapshot, None
        document._import_pages_from(source)
    finally:
        pdf._outlines_data, source._outlines = original, view
