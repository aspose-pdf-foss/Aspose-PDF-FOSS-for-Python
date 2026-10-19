"""Recognize web/email addresses and link only reliably located PDF text."""

from __future__ import annotations

import ipaddress
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit

from aspose_pdf.annotations import LinkAnnotation, Name
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.text_edit import _lex, _run_char_data, _walk_show_runs
from aspose_pdf.engine.text_locate import _bounds, _char_geometry, _span_quads
from aspose_pdf.engine.text_selection import page_indexes
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.interactive import URIAction
from aspose_pdf.text import TextFragment

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["AutoLinkResult"]

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
    """Created links and detected addresses that were left unchanged.

    ``annotations`` contains live link handles in page/match order.
    ``skipped_fragments`` have uncertain geometry; ``existing_fragments``
    overlap an existing link. Fragment offsets refer to full-page extraction.
    Counts include only addresses considered within ``max_count``.
    """

    annotations: tuple[LinkAnnotation, ...] = ()
    skipped_fragments: tuple[TextFragment, ...] = ()
    existing_fragments: tuple[TextFragment, ...] = ()

    @property
    def linked_count(self) -> int:
        """The number of new annotations, one per linked address."""
        return len(self.annotations)

    @property
    def skipped_count(self) -> int:
        """The number of addresses without reliable geometry."""
        return len(self.skipped_fragments)

    @property
    def existing_count(self) -> int:
        """The number of addresses already covered by link annotations."""
        return len(self.existing_fragments)

    @property
    def matched_count(self) -> int:
        """The number of detected addresses considered, including skips."""
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


def _located(engine, index: int, urls: bool, emails: bool) -> dict[str, list]:
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
        for start, end, value, _uri in _candidates(text, urls, emails):
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
            budget.check(count, "max_container_items", "auto-link located addresses")
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


def _add_link(engine, page, page_dict, annots, fragment, uri) -> LinkAnnotation:
    """Append a typed URI action and return a live handle without reading APs."""
    action = engine._action_cos(URIAction(uri))
    annotation = PdfDictionary(
        {
            PdfName("Type"): PdfName("Annot"),
            PdfName("Subtype"): PdfName("Link"),
            PdfName("Rect"): PdfArray([PdfNumber(value) for value in fragment.rect]),
            PdfName("Border"): PdfArray([PdfNumber(0), PdfNumber(0), PdfNumber(0)]),
            PdfName("A"): action,
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
                "A": {"Type": Name("Action"), "S": Name("URI"), "URI": uri},
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
    annotations, skipped, existing = [], [], []
    considered = overlap_checks = 0
    budget = engine._load_budget
    for index in range(document.page_count) if indexes is None else indexes:
        page = document.pages[index]
        matches = []
        by_text = defaultdict(list)
        for start, end, value, uri in _candidates(page.extract_text(), urls, emails):
            budget.check(
                len(matches) + 1, "max_container_items", "auto-link page matches"
            )
            fragment = TextFragment(index, value, start, end)
            matches.append((fragment, uri))
            by_text[value].append(fragment)
        if not matches:
            continue
        located = _located(engine, index, urls, emails)
        for value, group in by_text.items():
            places = located[value]
            # Counts must agree: a form or an unmeasurable repeated address
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
                _add_link(engine, page, page_dict, annots, fragment, uri)
            )
        if max_count and considered >= max_count:
            break
    return AutoLinkResult(tuple(annotations), tuple(skipped), tuple(existing))
