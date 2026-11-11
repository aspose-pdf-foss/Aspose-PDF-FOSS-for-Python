"""Compose text search, reliable match geometry and text markup annotations."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable
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
        page = document._page_at(index)
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


def markup_texts(
    document: Document,
    patterns: dict[str | re.Pattern[str], dict[str, Any] | None],
    *,
    kind: str = "highlight",
    page_index: int | None = None,
    pages: Iterable[int] | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
    regex: bool = False,
) -> TextMarkupResult:
    """Mark multiple text patterns in a single call.

    This is a convenience method that applies multiple text markup patterns
    to the document. It is equivalent to calling :meth:`markup_text` for each
    pattern, but more efficient as it performs a single scan of the content
    streams per pattern.

    Parameters
    ----------
    patterns
        A dictionary mapping search patterns to options. Each key is a string
        or compiled regex pattern; each value is a dict of options for that
        pattern (color, opacity, contents, title, kind), or None for defaults.
        The ``kind`` option specifies the markup style for that pattern
        ('highlight', 'underline', 'strike_out', or 'squiggly'). When omitted,
        the global ``kind`` parameter is used.
    kind
        The default markup kind: 'highlight', 'underline', 'strike_out', or
        'squiggly'. Patterns without a ``kind`` option use this value.
    page_index
        One-based page number. If omitted and ``pages`` is also omitted,
        all pages are processed. Cannot be used together with ``pages``.
    pages
        Iterable of one-based page numbers to process. If omitted and
        ``page_index`` is also omitted, all pages are processed.
        Cannot be used together with ``page_index``.
    case_sensitive
        If True (default), matching is case-sensitive. Ignored for compiled
        regex patterns.
    max_count
        Maximum marks to perform across all patterns. 0 (default) means
        unlimited. The count is shared across all patterns.
    regex
        If True, all string patterns are treated as regular expressions.
        Not required for compiled re.Pattern keys.

    Returns
    -------
    TextMarkupResult
        A result object with annotations, skipped_fragments, marked_count,
        skipped_count, and matched_count.

    See Also
    --------
    markup_text : Mark a single text pattern.
    highlight_texts : Highlight multiple text patterns.

    Examples
    --------
    Mark multiple patterns with different colors:

    >>> document.markup_texts(
    ...     {"review": {"color": (1, 0, 0)}, "draft": {"color": (0, 1, 0)}},
    ...     kind="highlight"
    ... )

    Mix different markup kinds:

    >>> document.markup_texts(
    ...     {"review": {"kind": "highlight", "color": (1, 0, 0)},
    ...      "draft": {"kind": "underline", "color": (0, 1, 0)}}
    ... )

    Use with regex patterns:

    >>> import re
    >>> document.markup_texts(
    ...     {re.compile(r"\\d{4}"): {}, "Mr.": {"title": "Formal Title"}},
    ...     kind="underline",
    ...     regex=True
    ... )

    Limit marks and target specific pages:

    >>> document.markup_texts(
    ...     {"important": {}, "critical": {}},
    ...     pages=[1, 3],
    ...     max_count=5,
    ...     kind="strike_out"
    ... )
    """
    document._ensure_not_disposed()
    engine = document._engine_pdf
    if engine is None:
        raise AsposePdfException("No document loaded")
    if not isinstance(patterns, dict):
        raise TypeError("patterns must be a dictionary")

    if page_index is not None and pages is not None:
        raise PdfValidationException(
            "page_index and pages cannot be used together. "
            "Use page_index for a single page or pages for multiple pages."
        )

    # Build a list of (pattern_key, pattern_str, is_regex, options) tuples
    pattern_items: list[tuple[str | re.Pattern[str], str, bool, dict[str, Any]]] = []
    for search, opts in patterns.items():
        if opts is None:
            opts = {}
        if not isinstance(opts, dict):
            raise TypeError(f"Options for pattern must be a dict or None, got {type(opts)}")

        if isinstance(search, re.Pattern):
            pattern_str, search_regex = search.pattern, True
        elif isinstance(search, str):
            pattern_str, search_regex = search, regex
        else:
            raise TypeError(
                "pattern keys must be strings or compiled regular expressions"
            )

        pattern_items.append((search, pattern_str, search_regex, opts))

    # Validate kind and other parameters
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

    if page_index is not None and pages is not None:
        raise PdfValidationException(
            "page_index and pages cannot be used together. "
            "Use page_index for a single page or pages for multiple pages."
        )

    if page_index is not None:
        if isinstance(page_index, bool) or not isinstance(page_index, int):
            raise TypeError("page_index must be a zero-based integer")
        engine._validate_page_index(page_index)
        indexes = (page_index,)
    elif pages is not None:
        # pages is already an _EngineSelection from the decorator
        # Iterate over it to get zero-based indices
        indexes = tuple(pages)
    else:
        indexes = range(document.page_count)

    # Process each pattern and collect results
    all_annotations: list[
        HighlightAnnotation | UnderlineAnnotation | StrikeOutAnnotation | SquigglyAnnotation
    ] = []
    all_skipped: list[TextFragment] = []
    considered = 0

    for search, pattern_str, search_regex, opts in pattern_items:
        # Build the pattern with appropriate flags
        flags = 0 if search_regex else re.IGNORECASE if not case_sensitive else 0
        if isinstance(search, re.Pattern):
            flags = search.flags
        else:
            if not case_sensitive:
                flags |= re.IGNORECASE
        pattern = re.compile(pattern_str, flags)

        # Get per-pattern kind (falls back to the global kind parameter)
        pattern_kind = opts.get("kind", kind)
        if not isinstance(pattern_kind, str):
            raise TypeError("kind must be a string")
        if pattern_kind not in _MARKUP_METHODS:
            raise PdfValidationException(
                f"kind must be one of {list(_MARKUP_METHODS.keys())}, got {pattern_kind!r}"
            )

        # Get default color based on pattern_kind
        default_color = (1.0, 1.0, 0.0) if pattern_kind == "highlight" else (0.0, 0.0, 0.0)
        # Get options for this pattern
        pattern_color = opts.get("color", default_color)
        pattern_opacity = opts.get("opacity", 0.5)
        pattern_contents = opts.get("contents", "")
        pattern_title = opts.get("title")

        normalized_color = normalize_color(pattern_color)
        if isinstance(pattern_opacity, bool) or not isinstance(
            pattern_opacity, (int, float)
        ):
            raise TypeError("opacity must be a finite number between zero and one")
        if not math.isfinite(pattern_opacity) or not 0 <= pattern_opacity <= 1:
            raise PdfValidationException("opacity must be between zero and one")
        if not isinstance(pattern_contents, str) or (
            pattern_title is not None and not isinstance(pattern_title, str)
        ):
            raise TypeError(
                "contents and title must be strings; title may be None"
            )
        try:
            pattern_contents.encode("utf-16-be")
            if pattern_title is not None:
                pattern_title.encode("utf-16-be")
        except UnicodeError as exc:
            raise PdfValidationException(
                "Text markup contents and title must be valid Unicode"
            ) from exc

        for index in indexes:
            page = document._page_at(index)
            text = page.extract_text()
            fragments: list[TextFragment] = []
            by_text: dict[str, list[TextFragment]] = defaultdict(list)
            for match in pattern.finditer(text):
                if match.start() == match.end():
                    continue
                budget = _LoadBudget(document.load_limits)
                budget.check(
                    len(fragments) + 1, "max_container_items", "text markup page matches"
                )
                fragment = TextFragment(
                    index, match.group(), match.start(), match.end()
                )
                fragments.append(fragment)
                by_text[fragment.text].append(fragment)
            if not fragments:
                continue

            # Locate text in content stream
            located: dict[str, list[Any]] = defaultdict(list)
            for item in engine.locate_text(index, pattern):
                located[item.text].append(item)

            for value, group in by_text.items():
                places = located[value]
                if len(places) != len(group):
                    continue
                for fragment, item in zip(group, places):
                    fragment.rect = item.rect
                    fragment.quads = list(item.quads)
                    fragment.font_name = item.font_name
                    fragment.font_size = item.font_size
                    fragment.color = item.color

            for fragment in fragments:
                budget = _LoadBudget(document.load_limits)
                budget.check(
                    considered + 1,
                    "max_container_items",
                    "text markup result matches",
                )
                considered += 1
                if not fragment.quads:
                    all_skipped.append(fragment)
                else:
                    quads = [
                        (quad[3], quad[2], quad[0], quad[1]) for quad in fragment.quads
                    ]
                    all_annotations.append(
                        getattr(page.annotations, _MARKUP_METHODS[pattern_kind])(
                            quads,
                            pattern_contents,
                            color=normalized_color,
                            opacity=pattern_opacity,
                            title=pattern_title,
                        )
                    )
                if max_count and considered >= max_count:
                    return TextMarkupResult(
                        tuple(all_annotations), tuple(all_skipped)
                    )

    return TextMarkupResult(tuple(all_annotations), tuple(all_skipped))


def highlight_texts(
    document: Document,
    patterns: dict[str | re.Pattern[str], dict[str, Any] | None],
    *,
    page_index: int | None = None,
    pages: Iterable[int] | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
    regex: bool = False,
) -> TextHighlightResult:
    """Highlight multiple text patterns in a single call.

    This is a convenience method that applies multiple highlight patterns
    to the document. It is equivalent to calling :meth:`highlight_text`
    for each pattern, but more efficient as it performs a single scan
    of the content streams per pattern.

    Parameters
    ----------
    patterns
        A dictionary mapping search patterns to options. Each key is a string
        or compiled regex pattern; each value is a dict of options for that
        pattern (color, opacity, contents, title). The color defaults to
        yellow for highlights if not specified.
    page_index
        One-based page number. If omitted and ``pages`` is also omitted,
        all pages are processed. Cannot be used together with ``pages``.
    pages
        Iterable of one-based page numbers to process. If omitted and
        ``page_index`` is also omitted, all pages are processed.
        Cannot be used together with ``page_index``.
    case_sensitive
        If True (default), matching is case-sensitive. Ignored for compiled
        regex patterns.
    max_count
        Maximum highlights to perform across all patterns. 0 (default) means
        unlimited. The count is shared across all patterns.
    regex
        If True, all string patterns are treated as regular expressions.
        Not required for compiled :class:`re.Pattern` keys.

    Returns
    -------
    TextHighlightResult
        A result object with highlights, skipped_fragments, highlighted_count,
        skipped_count, and matched_count.

    See Also
    --------
    highlight_text : Highlight a single text pattern.
    markup_texts : Mark multiple text patterns with different styles.

    Examples
    --------
    Highlight multiple patterns:

    >>> document.highlight_texts({"review": {}, "approved": {}})

    Use with regex patterns:

    >>> import re
    >>> document.highlight_texts(
    ...     {re.compile(r"\\d{4}"): {"color": (1, 0, 0)}, "Mr.": {}},
    ...     regex=True
    ... )

    Limit highlights and target specific pages:

    >>> document.highlight_texts(
    ...     {"important": {}, "critical": {}},
    ...     pages=[1, 3],
    ...     max_count=5
    ... )
    """
    if page_index is not None and pages is not None:
        raise PdfValidationException(
            "page_index and pages cannot be used together. "
            "Use page_index for a single page or pages for multiple pages."
        )

    result = markup_texts(
        document,
        patterns,
        kind="highlight",
        page_index=page_index,
        pages=pages,
        case_sensitive=case_sensitive,
        max_count=max_count,
        regex=regex,
    )
    return TextHighlightResult(
        cast("tuple[HighlightAnnotation, ...]", result.annotations),
        result.skipped_fragments,
    )
