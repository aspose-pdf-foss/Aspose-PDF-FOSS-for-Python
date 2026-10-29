"""Delete inspected pages while preserving retained navigation and live outlines."""

from __future__ import annotations

from bisect import bisect_left
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def remove_pages(document: Document, indexes: list[int]) -> None:
    """Delete known original indices after validating labels and bookmark targets."""
    from .bookmark_split import _snapshot
    from .text_split import _refresh_targets

    removed = set(indexes)
    if not removed:
        return
    if len(removed) == document.page_count:
        raise PdfValidationException("Removing every page would leave an empty document")

    pdf = document._engine_pdf
    # Validate label metadata before deletion can change the page list.
    pdf.page_label_ranges()
    roots = pdf._outlines_data if document._outlines is None else document._outlines._items
    outlines, _starts = _snapshot(pdf, roots, 1) if roots else ([], {})
    descending = sorted(removed, reverse=True)
    ascending = descending[::-1]
    stack = list(outlines)
    while stack:
        item = stack.pop()
        index = item["page_index"]
        if index in removed:
            item.update(page_index=None, target=None, loaded_target=None)
        elif index is not None:
            item["page_index"] = index - bisect_left(ascending, index)
        stack.extend(item["children"])

    live = []
    if document._outlines is not None:
        pending = list(zip(document._outlines._items, outlines, strict=True))
        while pending:
            item, entry = pending.pop()
            live.append((item, entry))
            pending.extend(zip(item.children, entry["children"], strict=True))
    for index in descending:
        document.pages.delete(index)
    pdf._outlines_data = outlines
    _refresh_targets(pdf)
    for item, entry in live:
        item._page_index = entry["page_index"]
        item._destination = entry.get("target")
        item._loaded_target = entry.get("loaded_target")
    pdf.free_memory()
    pdf._page_text_cursor = 0
