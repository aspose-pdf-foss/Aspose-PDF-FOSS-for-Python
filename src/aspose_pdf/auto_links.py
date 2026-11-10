"""Link recognized addresses or literal phrases with reliable PDF text bounds."""

from __future__ import annotations

import ipaddress
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

from aspose_pdf.annotations import LinkAnnotation, Name
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    encode_pdf_text_string,
)
from aspose_pdf.engine.text_edit import _lex, _run_char_data, _walk_show_runs
from aspose_pdf.engine.text_locate import _bounds, _char_geometry, _span_quads
from aspose_pdf.engine.text_selection import page_indexes
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.interactive import Destination, URIAction, destination_from_spec
from aspose_pdf.text import TextFragment

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["AutoLinkResult"]

_CandidateFinder = Callable[[str], Iterator[tuple[int, int, str, str]]]

_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_EMAIL = (
    r"(?<![\w.!#$%&'*+/=?^_`{|}~@-])"
    + _ATOM
    + r"(?:\."
    + _ATOM
    + r")*@"
    + _LABEL
    + r"(?:\."
    + _LABEL
    + r")+"
    + r"(?![\w@-])"
)
_ADDRESSES = re.compile(
    r"(?P<url>(?<![\w@./:+-])(?i:https?://|www\.)[^\s<>\"'`\x00-\x1f\x7f\u201c\u201d\u2018\u2019\u00ab\u00bb]+)"
    + r"|(?P<email>"
    + _EMAIL
    + r")",
)
_TRAILING = ".,;:!?…"
_CLOSERS = {")": "(", "]": "[", "}": "{"}


@dataclass(frozen=True)
class AutoLinkResult:
    """Created links and matched text that was left unchanged.

    ``annotations`` contains live link handles in page/match order.
    ``skipped_fragments`` have uncertain geometry; ``existing_fragments``
    overlap an existing link. Fragment offsets refer to full-page extraction.
    Counts include only matches considered within ``max_count``.
    """

    annotations: tuple[LinkAnnotation, ...] = ()
    skipped_fragments: tuple[TextFragment, ...] = ()
    existing_fragments: tuple[TextFragment, ...] = ()

    @property
    def linked_count(self) -> int:
        """The number of new annotations, one per linked match."""
        return len(self.annotations)

    @property
    def skipped_count(self) -> int:
        """The number of matches without reliable geometry."""
        return len(self.skipped_fragments)

    @property
    def existing_count(self) -> int:
        """The number of matches already covered by link annotations."""
        return len(self.existing_fragments)

    @property
    def matched_count(self) -> int:
        """The number of text matches considered, including skips."""
        return self.linked_count + self.skipped_count + self.existing_count


def _trim_url(value: str) -> str:
    """Remove prose punctuation and unmatched closing brackets."""
    end = len(value)
    counts = {character: value.count(character) for character in "()[]{}"}
    while end:
        last = value[end - 1]
        if last in _TRAILING:
            end -= 1
        elif last in _CLOSERS:
            if counts[last] <= counts[_CLOSERS[last]]:
                break
            counts[last] -= 1
            end -= 1
        else:
            break
    return value[:end]


def _web_uri(value: str) -> str | None:
    """Build an ASCII HTTP(S) URI without contacting its destination."""
    try:
        parts = urlsplit(
            "https://" + value if value.lower().startswith("www.") else value
        )
        host = parts.hostname
        if parts.scheme.lower() not in {"http", "https"} or not host:
            return None
        port = parts.port
        if ":" in host:
            ipaddress.IPv6Address(host)
            host = "[" + host + "]"
        else:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or any(
                not re.fullmatch(_LABEL, label) for label in host.split(".")
            ):
                return None
        netloc = host + (f":{port}" if port is not None else "")
        if "@" in parts.netloc:
            netloc = parts.netloc.rsplit("@", 1)[0] + "@" + netloc
        uri = urlunsplit(
            (parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment)
        )
        return quote(uri, safe=":/?#[]@!$&'()*+,;=%~-._")
    except (ValueError, UnicodeError):
        return None


