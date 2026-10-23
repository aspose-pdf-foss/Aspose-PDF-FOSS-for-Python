"""Plan filtered annotation removal before changing arrays or relationships."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .annotation_json import _subtypes
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)
from .simple_pdf import _PDF_DATE_RE, _parse_pdf_date
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document

_REVIEW = frozenset(
    {
        "Text",
        "FreeText",
        "Line",
        "Square",
        "Circle",
        "Polygon",
        "PolyLine",
        "Highlight",
        "Underline",
        "Squiggly",
        "StrikeOut",
        "Stamp",
        "Caret",
        "Ink",
        "Redact",
    }
)
_ANNOTS = PdfName("Annots")
_MISSING = object()


def _utc(value: dt.datetime) -> dt.datetime:
    """Use UTC for missing zones and normalize aware values deterministically."""
    return (
        value.replace(tzinfo=dt.UTC)
        if value.utcoffset() is None
        else value.astimezone(dt.UTC)
    )


def _pdf_date(text: str) -> dt.datetime | None:
    """Require the entire PDF date and valid zone components before parsing."""
    text = text.strip()
    if text[:2].upper() == "D:":
        text = text[2:]
    if not text.isascii():
        return None
    match = _PDF_DATE_RE.fullmatch(text)
    if match is None:
        return None
    zone = match.group(7)
    if zone and zone[0] in "+-":
        digits = zone[1:].replace("'", "")
        if int(digits[:2]) > 23 or (len(digits) > 2 and int(digits[2:]) > 59):
            return None
    try:
        value = _parse_pdf_date(text)
        return _utc(value) if value is not None else None
    except (ValueError, OverflowError):
        return None


def _cutoff(value: Any, name: str) -> dt.datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, dt.datetime):
            return _utc(value)
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time(), dt.UTC)
        if isinstance(value, str):
            parsed = _pdf_date(value)
            if parsed is not None:
                return parsed
            return _utc(dt.datetime.fromisoformat(value))
    except (ValueError, OverflowError, TypeError):
        pass
    raise PdfValidationException(
        f"{name} must be a valid datetime, date, ISO date or PDF date"
    )


def _authors(value, case_sensitive, budget):
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, (bytes, bytearray)):
        raise PdfValidationException("authors must be a string or iterable of strings")
    try:
        iterator = iter(value)
    except TypeError:
        raise PdfValidationException(
            "authors must be a string or iterable of strings"
        ) from None
    result = set()
    total_bytes = 0
    for offset, author in enumerate(iterator, 1):
        budget.check(offset, "max_container_items", "annotation cleanup authors")
        if not isinstance(author, str):
            raise PdfValidationException("authors must contain strings")
        try:
            size = len(author.encode("utf-8"))
        except UnicodeError:
            raise PdfValidationException("authors must contain valid text") from None
        budget.check(size, "max_object_bytes", "annotation cleanup author")
        normalized = author if case_sensitive else author.casefold()
        total_bytes += len(normalized.encode("utf-8"))
        budget.check(
            total_bytes, "max_total_decoded_bytes", "annotation cleanup author bytes"
        )
        result.add(normalized)
    return result


class _Reader:
    def __init__(self, pdf, budget):
        self.pdf, self.budget, self.work = pdf, budget, 0

    def tick(self, depth=0):
        self.work += 1
        self.budget.check(
            self.work, "max_container_items", "annotation cleanup metadata work"
        )
        self.budget.check(
            depth, "max_nesting_depth", "annotation cleanup reference depth"
        )

    def resolve(self, value):
        seen = set()
        while isinstance(value, PdfIndirectReference):
            self.tick(len(seen) + 1)
            identity = value.object_number, value.gen_number
            if identity in seen:
                raise PdfParseException("Annotation cleanup reference cycle")
            seen.add(identity)
            value = self.pdf._resolve(value)
            if value is None:
                raise PdfParseException("Annotation cleanup reference was not found")
        return value

    def page(self, index):
        self.tick()
        page = self.pdf._get_page_dict(index)
        if not isinstance(page, PdfDictionary) or isinstance(page, PdfStream):
            raise PdfParseException("Annotation cleanup page must be a dictionary")
        raw = page.mapping.get(_ANNOTS, _MISSING)
        array = self.resolve(None if raw is _MISSING else raw)
        if array is None or isinstance(array, PdfNull):
            return page, raw, []
        if not isinstance(array, PdfArray):
            raise PdfParseException("Annotation cleanup Annots must be an array")
        self.budget.check(
            len(array.items), "max_container_items", "annotation cleanup page entries"
        )
        entries = []
        for reference in array.items:
            self.tick()
            obj = self.resolve(reference)
            if not isinstance(obj, PdfDictionary) or isinstance(obj, PdfStream):
                raise PdfParseException("Annotation cleanup entry must be a dictionary")
            kind = self.resolve(obj.mapping.get(PdfName("Subtype")))
            if not isinstance(kind, PdfName) or not kind.name[1:]:
                raise PdfParseException(
                    "Annotation cleanup Subtype must be a nonempty PDF name"
                )
            entries.append((reference, obj, kind.name[1:]))
        return page, raw, entries

    def text(self, obj, key):
        value = self.resolve(obj.mapping.get(PdfName(key)))
        if value is None or isinstance(value, PdfNull):
            return ""
        if not isinstance(value, PdfString):
            return None
        self.budget.check(
            len(value.value), "max_object_bytes", "annotation cleanup metadata string"
        )
        return decode_pdf_text_string(value)

    def target(self, obj, key):
        value = self.resolve(obj.mapping.get(PdfName(key)))
        return (
            id(value)
            if isinstance(value, PdfDictionary) and not isinstance(value, PdfStream)
            else None
        )


def remove(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    authors: str | Iterable[str] | None = None,
    before: dt.datetime | dt.date | str | None = None,
    after: dt.datetime | dt.date | str | None = None,
    date_field: str = "modified",
    subtypes: str | Iterable[str] | None = None,
    case_sensitive: bool = True,
    dry_run: bool = False,
) -> int:
    """Remove matching page annotation occurrences and repair surviving links."""
    budget = _LoadBudget(document.load_limits)
    for name, value in (("case_sensitive", case_sensitive), ("dry_run", dry_run)):
        if type(value) is not bool:
            raise PdfValidationException(f"{name} must be a boolean")
    if date_field not in ("modified", "created"):
        raise PdfValidationException("date_field must be modified or created")
    lower, upper = _cutoff(after, "after"), _cutoff(before, "before")
    if lower is not None and upper is not None and lower >= upper:
        raise PdfValidationException("after must be earlier than before")
    wanted_authors = _authors(authors, case_sensitive, budget)
    wanted = _subtypes(subtypes, budget)
    if wanted is not None:
        total_bytes = 0
        for kind in wanted:
            try:
                size = len(kind.encode("utf-8"))
            except UnicodeError:
                raise PdfValidationException(
                    "subtypes must contain valid text"
                ) from None
            budget.check(size, "max_object_bytes", "annotation cleanup subtype")
            total_bytes += size
        budget.check(
            total_bytes, "max_total_decoded_bytes", "annotation cleanup subtype bytes"
        )
    wanted = _REVIEW if wanted is None else wanted
    if "Widget" in wanted:
        raise PdfValidationException("Widget cleanup must use the form API")
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check(
        len(selection), "max_container_items", "annotation cleanup page selection"
    )
    budget.check_pages(len(selection))
    if not selection or not wanted or wanted_authors == set():
        return 0
    selected = dict.fromkeys(selection)
    pdf = document._engine_pdf
    pdf._ensure_cos()
    reader = _Reader(pdf, budget)
    snapshots = {index: reader.page(index) for index in selected}
    removed = {index: set() for index in selected}
    date_key = "M" if date_field == "modified" else "CreationDate"
    for index, (_page, _raw, entries) in snapshots.items():
        for position, (_reference, obj, kind) in enumerate(entries):
            if kind not in wanted:
                continue
            if wanted_authors is not None:
                author = reader.text(obj, "T")
                if (
                    author is None
                    or (author if case_sensitive else author.casefold())
                    not in wanted_authors
                ):
                    continue
            if lower is not None or upper is not None:
                text = reader.text(obj, date_key)
                moment = _pdf_date(text) if text is not None else None
                if (
                    moment is None
                    or (lower is not None and moment <= lower)
                    or (upper is not None and moment >= upper)
                ):
                    continue
            removed[index].add(position)
    if not any(removed.values()):
        return 0
    # Relationship repair needs the remaining annotation identities on all
    # pages, but never their content, appearance or embedded asset streams.
    budget.check(
        document.page_count, "max_container_items", "annotation cleanup page inventory"
    )
    for index in range(document.page_count):
        if index not in snapshots:
            snapshots[index] = reader.page(index)
    owners = {
        id(snapshots[index][2][position][1])
        for index in selected
        for position in removed[index]
    }
    popup_targets = {
        reader.target(snapshots[index][2][position][1], "Popup")
        for index in selected
        for position in removed[index]
        if snapshots[index][2][position][2] != "Popup"
    }
    for index in selected:
        entries = snapshots[index][2]
        for position, (_reference, obj, kind) in enumerate(entries):
            if kind == "Popup" and (
                id(obj) in popup_targets or reader.target(obj, "Parent") in owners
            ):
                parent = reader.target(obj, "Parent")
                if (
                    id(obj) not in owners
                    and parent is not None
                    and parent not in owners
                ):
                    raise PdfValidationException(
                        "Conflicting popup ownership in annotation cleanup"
                    )
                removed[index].add(position)
    removed_ids, surviving_ids = set(), set()
    for index, (_page, _raw, entries) in snapshots.items():
        for position, (_reference, obj, _kind) in enumerate(entries):
            target = (
                removed_ids if position in removed.get(index, ()) else surviving_ids
            )
            target.add(id(obj))
    detached = removed_ids - surviving_ids
    automatic_popups = {
        id(obj)
        for index, (_page, _raw, entries) in snapshots.items()
        for position, (_reference, obj, kind) in enumerate(entries)
        if kind == "Popup" and position in removed.get(index, ())
    } - owners
    owned = set()
    for index, (_page, _raw, entries) in snapshots.items():
        for position, (_reference, obj, kind) in enumerate(entries):
            if id(obj) in detached and kind != "Popup":
                owned.add(reader.target(obj, "Popup"))
    changes = {}
    for index, (_page, _raw, entries) in snapshots.items():
        for position, (_reference, obj, kind) in enumerate(entries):
            if position in removed.get(index, ()):
                continue
            reader.tick()
            if kind == "Popup" and (
                id(obj) in owned or reader.target(obj, "Parent") in detached
            ):
                raise PdfValidationException(
                    "Cleanup would orphan a popup outside the selected pages"
                )
            replacement = dict(obj.mapping)
            for key in ("IRT", "Popup"):
                target = reader.target(obj, key)
                if target in detached:
                    if (
                        key == "Popup"
                        and target in automatic_popups
                        and id(obj) not in owners
                    ):
                        raise PdfValidationException(
                            "Conflicting popup ownership in annotation cleanup"
                        )
                    replacement.pop(PdfName(key), None)
                    if key == "IRT":
                        replacement.pop(PdfName("RT"), None)
            if replacement != obj.mapping:
                changes[id(obj)] = obj, obj.mapping, replacement
    arrays = []
    count = sum(len(positions) for positions in removed.values())
    for index, positions in removed.items():
        if positions:
            page, raw, entries = snapshots[index]
            arrays.append(
                (
                    page,
                    raw,
                    PdfArray(
                        [
                            reference
                            for position, (reference, _obj, _kind) in enumerate(entries)
                            if position not in positions
                        ]
                    ),
                )
            )
    if dry_run:
        return count
    try:
        for obj, _old, replacement in changes.values():
            obj.mapping = replacement
        for page, _raw, array in arrays:
            page.mapping[_ANNOTS] = array
    except BaseException:
        for obj, old, _replacement in changes.values():
            obj.mapping = old
        for page, raw, _array in arrays:
            if raw is _MISSING:
                page.mapping.pop(_ANNOTS, None)
            else:
                page.mapping[_ANNOTS] = raw
        raise
    return count
