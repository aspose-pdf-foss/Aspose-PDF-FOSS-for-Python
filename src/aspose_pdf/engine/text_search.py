"""Read-only search snapshots and bounded JSON/CSV reports with context."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.text import TextFragment, TextFragmentAbsorber, TextSearchMatch

from .csv_export import CsvWriter
from .link_export import _json_tokens
from .stream_output import write_all
from .text_selection import page_indexes
from .text_stream import iter_text_pages

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _text_size(text: str, budget: _LoadBudget) -> int:
    budget.check(len(text), "max_object_bytes", "search text")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise PdfValidationException("Search text contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", "search text")
    return size


def _matcher(pattern, regex, case_sensitive, context_chars, budget):
    for name, value in (("regex", regex), ("case_sensitive", case_sensitive)):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    if (
        isinstance(context_chars, bool)
        or not isinstance(context_chars, int)
        or context_chars < 0
    ):
        raise PdfValidationException("context_chars must be a non-negative integer")
    flags = 0
    if isinstance(pattern, re.Pattern):
        source, flags = pattern.pattern, pattern.flags
        regex = True
    else:
        source = pattern
    if not isinstance(source, str) or not source:
        raise PdfValidationException("pattern must be nonempty text or a text regex")
    _text_size(source, budget)
    if not regex:
        source = re.escape(source)
        _text_size(source, budget)
    if not case_sensitive:
        flags |= re.IGNORECASE
    try:
        return re.compile(source, flags), regex
    except (re.error, ValueError, OverflowError) as exc:
        raise PdfValidationException(f"Invalid search pattern: {exc}") from exc


def _matches(matcher: re.Pattern[str], text: str, regex: bool) -> Iterator[re.Match]:
    if regex:
        yield from matcher.finditer(text)
    else:
        position = 0
        while (match := matcher.search(text, position)) is not None:
            yield match
            position = match.start() + 1


def search(
    document: Document,
    pattern: str | re.Pattern[str],
    *,
    pages: Iterable[int] | None,
    regex: bool,
    case_sensitive: bool,
    context_chars: int,
) -> list[TextSearchMatch]:
    """Match selected extracted pages and reuse the absorber's placement rules."""
    from aspose_pdf.pages import Page

    budget = _LoadBudget(document.load_limits)
    matcher, regex = _matcher(pattern, regex, case_sensitive, context_chars, budget)
    indexes = page_indexes(pages, document.page_count, limits=document.load_limits)
    indexes = range(document.page_count) if indexes is None else indexes
    texts = iter_text_pages(
        document, pages=indexes, clip=None, skip_artifacts=False, exclude_regions=None
    )
    results = []
    total_text = work = 0
    placer = TextFragmentAbsorber()
    for index, text in zip(indexes, texts, strict=True):
        fragments, contexts = [], []
        for match in _matches(matcher, text, regex):
            work += 1
            budget.check(work, "max_content_tokens", "search matches inspected")
            start, end = match.span()
            if start == end:
                continue
            budget.check(
                len(results) + len(fragments) + 1,
                "max_container_items",
                "search matches",
            )
            before = text[max(0, start - context_chars) : start]
            after = text[end : end + context_chars]
            matched = match.group()
            total_text += sum(
                _text_size(value, budget) for value in (matched, before, after)
            )
            budget.check(total_text, "max_total_decoded_bytes", "search result text")
            fragments.append(TextFragment(index, matched, start, end))
            contexts.append((before, after))
        # Placement does not bind the fragments to a write-through edit session.
        placer._place(Page._from_index(document, index), fragments)
        for fragment, (before, after) in zip(fragments, contexts, strict=True):
            results.append(
                TextSearchMatch(
                    index,
                    fragment.text,
                    fragment.start,
                    fragment.end,
                    before,
                    after,
                    None if fragment.rect is None else tuple(fragment.rect),
                    None
                    if fragment.quads is None
                    else tuple(
                        tuple(tuple(point) for point in quad) for quad in fragment.quads
                    ),
                )
            )
    return results


def _record(match: TextSearchMatch) -> dict:
    return dict(
        page_index=match.page_index + 1,
        text=match.text,
        start=match.start,
        end=match.end,
        context_before=match.context_before,
        context_after=match.context_after,
        rect=match.rect,
        quads=match.quads,
    )