def _candidates(
    text: str, urls: bool, emails: bool
) -> Iterator[tuple[int, int, str, str]]:
    """Yield trimmed text spans and normalized targets in text order."""
    for match in _ADDRESSES.finditer(text):
        if match.lastgroup == "url":
            if not urls:
                continue
            value = _trim_url(match.group())
            uri = _web_uri(value)
        else:
            if not emails:
                continue
            value = match.group()
            local, domain = value.rsplit("@", 1)
            if (
                len(local) > 64
                or len(value) > 254
                or not any(
                    character.isalpha() for character in domain.rsplit(".", 1)[-1]
                )
            ):
                continue
            uri = "mailto:" + quote(value, safe="@.!$'*+-_~")
        if uri:
            yield match.start(), match.start() + len(value), value, uri


def _located(engine, index: int, candidates: _CandidateFinder) -> dict[str, list]:
    """Reuse the text locator's run decoding and whole-code-unit geometry."""
    budget = engine._load_budget
    found = defaultdict(list)
    count = 0
    runs = _walk_show_runs(
        _lex(engine.get_page_content(index), budget=budget, skip_inline_images=True),
        metric_for_name=engine._build_simple_font_metrics(index),
        budget=budget,
    )
    for run in runs:
        if not run.geometry_ok or run.origin_trm is None:
            continue
        infos, text, entries, _starts = _run_char_data(run, budget=budget)
        geometry = None
        for start, end, value, _uri in candidates(text):
            if (
                not entries[start][2]
                or not entries[end - 1][3]
                or all(entries[i][4] for i in range(start, end))
                or any(entries[i][5] for i in range(start, end))
            ):
                continue
            if geometry is None:
                geometry = _char_geometry(run, infos, budget=budget)
                if geometry is None:
                    break
            quads = _span_quads(run.origin_trm, geometry, start, end, budget=budget)
            if not quads:
                continue
            rect = _bounds(quads)
            if (
                not all(math.isfinite(v) for v in rect)
                or rect[2] <= rect[0]
                or rect[3] <= rect[1]
            ):
                continue
            count += 1
            budget.check(count, "max_container_items", "auto-link located matches")
            found[value].append((rect, quads))
    return found


def _page_annotations(engine, index: int):
    """Read existing link rectangles without decoding annotation appearances."""
    page = engine._get_page_dict(index)
    if not isinstance(page, PdfDictionary):
        raise PdfValidationException("Cannot resolve the page for automatic links")
    original = page.mapping.get(PdfName("Annots"))
    annots = engine._resolve(original)
    if original is not None and not isinstance(annots, PdfArray):
        raise PdfValidationException("Page Annots must be an array")
    if annots is None:
        annots = PdfArray([])
    engine._load_budget.check(
        len(annots.items), "max_container_items", "auto-link page annotations"
    )
    rectangles = []
    for reference in annots.items:
        annotation = engine._resolve(reference)
        if not isinstance(annotation, PdfDictionary):
            raise PdfValidationException("Page contains an invalid annotation entry")
        if annotation.mapping.get(PdfName("Subtype")) != PdfName("Link"):
            continue
        rect = engine._resolve(annotation.mapping.get(PdfName("Rect")))
        if not isinstance(rect, PdfArray) or len(rect.items) != 4:
            continue
        numbers = [engine._resolve(value) for value in rect.items]
        if not all(isinstance(value, PdfNumber) for value in numbers):
            continue
        try:
            box = tuple(float(value.value) for value in numbers)
        except (OverflowError, TypeError, ValueError):
            continue
        if all(math.isfinite(v) for v in box):
            rectangles.append(
                (
                    min(box[0], box[2]),
                    min(box[1], box[3]),
                    max(box[0], box[2]),
                    max(box[1], box[3]),
                )
            )
    # A shared /Annots array must not put this page's new links on other pages.
    return page, PdfArray(list(annots.items)), rectangles


def _overlaps(first, second) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(
        first[3], second[3]
    ) > max(first[1], second[1])


