"""Compare selected page text without filling full-document extraction caches."""

from __future__ import annotations

from collections.abc import Iterable
from difflib import unified_diff
from itertools import zip_longest
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _combined_limits, _LoadBudget
from aspose_pdf.text_comparison import PageTextComparison

from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _size(text: str, budget: _LoadBudget) -> int:
    """Bound string encoding as well as the retained UTF-8 representation."""
    budget.check(len(text), "max_object_bytes", "text comparison string")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise PdfValidationException("Compared text contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", "text comparison string")
    return size


def _normalize(text: str, case_sensitive: bool, ignore_whitespace: bool) -> str:
    """Collapse Unicode whitespace runs, then apply Unicode case folding."""
    if ignore_whitespace:
        text = " ".join(text.split())
    return text if case_sensitive else text.casefold()


def _lines(text: str) -> list[str]:
    """Split on LF alone, preserving content and the presence of a final LF."""
    pieces = text.split("\n")
    return [piece + "\n" for piece in pieces[:-1]] + (
        [pieces[-1]] if pieces[-1] else []
    )


def _diff(
    left: str,
    right: str,
    index: int | None,
    other_index: int | None,
    budget: _LoadBudget,
    retained: int,
) -> tuple[str, int]:
    """Build a bounded line diff, including page-presence-only differences."""
    line_count = sum(
        text.count("\n") + bool(text and not text.endswith("\n"))
        for text in (left, right)
    )
    budget.check(line_count, "max_container_items", "text comparison diff lines")
    before = f"page-{index + 1}" if index is not None else "missing"
    after = f"other-page-{other_index + 1}" if other_index is not None else "missing"
    chunks = unified_diff(_lines(left), _lines(right), fromfile=before, tofile=after)
    output = []
    size = 0
    for chunk in chunks:
        if not chunk.endswith("\n"):
            chunk += "\n\\ No newline at end of text\n"
        size += _size(chunk, budget)
        budget.check(size, "max_object_bytes", "text comparison diff")
        budget.check(
            retained + size, "max_total_decoded_bytes", "text comparison reports"
        )
        output.append(chunk)
    if not output:
        # Equal empty strings can still represent a missing page on one side.
        output = [f"--- {before}\n+++ {after}\n"]
        size = _size(output[0], budget)
        budget.check(
            retained + size, "max_total_decoded_bytes", "text comparison reports"
        )
    return "".join(output), size


def compare_text(
    document: Document,
    other: Document,
    *,
    pages: Iterable[int] | None = None,
    other_pages: Iterable[int] | None = None,
    case_sensitive: bool = True,
    ignore_whitespace: bool = False,
    skip_artifacts: bool = False,
) -> list[PageTextComparison]:
    """Validate both full selections, then extract and compare one pair at a time."""
    for name, value in (
        ("case_sensitive", case_sensitive),
        ("ignore_whitespace", ignore_whitespace),
        ("skip_artifacts", skip_artifacts),
    ):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    budget = _LoadBudget(_combined_limits(document.load_limits, other.load_limits))
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    other_selection = page_indexes(other_pages, other.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    other_selection = (
        range(other.page_count) if other_selection is None else other_selection
    )
    pairs = max(len(selection), len(other_selection))
    budget.check(pairs, "max_container_items", "text comparison page pairs")
    budget.check_pages(pairs)
    results = []
    retained = 0
    for index, other_index in zip_longest(selection, other_selection):
        texts = []
        normalized = []
        for source, page in ((document, index), (other, other_index)):
            text = (
                source._engine_pdf.extract_page_text(
                    page, skip_artifacts=skip_artifacts
                )
                if page is not None
                else None
            )
            if text is not None:
                retained += _size(text, budget)
                budget.check(
                    retained, "max_total_decoded_bytes", "text comparison reports"
                )
            texts.append(text)
            value = _normalize(text or "", case_sensitive, ignore_whitespace)
            _size(value, budget)
            normalized.append(value)
        matches = (
            index is not None
            and other_index is not None
            and normalized[0] == normalized[1]
        )
        difference = ""
        if not matches:
            difference, size = _diff(*normalized, index, other_index, budget, retained)
            retained += size
        results.append(
            PageTextComparison(index, other_index, *texts, matches, difference)
        )
    return results