def to_json(matches: Iterable[TextSearchMatch], budget: _LoadBudget) -> bytes:
    """Encode a versioned report without allocating large escaped strings."""
    output = bytearray()

    def write(data: bytes) -> None:
        size = len(output) + len(data)
        for limit in (
            "max_codec_work_bytes",
            "max_decoded_stream_bytes",
            "max_total_decoded_bytes",
        ):
            budget.check(size, limit, "search JSON bytes")
        output.extend(data)

    try:
        write(b'{"schema_version":1,"matches":[')
        for index, match in enumerate(matches):
            if index:
                write(b",")
            for token in _json_tokens(_record(match)):
                write(token.encode("utf-8"))
        write(b"]}\n")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PdfValidationException(
            "Search report cannot be encoded as UTF-8 JSON"
        ) from exc
    return bytes(output)


def to_csv(matches: Iterable[TextSearchMatch], writer: CsvWriter) -> bytes:
    """Flatten bounds into columns and retain multiple quads in a JSON cell."""
    writer.writerow(
        (
            "page_number",
            "text",
            "start",
            "end",
            "context_before",
            "context_after",
            "x0",
            "y0",
            "x1",
            "y1",
            "quads",
        )
    )
    for match in matches:
        quads = "" if match.quads is None else "".join(_json_tokens(match.quads))
        writer.writerow(
            [
                str(match.page_index + 1),
                match.text,
                str(match.start),
                str(match.end),
                match.context_before,
                match.context_after,
                *[
                    "" if value is None else str(value)
                    for value in (match.rect or (None,) * 4)
                ],
                quads,
            ]
        )
    return writer.finish()


def export_report(
    document,
    pattern,
    destination,
    *,
    pages,
    regex,
    case_sensitive,
    context_chars,
    overwrite,
    csv_options=None,
) -> bytes:
    """Complete extraction and encoding before touching any output destination."""
    from aspose_pdf._export_files import write_files

    document._ensure_not_disposed()
    if document._engine_pdf is None:
        raise AsposePdfException("No document loaded")
    if not isinstance(overwrite, bool):
        raise PdfValidationException("overwrite must be a boolean")
    budget = _LoadBudget(document.load_limits)
    writer = (
        None
        if csv_options is None
        else CsvWriter(*csv_options, budget, context="CSV search")
    )
    matches = search(
        document,
        pattern,
        pages=pages,
        regex=regex,
        case_sensitive=case_sensitive,
        context_chars=context_chars,
    )
    data = to_json(matches, budget) if writer is None else to_csv(matches, writer)
    if destination is not None:
        if hasattr(destination, "write"):
            write_all(destination, data)
        else:
            write_files([(Path(destination), data)], overwrite)
    return data


def find_text(
    document,
    pattern,
    *,
    pages,
    regex,
    case_sensitive,
) -> list[str]:
    """Return matched text strings without context or geometry.

    This is a lightweight companion to :func:`search`. It performs the same
    pattern matching but returns only the matched substrings, one per hit,
    in document order. Use this when you only need to know *what* matched
    and not where or how it appeared.

    Args:
        document: The document to search.
        pattern: Search string (literal) or compiled regex pattern.
        pages: One-based page numbers or None for all pages.
        regex: Treat pattern as a regular expression.
        case_sensitive: Match case exactly unless False.

    Returns:
        List of matched text strings in page order. Empty if no matches.

    See Also:
        :meth:`~aspose_pdf.Document.find_text`: The public method that wraps this.
    """
    budget = _LoadBudget(document.load_limits)
    matcher, regex = _matcher(pattern, regex, case_sensitive, context_chars=0, budget=budget)
    indexes = page_indexes(pages, document.page_count, limits=document.load_limits)
    indexes = range(document.page_count) if indexes is None else indexes
    texts = iter_text_pages(
        document, pages=indexes, clip=None, skip_artifacts=False, exclude_regions=None
    )
    results: list[str] = []
    for index, text in zip(indexes, texts, strict=True):
        for match in _matches(matcher, text, regex):
            results.append(match.group())
    return results