def _add_link(
    engine, page, page_dict, annots, fragment, uri, target=None
) -> LinkAnnotation:
    """Append a link and return a live handle without reading appearances."""
    if target is None:
        cos_target, key = engine._action_cos(URIAction(uri)), "A"
        property_value = {"Type": Name("Action"), "S": Name("URI"), "URI": uri}
    else:
        cos_target, key = engine._interactive_target_cos(target)
        property_value = target
    annotation = PdfDictionary(
        {
            PdfName("Type"): PdfName("Annot"),
            PdfName("Subtype"): PdfName("Link"),
            PdfName("Rect"): PdfArray([PdfNumber(value) for value in fragment.rect]),
            PdfName("Border"): PdfArray([PdfNumber(0), PdfNumber(0), PdfNumber(0)]),
            PdfName(key): cos_target,
        }
    )
    reference = engine._cos_doc.register_object(annotation)
    index = len(annots.items)
    annots.items.append(reference)
    page_dict.mapping[PdfName("Annots")] = annots
    return LinkAnnotation(
        page,
        index,
        {
            "Subtype": "Link",
            "Rect": fragment.rect,
            "Contents": "",
            "Properties": {
                "Border": [0, 0, 0],
                key: property_value,
            },
        },
    )


def _auto_link(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    urls: bool = True,
    emails: bool = True,
    max_count: int = 0,
) -> AutoLinkResult:
    """Compose address recognition, conservative geometry and link creation."""
    document._ensure_not_disposed()
    engine = document._engine_pdf
    if engine is None:
        raise AsposePdfException("No document loaded")
    if not isinstance(urls, bool) or not isinstance(emails, bool):
        raise PdfValidationException("urls and emails must be booleans")
    if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 0:
        raise PdfValidationException("max_count must be a non-negative integer")
    indexes = page_indexes(pages, document.page_count, limits=document.load_limits)
    if not urls and not emails:
        return AutoLinkResult()
    return _link_matches(
        document, indexes, lambda text: _candidates(text, urls, emails), max_count
    )


def _link_text(
    document: Document,
    search: str,
    uri: str | None = None,
    *,
    target: Destination | str | None = None,
    pages: Iterable[int] | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
) -> AutoLinkResult:
    """Find literal matches and link them to one URI or local destination."""
    document._ensure_not_disposed()
    engine = document._engine_pdf
    if engine is None:
        raise AsposePdfException("No document loaded")
    if not isinstance(search, str) or not search:
        raise PdfValidationException("search must be nonempty text")
    if target is None:
        if not isinstance(uri, str) or not uri.strip():
            raise PdfValidationException("uri must be nonblank text, or supply target")
    elif uri is not None:
        raise PdfValidationException("Supply either uri or target, not both")
    if not isinstance(case_sensitive, bool):
        raise PdfValidationException("case_sensitive must be a boolean")
    if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 0:
        raise PdfValidationException("max_count must be a non-negative integer")
    budget = engine._load_budget
    values = [(search, "search")]
    if uri is not None:
        values.append((uri, "uri"))
    for value, label in values:
        budget.check(len(value), "max_object_bytes", f"link text {label}")
        try:
            encoded = (
                encode_pdf_text_string(value) if label == "uri" else value.encode("utf-8")
            )
        except UnicodeError as exc:
            raise PdfValidationException(f"{label} contains invalid Unicode") from exc
        budget.check(len(encoded), "max_object_bytes", f"link text {label}")
    if target is not None:
        _validate_local_target(engine, target, document.page_count)
    source = re.escape(search)
    budget.check(len(source), "max_object_bytes", "link text pattern")
    pattern = re.compile(source, 0 if case_sensitive else re.IGNORECASE)
    indexes = page_indexes(pages, document.page_count, limits=document.load_limits)

    def candidates(text: str) -> Iterator[tuple[int, int, str, str]]:
        for match in pattern.finditer(text):
            yield match.start(), match.end(), match.group(), uri or ""

    return _link_matches(document, indexes, candidates, max_count, target=target)


