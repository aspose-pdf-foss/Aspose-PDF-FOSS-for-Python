"""Find page boundaries from extracted text before importing independent parts."""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import pairwise
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

from .bookmark_split import _range_outlines, _snapshot
from .cos import PdfStream

if TYPE_CHECKING:
    from .simple_pdf import SimplePdf


def _pattern(
    pdf: SimplePdf,
    pattern: str | re.Pattern[str],
    regex: bool,
    case_sensitive: bool,
    include_preamble: bool,
) -> re.Pattern[str]:
    """Validate all options and compile the matcher before reading any page."""
    for name, value in (
        ("regex", regex),
        ("case_sensitive", case_sensitive),
        ("include_preamble", include_preamble),
    ):
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
    flags = 0
    if isinstance(pattern, re.Pattern):
        source, flags = pattern.pattern, pattern.flags
        if not isinstance(source, str):
            raise TypeError("pattern must be a text regular expression")
        is_regex = True
    elif isinstance(pattern, str):
        source, is_regex = pattern, regex
    else:
        raise TypeError("pattern must be a string or a compiled regular expression")
    if not source:
        raise ValueError("pattern must not be empty")
    budget = pdf._load_budget
    budget.check(len(source), "max_object_bytes", "text split pattern")
    try:
        encoded = source.encode("utf-8")
    except UnicodeError as exc:
        raise PdfValidationException("pattern contains invalid Unicode") from exc
    budget.check(len(encoded), "max_object_bytes", "text split pattern")
    if not is_regex:
        source = re.escape(source)
    if not case_sensitive:
        flags |= re.IGNORECASE
    return re.compile(source, flags)


def split_by_text(
    pdf: SimplePdf,
    pattern: str | re.Pattern[str],
    *,
    regex: bool = False,
    case_sensitive: bool = True,
    include_preamble: bool = True,
    outlines: list | None = None,
) -> Iterator[SimplePdf]:
    """Yield whole-page parts, restoring the source outline model before each yield."""
    pdf._ensure_not_disposed()
    matcher = _pattern(pdf, pattern, regex, case_sensitive, include_preamble)
    count = len(pdf.pages)
    pdf._load_budget.check_pages(count)
    pdf._load_budget.check(count, "max_container_items", "text split pages")
    starts = []
    for index in range(count):
        # A match starts one part even when the page has several occurrences.
        # Do not join page texts or fill the full-document text/content caches.
        if matcher.search(pdf.extract_page_text(index)) is not None:
            starts.append(index)
    if not starts:
        raise PdfValidationException("No pages match the text split pattern")
    if include_preamble and starts[0] > 0:
        starts.insert(0, 0)
    starts.append(count)
    roots = pdf._outlines_data if outlines is None else outlines
    snapshot, _starts = _snapshot(pdf, roots, 1)
    for start, stop in pairwise(starts):
        original = pdf._outlines_data
        try:
            pdf._outlines_data = _range_outlines(snapshot, start, stop)
            result = pdf.extract_pages(range(start, stop))
        finally:
            pdf._outlines_data = original
        try:
            _check_result(result)
            _refresh_targets(result)
        except BaseException:
            result.dispose()
            raise
        yield result


def _check_result(pdf: SimplePdf, *, context: str = "text split") -> None:
    """Apply the source policy to the newly allocated graph and decoded content."""
    budget, objects = pdf._load_budget, pdf._cos_doc.objects
    budget.check_pages(len(pdf.pages))
    budget.check_objects(len(objects))
    budget.check_object_id(max(objects, default=0))
    for obj in objects.values():
        if isinstance(obj, PdfStream):
            budget.check(len(obj.content), "max_object_bytes", f"{context} stream")
    total = 0
    for content in pdf.page_contents:
        budget.check(len(content), "max_content_stream_bytes", f"{context} content")
        budget.check(len(content), "max_decoded_stream_bytes", f"{context} content")
        total += len(content)
    budget.check(total, "max_total_decoded_bytes", f"{context} content")


def _refresh_targets(pdf: SimplePdf) -> None:
    """Expose typed destinations from the imported references before a save/reload."""
    from .simple_pdf import (
        _UNRESOLVED_DESTINATION,
        action_from_cos,
        destination_from_cos,
    )

    stack = list(pdf._outlines_data)
    while stack:
        item = stack.pop()
        loaded = item.get("loaded_target")
        if loaded is not None:
            key, raw = loaded
            target = (
                action_from_cos(pdf._resolve(raw), pdf._resolve, pdf._page_index_of)
                if key == "A"
                else destination_from_cos(raw, pdf._resolve, pdf._page_index_of)
            )
            item["target"] = None if target is _UNRESOLVED_DESTINATION else target
        stack.extend(item.get("children", ()))
