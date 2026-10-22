"""Detached page-pair results from extracted PDF text comparison."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PageTextComparison"]


@dataclass(frozen=True, slots=True)
class PageTextComparison:
    """An immutable comparison of two selected pages, using zero-based indices.

    ``text`` and ``other_text`` contain the original extracted strings. A
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
