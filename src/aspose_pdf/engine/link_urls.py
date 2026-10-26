"""Plan isolated primary Link URI edits before changing annotation dictionaries."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

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
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _text_size(text, budget, context):
    if not isinstance(text, str):
        raise PdfValidationException(f"{context} must be text")
    budget.check(len(text), "max_object_bytes", context)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise PdfValidationException(f"{context} contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", context)


def _matcher(search, replacement, regex, case_sensitive, budget):
    _text_size(replacement, budget, "link URL replacement")
    if isinstance(search, re.Pattern):
        source, flags, is_regex = search.pattern, search.flags, True
    elif isinstance(search, str):
        source, flags, is_regex = search, 0, regex
    else:
        raise PdfValidationException("search must be text or a compiled text regular expression")
    _text_size(source, budget, "link URL search")
    if not source:
        raise PdfValidationException("search must not be empty")
    if not is_regex:
        source = re.escape(source)
        _text_size(source, budget, "link URL search")
    if not case_sensitive:
        flags |= re.IGNORECASE
    try:
        matcher = re.compile(source, flags)
        if is_regex:
            # Validate replacement references even when there are no links or matches.
            matcher.sub(replacement, "")
    except (re.error, ValueError, IndexError, OverflowError, RecursionError) as exc:
        raise PdfValidationException(f"Invalid link URL substitution: {exc}") from exc
    return matcher, is_regex


class _Reader:
    def __init__(self, pdf, budget):
        self.pdf, self.budget, self.work = pdf, budget, 0

    def tick(self, depth=0):
        self.work += 1
        self.budget.check(self.work, "max_container_items", "link URL metadata work")
        self.budget.check(depth, "max_nesting_depth", "link URL reference depth")

    def resolve(self, value):
        seen = set()
        while isinstance(value, PdfIndirectReference):
            self.tick(len(seen) + 1)
            key = value.object_number, value.gen_number
            if key in seen:
                raise PdfParseException("Link URL metadata contains a reference cycle")
            seen.add(key)
            value = self.pdf._resolve(value)
            if value is None:
                raise PdfParseException("Link URL metadata reference was not found")
        return value

    def dictionary(self, value, label):
        if not isinstance(value, PdfDictionary) or isinstance(value, PdfStream):
            raise PdfParseException(f"Link URL {label} must be a dictionary")
        self.budget.check(len(value.mapping), "max_container_items", f"link URL {label}")
        return value

    def page(self, index):
        self.tick()
        page = self.dictionary(self.pdf._get_page_dict(index), "page")
        annotations = self.resolve(page.mapping.get(PdfName("Annots")))
        if annotations is None or isinstance(annotations, PdfNull):
            return []
        if not isinstance(annotations, PdfArray):
            raise PdfParseException("Link URL Annots must be an array")
        self.budget.check(len(annotations.items), "max_container_items", "link URL annotations")
        entries = []
        for raw in annotations.items:
            self.tick()
            entry = self.dictionary(self.resolve(raw), "annotation")
            kind = self.resolve(entry.mapping.get(PdfName("Subtype")))
            if not isinstance(kind, PdfName) or not kind.name[1:]:
                raise PdfParseException("Link URL annotation Subtype must be a nonempty name")
            entries.append((entry, kind))
        return entries

    def uri(self, action):
        value = self.resolve(action.mapping.get(PdfName("URI")))
        if not isinstance(value, PdfString):
            raise PdfParseException("Link URI must be a PDF string")
        self.budget.check(len(value.value), "max_object_bytes", "link URL source")
        for prefix, encoding in ((b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le"), (b"\xef\xbb\xbf", "utf-8")):
            if value.value.startswith(prefix):
                try:
                    return value.value[len(prefix):].decode(encoding)
                except UnicodeError as exc:
                    raise PdfParseException("Link URI contains invalid encoded text") from exc
        return decode_pdf_text_string(value)


def _replace(uri, matcher, replacement, is_regex, reader):
    """Bound every output chunk before joining an expanded replacement."""
    chunks = []
    cursor = characters = utf16_bytes = 0
    ascii_only = True
    for match in matcher.finditer(uri):
        reader.tick()
        if is_regex:
            reader.budget.check(
                8 * len(replacement) * (len(uri) + 1),
                "max_codec_work_bytes", "link URL regex expansion",
            )
        inserted = match.expand(replacement) if is_regex else replacement
        if inserted == match.group():
            continue
        for chunk in (uri[cursor:match.start()], inserted):
            characters += len(chunk)
            reader.budget.check(characters, "max_object_bytes", "link URL result")
            utf16_bytes += len(chunk.encode("utf-16-be"))
            ascii_only = ascii_only and chunk.isascii()
            size = characters if ascii_only else utf16_bytes + 2
            reader.budget.check(size, "max_object_bytes", "link URL result")
            chunks.append(chunk)
        cursor = match.end()
    if not chunks:
        return uri, 0
    tail = uri[cursor:]
    characters += len(tail)
    reader.budget.check(characters, "max_object_bytes", "link URL result")
    utf16_bytes += len(tail.encode("utf-16-be"))
    size = characters if ascii_only and tail.isascii() else utf16_bytes + 2
    reader.budget.check(size, "max_object_bytes", "link URL result")
    chunks.append(tail)
    result = "".join(chunks)
    if not result:
        raise PdfValidationException("Link URL replacement must not produce an empty URI")
    return result, size


def replace_link_urls(
    document: Document,
    search: str | re.Pattern[str],
    replacement: str,
    *,
    pages: Iterable[int] | None = None,
    regex: bool = False,
    case_sensitive: bool = True,
    dry_run: bool = False,
) -> int:
    """Rewrite primary URI actions once per annotation, preserving other owners."""
    budget = _LoadBudget(document.load_limits)
    for name, value in (("regex", regex), ("case_sensitive", case_sensitive), ("dry_run", dry_run)):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    matcher, is_regex = _matcher(search, replacement, regex, case_sensitive, budget)
    indexes = page_indexes(pages, document.page_count, limits=budget.limits)
    indexes = range(document.page_count) if indexes is None else indexes
    budget.check(len(indexes), "max_container_items", "link URL page selection")
    if not indexes:
        return 0
    selected = set(indexes)
    reader = _Reader(document._engine_pdf, budget)
    changes = {}
    retained = 0
    for index in dict.fromkeys(indexes):
        for entry, kind in reader.page(index):
            if kind != PdfName("Link") or id(entry) in changes:
                continue
            action = reader.resolve(entry.mapping.get(PdfName("A")))
            if action is None or isinstance(action, PdfNull):
                continue
            reader.dictionary(action, "action")
            action_kind = reader.resolve(action.mapping.get(PdfName("S")))
            if not isinstance(action_kind, PdfName) or not action_kind.name[1:]:
                raise PdfParseException("Link action S must be a nonempty PDF name")
            if action_kind != PdfName("URI"):
                continue
            uri = reader.uri(action)
            result, size = _replace(uri, matcher, replacement, is_regex, reader)
            if result == uri:
                continue
            retained += size
            budget.check(retained, "max_total_decoded_bytes", "link URL planned text")
            copied_action = dict(action.mapping)
            copied_action[PdfName("URI")] = PdfString(result)
            copied_entry = dict(entry.mapping)
            copied_entry[PdfName("A")] = PdfDictionary(copied_action)
            changes[id(entry)] = entry, entry.mapping, copied_entry
    if not changes:
        return 0
    # Changing a shared annotation itself would escape the selected pages.
    # Keep its identity (including popup/tag references) and refuse ambiguity.
    for index in range(document.page_count):
        if index not in selected:
            for entry, _kind in reader.page(index):
                if id(entry) in changes:
                    raise PdfValidationException(
                        "A matching Link annotation is shared with an unselected page"
                    )
    cos = document._engine_pdf._cos_doc
    budget.check_objects(len(cos.objects) + len(changes))
    first_number = max(max(cos.objects, default=0), max(cos.generations, default=0)) + 1
    budget.check_object_id(first_number + len(changes) - 1)
    if dry_run:
        return len(changes)
    allocated = []
    try:
        for _entry, _old, mapping in changes.values():
            action = mapping[PdfName("A")]
            # Register copies as separate objects so a shared indirect action
            # does not become deeply embedded in its annotation dictionary.
            number = first_number + len(allocated)
            allocated.append((number, action))
            reference = cos.register_object(action)
            if reference.object_number != number:
                raise PdfValidationException("Link action allocation changed during URL replacement")
            mapping[PdfName("A")] = reference
        for entry, _old, mapping in changes.values():
            entry.mapping = mapping
    except BaseException:
        for entry, old, _mapping in changes.values():
            entry.mapping = old
        for number, action in allocated:
            if cos.objects.get(number) is action:
                del cos.objects[number]
        raise
    return len(changes)
