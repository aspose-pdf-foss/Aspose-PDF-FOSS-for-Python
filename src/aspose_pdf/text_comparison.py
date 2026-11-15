"""Detached page-pair results from extracted PDF text comparison."""

from __future__ import annotations

from dataclasses import dataclass

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["PageTextComparison"]


@dataclass(frozen=True, slots=True)
class PageTextComparison:
    """An immutable comparison of two selected pages, using one-based page numbers.

    ``text`` and ``other_text`` contain extracted strings after any exclusion
    filtering and before case or whitespace normalization. A
    missing page has a ``None`` index and text, distinct from a blank page's
    empty string. Missing pages never match, even when the other page is blank.

    ``matches`` compares text after the requested whitespace/case handling.
    ``unified_diff`` describes those same normalized strings with three lines
    of context, using one-based page numbers in its headers. It is empty for
    matches. A missing blank page produces headers alone. LF separates diff
    lines; missing final LF is marked explicitly. Reports remain usable after
    either document closes, without retaining document or page objects.
    """

    page_index: int | None
    other_page_index: int | None
    text: str | None
    other_text: str | None
    matches: bool
    unified_diff: str

    def __post_init__(self) -> None:
        """Validate that page_index and other_page_index are None or positive integers."""
        for name, value in (
            ("page_index", self.page_index),
            ("other_page_index", self.other_page_index),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise PdfValidationException(
                    f"{name} must be an integer, got {type(value).__name__}"
                )
            if value < 1:
                raise PdfValidationException(
                    f"{name} must be a positive integer, got {value}"
                )
