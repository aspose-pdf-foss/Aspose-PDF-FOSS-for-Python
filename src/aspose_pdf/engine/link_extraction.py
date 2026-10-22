"""Inventory link metadata without loading page content or executing actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.interactive import destination_from_spec
from aspose_pdf.link_extraction import ExtractedLink
from aspose_pdf.load_limits import _LoadBudget

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)
from .link_validation import _VIEWS, _absent, _finite, _Problem, _Reader
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


class _Inventory(_Reader):
    """Reuse bounded reference/name-tree reads and local destination validation."""

    def __init__(self, document):
        super().__init__(document)
        self.positions = {
            (number, self.pdf._cos_doc.generation_number(number)): index
            for index, number in enumerate(self.pdf._page_reference_numbers())
        }
        self.records: list[ExtractedLink] = []
        self.text_bytes = 0
        self.base_loaded = False
        self.base = None

    def name(self, value):
        """Keep one literal leading slash in a decoded destination name."""
        if isinstance(value, PdfName):
            name = value.name[1:]
            try:
                size = len(name.encode("utf-8"))
            except UnicodeError:
                raise _Problem("invalid_destination") from None
            self.budget.check(size, "max_object_bytes", "link extraction name")
            return name
        return super().name(value)

    def text(self, raw, code):
        value = self.resolve(raw)
        if not isinstance(value, PdfString):
            raise _Problem(code)
        self.budget.check(len(value.value), "max_object_bytes", "link extraction text")
        return decode_pdf_text_string(value)

    def emit(self, location, path, kind, fields=None, issues=()):
        self.tick()
        fields = fields or {}
        for text in (path, kind, *(v for v in fields.values() if isinstance(v, str))):
            if text is not None:
                self.text_bytes += len(text.encode("utf-8"))
        self.budget.check(
            self.text_bytes, "max_total_decoded_bytes", "link extraction report text"
        )
        self.budget.check(
            len(self.records) + 1, "max_container_items", "link extraction results"
        )
        self.records.append(
            ExtractedLink(
                **location,
                action_path=path,
                action_type=kind,
                **fields,
                issues=tuple(dict.fromkeys(issues)),
            )
        )

    def rect(self, entry):
        value = self.resolve(entry.mapping.get(PdfName("Rect")))
        if not isinstance(value, PdfArray) or len(self.array(value)) != 4:
            raise _Problem("invalid_rect")
        numbers = [self.resolve(raw) for raw in value.items]
        if any(not isinstance(v, PdfNumber) or not _finite(v.value) for v in numbers):
            raise _Problem("invalid_rect")
        x0, y0, x1, y1 = (float(v.value) for v in numbers)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def target(self, raw, remote=False):
        fields = {}
        issues = []
        try:
            value = self.resolve(raw)
            seen = set()
            while True:
                self.tick(len(seen))
                value = self.resolve(value)
                if isinstance(value, (PdfName, PdfString)):
                    name = self.name(value)
                    fields.setdefault("destination_name", name)
                    if remote:
                        return fields, issues
                    encoded = (
                        name.encode("latin-1")
                        if all(ord(c) < 256 for c in name)
                        else name.encode("utf-8")
                    )
                    keys = (
                        (value.value, name)
                        if isinstance(value, PdfString)
                        else (name, encoded)
                    )
                    named = self.named()
                    key = next((key for key in keys if key in named), None)
                    if key is None:
                        raise _Problem("missing_named_destination")
                    identity = ("name", key)
                    next_value = named[key]
                elif isinstance(value, PdfDictionary) and not isinstance(
                    value, PdfStream
                ):
                    identity = ("dict", id(value))
                    next_value = value.mapping.get(PdfName("D"))
                else:
                    break
                if identity in seen:
                    raise _Problem("cyclic_reference")
                seen.add(identity)
                value = next_value
            if _absent(value):
                raise _Problem("missing_target")
            if not isinstance(value, PdfArray) or len(self.array(value)) < 2:
                raise _Problem("invalid_destination")
            if remote:
                page = self.resolve(value.items[0])
                if (
                    not isinstance(page, PdfNumber)
                    or not _finite(page.value)
                    or page.value < 0
                    or int(page.value) != page.value
                ):
                    raise _Problem("invalid_destination")
                index = int(page.value)
                kind = self.resolve(value.items[1])
                if not isinstance(kind, PdfName) or kind.name[1:] not in _VIEWS:
                    raise _Problem("invalid_destination")
                if len(value.items) != _VIEWS[kind.name[1:]] + 2:
                    raise _Problem("invalid_destination")
            else:
                self.destination(value)
                page = value.items[0]
                index = self.positions[(page.object_number, page.gen_number)]
                kind = self.resolve(value.items[1])
                if kind.name[1:] not in _VIEWS:
                    raise _Problem("invalid_destination")
            params = []
            for raw_param in value.items[2:]:
                param = self.resolve(raw_param)
                if isinstance(param, PdfNull) and kind != PdfName("FitR"):
                    params.append(None)
                elif isinstance(param, PdfNumber) and _finite(param.value):
                    params.append(param.value)
                else:
                    raise _Problem("invalid_destination")
            if kind == PdfName("XYZ") and params[2] is not None and params[2] < 0:
                raise _Problem("invalid_destination")
            if kind == PdfName("FitR") and (
                params[0] >= params[2] or params[1] >= params[3]
            ):
                raise _Problem("invalid_destination")
            fields["destination"] = destination_from_spec(kind.name[1:], index, params)
        except _Problem as problem:
            issues.append(problem.code)
        return fields, issues

    def file_spec(self, raw):
        value = self.resolve(raw)
        if isinstance(value, PdfDictionary) and not isinstance(value, PdfStream):
            unicode_file = self.resolve(value.mapping.get(PdfName("UF")))
            value = (
                value.mapping.get(PdfName("F"))
                if _absent(unicode_file)
                else unicode_file
            )
        return self.text(value, "invalid_file")

    def uri_base(self):
        if not self.base_loaded:
            root = self.resolve(self.pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
            if not isinstance(root, PdfDictionary) or isinstance(root, PdfStream):
                raise _Problem("invalid_uri_base")
            uri = self.resolve(root.mapping.get(PdfName("URI")))
            if not _absent(uri):
                if not isinstance(uri, PdfDictionary) or isinstance(uri, PdfStream):
                    raise _Problem("invalid_uri_base")
                base = self.resolve(uri.mapping.get(PdfName("Base")))
                if not _absent(base):
                    self.base = self.text(base, "invalid_uri_base")
            self.base_loaded = True
        return self.base

    def action_fields(self, action, kind):
        fields, issues = {}, []
        if kind in ("GoTo", "GoToR"):
            fields, issues = self.target(
                action.mapping.get(PdfName("D")), kind == "GoToR"
            )
        if kind in ("GoToR", "Launch", "SubmitForm"):
            try:
                fields["file"] = self.file_spec(action.mapping.get(PdfName("F")))
            except _Problem as problem:
                issues.append(problem.code)
        if kind == "URI":
            try:
                fields["uri"] = self.text(
                    action.mapping.get(PdfName("URI")), "invalid_uri"
                )
                base = self.uri_base()
                fields["resolved_uri"] = (
                    urljoin(base, fields["uri"]) if base is not None else fields["uri"]
                )
            except _Problem as problem:
                issues.append(problem.code)
            except ValueError:
                issues.append("invalid_uri")
        if kind == "Named":
            try:
                name = self.resolve(action.mapping.get(PdfName("N")))
                if not isinstance(name, PdfName):
                    raise _Problem("invalid_action")
                fields["named_action"] = self.name(name)
            except _Problem as problem:
                issues.append(problem.code)
        return fields, issues

    def actions_inventory(self, raw, location, initial_issues):
        stack = [(raw, "A", 1, False)]
        active = set()
        while stack:
            raw, path, depth, leaving = stack.pop()
            self.tick(depth)
            if leaving:
                active.remove(raw)
                continue
            try:
                action = self.resolve(raw)
                if not isinstance(action, PdfDictionary) or isinstance(
                    action, PdfStream
                ):
                    raise _Problem("invalid_action")
                identity = id(action)
                if identity in active:
                    raise _Problem("cyclic_reference")
                active.add(identity)
                stack.append((identity, path, depth, True))
                try:
                    kind = self.resolve(action.mapping.get(PdfName("S")))
                    if not isinstance(kind, PdfName):
                        raise _Problem("invalid_action")
                    action_name = self.name(kind)
                    fields, issues = self.action_fields(action, action_name)
                except _Problem as problem:
                    self.emit(
                        location, path, None, issues=(*initial_issues, problem.code)
                    )
                else:
                    self.emit(
                        location,
                        path,
                        action_name,
                        fields,
                        (*initial_issues, *issues),
                    )
                try:
                    following = self.resolve(action.mapping.get(PdfName("Next")))
                except _Problem as problem:
                    self.emit(
                        location,
                        path + ".Next",
                        None,
                        issues=(*initial_issues, problem.code),
                    )
                    continue
                if not _absent(following):
                    children = (
                        self.array(following)
                        if isinstance(following, PdfArray)
                        else [following]
                    )
                    self.budget.check(
                        len(stack) + len(children),
                        "max_container_items",
                        "link extraction action stack",
                    )
                    stack.extend(
                        (child, path + f".Next[{index}]", depth + 1, False)
                        for index, child in reversed(list(enumerate(children)))
                    )
            except _Problem as problem:
                self.emit(location, path, None, issues=(*initial_issues, problem.code))

    def link(self, entry, page_index, annotation_index):
        issues = []
        try:
            rect = self.rect(entry)
        except _Problem as problem:
            rect = None
            issues.append(problem.code)
        location = dict(
            page_index=page_index, annotation_index=annotation_index, rect=rect
        )
        dest, action = (entry.mapping.get(PdfName(key)) for key in ("Dest", "A"))

        def present(raw):
            try:
                return not _absent(self.resolve(raw))
            except _Problem:
                return True

        has_dest = present(dest)
        has_action = present(action)
        if has_dest and has_action:
            issues.append("conflicting_targets")
        if has_dest:
            fields, target_issues = self.target(dest)
            self.emit(location, "Dest", "GoTo", fields, (*issues, *target_issues))
        if has_action:
            self.actions_inventory(action, location, issues)
        if not has_dest and not has_action:
            self.emit(location, "Dest/A", None, issues=(*issues, "missing_target"))


def extract(document: Document, pages: Any) -> list[ExtractedLink]:
    """Return links in first-selected page order, annotation order, action preorder."""
    pdf = document._engine_pdf
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check(
        len(selection), "max_container_items", "link extraction page selection"
    )
    budget.check_pages(len(selection))
    if not selection:
        return []
    pdf._ensure_cos()
    reader = _Inventory(document)
    try:
        for index in dict.fromkeys(selection):
            reader.tick()
            page = pdf._get_page_dict(index)
            if not isinstance(page, PdfDictionary):
                raise _Problem("invalid_structure")
            annotations = reader.resolve(page.mapping.get(PdfName("Annots")))
            if _absent(annotations):
                continue
            if not isinstance(annotations, PdfArray):
                raise _Problem("invalid_structure")
            for offset, raw in enumerate(reader.array(annotations)):
                reader.tick()
                entry = reader.resolve(raw)
                if not isinstance(entry, PdfDictionary) or isinstance(entry, PdfStream):
                    raise _Problem("invalid_structure")
                kind = reader.resolve(entry.mapping.get(PdfName("Subtype")))
                if not isinstance(kind, PdfName):
                    raise _Problem("invalid_structure")
                if kind == PdfName("Link"):
                    reader.link(entry, index, offset)
    except _Problem as problem:
        raise PdfParseException(
            f"Invalid link annotation metadata: {problem.code}"
        ) from None
    return reader.records