def _validate_local_target(engine, target: Destination | str, page_count: int) -> None:
    """Refuse broken destinations before reading any source page's content."""
    destination = target
    if isinstance(target, str):
        from aspose_pdf.engine.simple_pdf import _destination_name_bytes

        budget = engine._load_budget
        budget.check(len(target), "max_object_bytes", "link text target")
        encoded = _destination_name_bytes(target)
        budget.check(len(encoded), "max_object_bytes", "link text target")
        destination = engine.named_destination_target(target)
        if destination is None:
            raise PdfValidationException(
                "target must name an existing, resolvable named destination"
            )
    if not isinstance(destination, Destination):
        raise PdfValidationException("target must be a Destination or a destination name")
    page = getattr(destination, "page", None)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
        raise PdfValidationException("The link destination must name an existing page")
    try:
        kind, params = destination._spec()
        valid = destination_from_spec(kind, page, params) is not None
    except (NotImplementedError, TypeError, ValueError) as exc:
        raise PdfValidationException("Invalid link destination view") from exc
    if not valid:
        raise PdfValidationException("Invalid link destination view")
    for value in params:
        if value is None and kind != "FitR":
            continue
        try:
            valid = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
            )
        except OverflowError:
            valid = False
        if not valid:
            raise PdfValidationException(
                "Link destination coordinates must be finite numbers"
            )


def _link_matches(
    document: Document,
    indexes: tuple[int, ...] | None,
    candidates: _CandidateFinder,
    max_count: int,
    *,
    target: Destination | str | None = None,
) -> AutoLinkResult:
    """Share conservative placement and duplicate checks across link searches."""
    engine = document._engine_pdf
    annotations, skipped, existing = [], [], []
    considered = overlap_checks = 0
    budget = engine._load_budget
    for index in range(document.page_count) if indexes is None else indexes:
        page = document._page_at(index)
        matches = []
        by_text = defaultdict(list)
        for start, end, value, uri in candidates(page.extract_text()):
            budget.check(
                len(matches) + 1, "max_container_items", "auto-link page matches"
            )
            fragment = TextFragment(index, value, start, end)
            matches.append((fragment, uri))
            by_text[value].append(fragment)
        if not matches:
            continue
        located = _located(engine, index, candidates)
        for value, group in by_text.items():
            places = located[value]
            # Counts must agree: a form or an unmeasurable repeated match
            # must not borrow the coordinates of another occurrence.
            if len(places) == len(group):
                for fragment, (rect, quads) in zip(group, places):
                    fragment.rect, fragment.quads = rect, quads
        page_dict, annots, rectangles = _page_annotations(engine, index)
        planned = []
        for fragment, uri in matches:
            budget.check(
                considered + 1, "max_container_items", "auto-link result matches"
            )
            considered += 1
            if fragment.rect is None:
                skipped.append(fragment)
            else:
                covered = False
                for rect in rectangles:
                    overlap_checks += 1
                    budget.check(
                        overlap_checks, "max_content_tokens", "auto-link overlap checks"
                    )
                    if _overlaps(fragment.rect, rect):
                        covered = True
                        break
                if covered:
                    existing.append(fragment)
                else:
                    planned.append((fragment, uri))
                    rectangles.append(fragment.rect)
            if max_count and considered >= max_count:
                break
        budget.check(
            len(annots.items) + len(planned),
            "max_container_items",
            "auto-link page annotations",
        )
        budget.check_objects(len(engine._cos_doc.objects) + len(planned))
        if planned:
            last_object = max(
                max(engine._cos_doc.objects, default=0),
                max(engine._cos_doc.generations, default=0),
            ) + len(planned)
            budget.check_object_id(last_object)
        for fragment, uri in planned:
            annotations.append(
                _add_link(engine, page, page_dict, annots, fragment, uri, target)
            )
        if max_count and considered >= max_count:
            break
    return AutoLinkResult(tuple(annotations), tuple(skipped), tuple(existing))


