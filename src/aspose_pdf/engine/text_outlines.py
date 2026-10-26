"""Plan bookmarks from extracted text before replacing the live outline tree."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.outlines import OutlineCollection, OutlineItem

from .bookmark_json import _Context, _existing_collection, _walk
from .cos import PdfArray, PdfName, encode_pdf_text_string
from .text_region import validate_clip, validate_exclude_regions
from .text_selection import page_indexes, validate_skip_artifacts

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _size(text: str, budget: _LoadBudget, context: str, *, pdf_string: bool = False) -> int:
    budget.check(len(text), "max_object_bytes", context)
    try:
        size = len(encode_pdf_text_string(text) if pdf_string else text.encode("utf-8"))
    except UnicodeError as exc:
        raise PdfValidationException(f"{context} contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", context)
    return size


def _matcher(
    pattern: str | re.Pattern[str],
    regex: bool,
    case_sensitive: bool,
    title_group: int | str,
    budget: _LoadBudget,
) -> re.Pattern[str]:
    if not isinstance(regex, bool) or not isinstance(case_sensitive, bool):
        raise PdfValidationException("regex and case_sensitive must be booleans")
    if isinstance(pattern, re.Pattern):
        source, flags = pattern.pattern, pattern.flags
        is_regex = True
    elif isinstance(pattern, str):
        source, flags, is_regex = pattern, 0, regex
    else:
        raise PdfValidationException("pattern must be a string or a compiled text regular expression")
    if not isinstance(source, str) or not source:
        raise PdfValidationException("pattern must be nonempty text")
    _size(source, budget, "text bookmark pattern")
    if not is_regex:
        source = re.escape(source)
        _size(source, budget, "text bookmark pattern")
    if not case_sensitive:
        flags |= re.IGNORECASE
    try:
        matcher = re.compile(source, flags)
    except (re.error, ValueError, OverflowError, RecursionError) as exc:
        raise PdfValidationException(f"Invalid text bookmark pattern: {exc}") from exc
    if isinstance(title_group, bool) or not isinstance(title_group, (int, str)):
        raise PdfValidationException("title_group must be a group number or name")
    if (
        (isinstance(title_group, int) and not 0 <= title_group <= matcher.groups)
        or (isinstance(title_group, str) and title_group not in matcher.groupindex)
    ):
        raise PdfValidationException("title_group does not identify a pattern group")
    return matcher


def generate_outlines_from_text(
    document: Document,
    pattern: str | re.Pattern[str],
    *,
    pages: Iterable[int] | None = None,
    regex: bool = False,
    case_sensitive: bool = True,
    title_group: int | str = 0,
    replace: bool = True,
    max_count: int = 0,
    clip: Sequence[float] | None = None,
    exclude_regions: Iterable[Sequence[float]] | None = None,
    skip_artifacts: bool = False,
) -> int:
    """Create flat page destinations only after the complete requested search succeeds."""
    budget = _LoadBudget(document.load_limits)
    if not isinstance(replace, bool):
        raise PdfValidationException("replace must be a boolean")
    if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 0:
        raise PdfValidationException("max_count must be a non-negative integer")
    skip_artifacts = validate_skip_artifacts(skip_artifacts)
    matcher = _matcher(pattern, regex, case_sensitive, title_group, budget)
    rectangle = None if clip is None else validate_clip(clip)
    exclusions = validate_exclude_regions(exclude_regions, limits=budget.limits)
    count = document.page_count
    indexes = page_indexes(pages, count, limits=budget.limits)
    indexes = range(count) if indexes is None else indexes
    budget.check(len(indexes), "max_container_items", "text bookmark page selection")
    planned = []
    considered = retained = 0
    for index in indexes:
        text = document._engine_pdf.extract_page_text(
            index, clip=rectangle, exclude_regions=exclusions, skip_artifacts=skip_artifacts,
        )
        _size(text, budget, "text bookmark page text")
        for match in matcher.finditer(text):
            considered += 1
            budget.check(considered, "max_container_items", "text bookmark matches")
            value = match.group(title_group)
            if value is None:
                continue
            title = " ".join(value.split())
            if not title:
                continue
            retained += _size(title, budget, "text bookmark title", pdf_string=True)
            budget.check(retained, "max_total_decoded_bytes", "text bookmark titles")
            planned.append(OutlineItem(title, index))
            if max_count and len(planned) >= max_count:
                break
        if max_count and len(planned) >= max_count:
            break

    collection = document._outlines
    if planned:
        # Reuse bookmark import's object-slot policy, including replacement at
        # the object limit, without serializing or changing existing targets.
        context = _Context(document)
        if not replace:
            if collection is None:
                collection = _existing_collection(context)
            else:
                for _item, _entry, _parent in _walk(collection._items, context):
                    pass
        total = context.work + len(planned)
        budget.check(total, "max_container_items", "text bookmark tree items")
        context.check_new_objects(total)
        for item in planned:
            # Keep the matched page's identity if pages are inserted before a save.
            item._loaded_target = (
                "Dest", PdfArray([context.refs[item.page_index], PdfName("Fit")])
            )
    if replace:
        if collection is None:
            collection = OutlineCollection()
        collection._items = planned
        document._outlines = collection
    elif planned:
        combined = collection._items + planned
        collection._items = combined
        document._outlines = collection
    return len(planned)
