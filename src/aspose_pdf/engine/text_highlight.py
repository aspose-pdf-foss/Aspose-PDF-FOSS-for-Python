"""Compose text search, reliable match geometry and text markup annotations."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

from aspose_pdf.color import ColorValue
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.text import TextFragment, TextHighlightResult, TextMarkupResult

from .content_authoring import normalize_color

if TYPE_CHECKING:
    from aspose_pdf.annotations import (
        HighlightAnnotation,
        SquigglyAnnotation,
        StrikeOutAnnotation,
        UnderlineAnnotation,
    )
    from aspose_pdf.document import Document


_MARKUP_METHODS = {
    "highlight": "add_highlight",
    "underline": "add_underline",
    "strike_out": "add_strike_out",
    "squiggly": "add_squiggly",
}


def markup_text(
    document: Document,
    search: str | re.Pattern[str],
    *,
    kind: str = "highlight",
    page_index: int | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
    regex: bool = False,
    color: ColorValue | None = None,
    opacity: float = 0.5,
    contents: str = "",
    title: str | None = None,
) -> TextMarkupResult:
    """Mark non-empty matches when extraction and geometry agree."""
    document._ensure_not_disposed()
    engine = document._engine_pdf
    if engine is None:
        raise AsposePdfException("No document loaded")
    if not isinstance(kind, str):
        raise TypeError("kind must be a string")
    if kind not in _MARKUP_METHODS:
        raise PdfValidationException(
            "kind must be 'highlight', 'underline', 'strike_out', or 'squiggly'"
        )
    if not isinstance(regex, bool) or not isinstance(case_sensitive, bool):
        raise TypeError("regex and case_sensitive must be booleans")
    if isinstance(max_count, bool) or not isinstance(max_count, int):
        raise TypeError("max_count must be a non-negative integer")
    if max_count < 0:
        raise ValueError("max_count must be greater than or equal to zero")
    if isinstance(search, re.Pattern):
        if not isinstance(search.pattern, str):
            raise TypeError("search must be a text regular expression")
        source, flags = search.pattern, search.flags
    elif isinstance(search, str):
        source, flags = search if regex else re.escape(search), 0
    else:
        raise TypeError("search must be a string or a compiled regular expression")
    if not source:
        raise ValueError("search must not be empty")
    pattern = re.compile(source, flags | (0 if case_sensitive else re.IGNORECASE))
    default_color = (1.0, 1.0, 0.0) if kind == "highlight" else (0.0, 0.0, 0.0)
    normalized_color = normalize_color(default_color if color is None else color)
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
        raise TypeError("opacity must be a finite number between zero and one")
    if not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise PdfValidationException("opacity must be between zero and one")
    if not isinstance(contents, str) or (
        title is not None and not isinstance(title, str)
    ):
        raise TypeError("contents and title must be strings; title may be None")
    try:
        contents.encode("utf-16-be")
        if title is not None:
            title.encode("utf-16-be")
    except UnicodeError as exc:
        raise PdfValidationException(
            "Text markup contents and title must be valid Unicode"
        ) from exc
    if page_index is None:
        indexes = range(document.page_count)
    else:
        if isinstance(page_index, bool) or not isinstance(page_index, int):
            raise TypeError("page_index must be a zero-based integer")
        engine._validate_page_index(page_index)
        indexes = (page_index,)

    budget = _LoadBudget(document.load_limits)
    annotations: list[
        HighlightAnnotation | UnderlineAnnotation | StrikeOutAnnotation | SquigglyAnnotation
    ] = []
    skipped: list[TextFragment] = []
    considered = 0
    for index in indexes:
        page = document.pages[index]
        text = page.extract_text()
        fragments: list[TextFragment] = []
        by_text: dict[str, list[TextFragment]] = defaultdict(list)
        for match in pattern.finditer(text):
            if match.start() == match.end():
                continue
            budget.check(
                len(fragments) + 1, "max_container_items", "text markup page matches"
            )
            fragment = TextFragment(index, match.group(), match.start(), match.end())
            fragments.append(fragment)
            by_text[fragment.text].append(fragment)
        if not fragments:
            continue
        located: dict[str, list[Any]] = defaultdict(list)
        for item in engine.locate_text(index, pattern):
            located[item.text].append(item)
        for value, group in by_text.items():
            places = located[value]
            # Counts must agree for each matched string. A form XObject or an
            # unmeasurable repeated occurrence must not lend another match its
            # coordinates. Matching the original regex preserves its context.
            if len(places) != len(group):
                continue
            for fragment, item in zip(group, places):
                fragment.rect = item.rect
                fragment.quads = list(item.quads)
                fragment.font_name = item.font_name
                fragment.font_size = item.font_size
                fragment.color = item.color
        for fragment in fragments:
            budget.check(
                considered + 1, "max_container_items", "text markup result matches"
            )
            considered += 1
            if not fragment.quads:
                skipped.append(fragment)
            else:
                # The locator's corners are anticlockwise from the lower-left;
                # annotation QuadPoints use upper-left/right, lower-left/right.
                quads = [
                    (quad[3], quad[2], quad[0], quad[1]) for quad in fragment.quads
                ]
                annotations.append(
                    getattr(page.annotations, _MARKUP_METHODS[kind])(
                        quads,
                        contents,
                        color=normalized_color,
                        opacity=opacity,
                        title=title,
                    )
                )
            if max_count and considered >= max_count:
                return TextMarkupResult(tuple(annotations), tuple(skipped))
    return TextMarkupResult(tuple(annotations), tuple(skipped))


def highlight_text(
    document: Document,
    search: str | re.Pattern[str],
    *,
    page_index: int | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
    regex: bool = False,
    color: ColorValue = (1.0, 1.0, 0.0),
    opacity: float = 0.5,
    contents: str = "",
    title: str | None = None,
) -> TextHighlightResult:
    """Keep the highlight-only entry point and its original result type."""
    result = markup_text(
        document,
        search,
        page_index=page_index,
        case_sensitive=case_sensitive,
        max_count=max_count,
        regex=regex,
        color=color,
        opacity=opacity,
        contents=contents,
        title=title,
    )
    return TextHighlightResult(
        cast("tuple[HighlightAnnotation, ...]", result.annotations),
        result.skipped_fragments,
    )