def _link_texts(
    document: Document,
    patterns: dict[str | re.Pattern[str], dict[str, Any] | None],
    *,
    uri: str | None = None,
    target: Destination | str | None = None,
    pages: Iterable[int] | None = None,
    case_sensitive: bool = True,
    max_count: int = 0,
    regex: bool = False,
) -> AutoLinkResult:
    """Link multiple text patterns to one URI or local destination.

    This is a convenience method that links multiple text patterns
    to the same URI or destination. It is equivalent to calling
    :meth:`_link_text` for each pattern, but more efficient as it
    performs a single scan of the content streams.

    Parameters
    ----------
    document
        The document to link text in.
    patterns
        A dictionary mapping search patterns to options. Each key is a string
        or compiled regex pattern; each value is a dict of options for that
        pattern (currently unused, kept for consistency with other batch APIs).
    uri
        The URI to link to. Exactly one of ``uri`` and ``target`` must be provided.
    target
        A destination to link to. Exactly one of ``uri`` and ``target`` must be provided.
    pages
        One-based page numbers or None for all pages.
    case_sensitive
        If True (default), matching is case-sensitive. Ignored for compiled
        regex patterns.
    max_count
        Maximum links to create across all patterns. 0 (default) means
        unlimited. The count is shared across all patterns.
    regex
        If True, all string patterns are treated as regular expressions.
        Not required for compiled :class:`re.Pattern` keys.

    Returns
    -------
    AutoLinkResult
        A result object with annotations, skipped_fragments, existing_fragments,
        and counts (linked_count, skipped_count, existing_count, matched_count).

    See Also
    --------
    link_text : Link a single text pattern.
    highlight_texts : Highlight multiple text patterns.

    Examples
    --------
    Link multiple patterns to a URI:

    >>> document.link_texts(
    ...     {"review": {}, "approved": {}},
    ...     uri="https://example.com/docs"
    ... )

    Link multiple patterns to a named destination:

    >>> document.link_texts(
    ...     {re.compile(r"\\d{4}"): {}, "chapter": {}},
    ...     target="appendix",
    ...     regex=True
    ... )

    Limit links and target a specific page:

    >>> document.link_texts(
    ...     {"important": {}, "critical": {}},
    ...     uri="https://example.com",
    ...     page_index=1,
    ...     max_count=5
    ... )
    """
    document._ensure_not_disposed()
    engine = document._engine_pdf
    if engine is None:
        raise AsposePdfException("No document loaded")

    if not patterns:
        raise PdfValidationException("patterns must not be empty")

    # Validate and normalize patterns
    compiled_patterns = []
    for pattern_key, options in patterns.items():
        if options is not None and not isinstance(options, dict):
            raise PdfValidationException(
                f"Pattern options must be a dict or None, got {type(options).__name__}"
            )
        if isinstance(pattern_key, re.Pattern):
            # For compiled patterns, use the pattern as-is
            compiled_patterns.append(pattern_key)
        elif isinstance(pattern_key, str):
            if not pattern_key:
                raise PdfValidationException("Pattern string must not be empty")
            if regex:
                # Treat as regex
                try:
                    compiled_patterns.append(
                        re.compile(pattern_key, 0 if case_sensitive else re.IGNORECASE)
                    )
                except re.error as exc:
                    raise PdfValidationException(
                        f"Invalid regex pattern: {pattern_key}"
                    ) from exc
            else:
                # Escape and treat as literal
                compiled_patterns.append(
                    re.compile(re.escape(pattern_key), 0 if case_sensitive else re.IGNORECASE)
                )
        else:
            raise PdfValidationException(
                f"Pattern key must be a string or compiled regex, got {type(pattern_key).__name__}"
            )

    # Validate uri/target exactly one
    if target is None:
        if not isinstance(uri, str) or not uri.strip():
            raise PdfValidationException(
                "uri must be nonblank text, or supply target"
            )
    elif uri is not None:
        raise PdfValidationException("Supply either uri or target, not both")

    # Validate target if provided
    if target is not None:
        _validate_local_target(engine, target, document.page_count)

    # Build page index list
    indexes = page_indexes(pages, document.page_count, limits=document.load_limits)
    if indexes is None:
        indexes = tuple(range(document.page_count))
    else:
        indexes = tuple(indexes)

    def candidates(text: str) -> Iterator[tuple[int, int, str, str]]:
        """Yield (start, end, value, uri) for all pattern matches in text."""
        for pattern in compiled_patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                value = match.group()
                if value:
                    # Yield the URI or an empty string (target is passed separately)
                    yield start, end, value, uri or ""

    # Reuse the shared _link_matches function, passing the pre-computed indexes
    result = _link_matches(document, indexes, candidates, max_count, target=target)
    return result
