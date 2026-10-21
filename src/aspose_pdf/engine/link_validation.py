"""Read local navigation metadata without decoding content or executing actions."""

from __future__ import annotations

import math
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import Action, Destination, GoToAction
from aspose_pdf.link_validation import LinkValidationIssue
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.outlines import OutlineItem

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from .text_selection import page_indexes

_VIEWS = {
    "Fit": 0,
    "FitB": 0,
    "XYZ": 3,
    "FitH": 1,
    "FitBH": 1,
    "FitV": 1,
    "FitBV": 1,
    "FitR": 4,
}
_MESSAGES = {
    "missing_target": "A link or GoTo action has no destination",
    "missing_page": "The destination page reference is not in the current page tree",
    "missing_named_destination": "The destination name is not defined",
    "invalid_destination": "The destination has an invalid type, view or parameters",
    "invalid_action": "The action or its Next entry has an invalid structure",
    "conflicting_targets": "Both Dest and A are present on the same navigation source",
    "cyclic_reference": "The navigation metadata contains a reference cycle",
    "invalid_structure": "The navigation metadata has an invalid structure",
}
_MISSING_REFERENCE = object()


class _Problem(Exception):
    def __init__(self, code: str, name: str | None = None):
        self.code, self.name = code, name


class _Reader:
    def __init__(self, document: Any):
        self.document = document
        self.pdf = document._engine_pdf
        self.budget = _LoadBudget(self.pdf._load_limits)
        self.budget.check(
            self.pdf.page_count, "max_container_items", "link validation page index"
        )
        self.work = 0
        self.issues: list[LinkValidationIssue] = []
        self.names: dict[Any, Any] | None = None
        self.name_problems: list[tuple[str, str]] = []
        self.pages = {
            (number, self.pdf._cos_doc.generation_number(number))
            for number in (
                self.pdf._page_reference_numbers() if self.pdf.page_count else []
            )
        }

    def tick(self, depth: int = 0) -> None:
        self.work += 1
        self.budget.check(
            self.work, "max_container_items", "link validation metadata work"
        )
        self.budget.check(depth, "max_nesting_depth", "link validation metadata depth")

    def resolve(self, value: Any) -> Any:
        seen = set()
        while isinstance(value, PdfIndirectReference):
            self.tick(len(seen) + 1)
            key = value.object_number, value.gen_number
            if key in seen:
                raise _Problem("cyclic_reference")
            seen.add(key)
            value = self.pdf._resolve(value)
            if value is None:
                return _MISSING_REFERENCE
        return value

    def array(self, value: PdfArray) -> list:
        self.budget.check(
            len(value.items), "max_container_items", "link validation array"
        )
        return value.items

    def name(self, value: PdfName | PdfString) -> str:
        raw = value.value if isinstance(value, PdfString) else value.name.lstrip("/")
        try:
            size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        except UnicodeError:
            raise _Problem("invalid_destination") from None
        self.budget.check(size, "max_object_bytes", "link validation destination name")
        return raw.decode("latin-1") if isinstance(raw, bytes) else raw

    def add(
        self, code: str, location: dict, path: str, name: str | None = None
    ) -> None:
        self.budget.check(
            len(self.issues) + 1, "max_container_items", "link validation issues"
        )
        self.issues.append(
            LinkValidationIssue(
                code=code,
                message=_MESSAGES[code],
                target_path=path,
                **{
                    **location,
                    **(
                        {"destination_name": name}
                        if name is not None and "destination_name" not in location
                        else {}
                    ),
                },
            )
        )

    def check(self, operation: Any, location: dict, path: str) -> None:
        try:
            operation()
        except _Problem as problem:
            self.add(problem.code, location, path, problem.name)

    def named(self) -> dict[Any, Any]:
        if self.names is not None:
            return self.names
        self.names = {}
        try:
            self.read_named()
        except _Problem as problem:
            self.name_problems.append(("Dests", problem.code))
        return self.names

    def read_named(self) -> None:
        found: dict[Any, Any] = {}
        self.names = found
        if self.pdf._cos_doc is None:
            return
        catalog = self.resolve(self.pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(catalog, PdfDictionary) or isinstance(catalog, PdfStream):
            raise _Problem("invalid_structure")

        def problem(path: str, code: str = "invalid_structure") -> None:
            self.tick()
            self.name_problems.append((path, code))

        legacy = self.resolve(catalog.mapping.get(PdfName("Dests")))
        if not _absent(legacy):
            if not isinstance(legacy, PdfDictionary) or isinstance(legacy, PdfStream):
                problem("Dests")
            else:
                self.budget.check(
                    len(legacy.mapping), "max_container_items", "legacy destinations"
                )
                for key, value in legacy.mapping.items():
                    self.tick()
                    if isinstance(key, PdfName):
                        found[self.name(key)] = value
                    else:
                        problem("Dests")
        names = self.resolve(catalog.mapping.get(PdfName("Names")))
        if _absent(names):
            return
        if not isinstance(names, PdfDictionary) or isinstance(names, PdfStream):
            problem("Names")
            return
        root = self.resolve(names.mapping.get(PdfName("Dests")))
        if _absent(root):
            return
        stack = [(root, 1)]
        seen = set()
        previous: bytes | None = None
        while stack:
            raw, depth = stack.pop()
            self.tick(depth)
            try:
                node = self.resolve(raw)
            except _Problem as error:
                problem("Names.Dests", error.code)
                continue
            if not isinstance(node, PdfDictionary) or isinstance(node, PdfStream):
                problem("Names.Dests")
                continue
            if id(node) in seen:
                problem("Names.Dests", "cyclic_reference")
                continue
            seen.add(id(node))
            pairs = self.resolve(node.mapping.get(PdfName("Names")))
            kids = self.resolve(node.mapping.get(PdfName("Kids")))
            if not _absent(pairs):
                if not isinstance(pairs, PdfArray):
                    problem("Names.Dests.Names")
                else:
                    items = self.array(pairs)
                    if len(items) % 2:
                        problem("Names.Dests.Names")
                    for offset in range(0, len(items) - 1, 2):
                        self.tick()
                        key = self.resolve(items[offset])
                        if not isinstance(key, PdfString):
                            problem("Names.Dests.Names")
                            continue
                        self.name(key)
                        if previous is not None and key.value <= previous:
                            problem("Names.Dests.Names")
                        previous = key.value
                        found.setdefault(key.value, items[offset + 1])
            if not _absent(kids):
                if not isinstance(kids, PdfArray):
                    problem("Names.Dests.Kids")
                else:
                    children = self.array(kids)
                    self.budget.check(
                        len(stack) + len(children),
                        "max_container_items",
                        "destination tree stack",
                    )
                    stack.extend((child, depth + 1) for child in reversed(children))
            if (_absent(kids) and _absent(pairs)) or (
                not _absent(kids) and not _absent(pairs)
            ):
                problem("Names.Dests")

    def destination(self, value: Any) -> None:
        seen = set()
        name = None
        while True:
            self.tick(len(seen))
            value = self.resolve(value)
            if isinstance(value, (PdfName, PdfString)):
                name = self.name(value)
                if isinstance(value, PdfString):
                    keys = value.value, name
                else:
                    try:
                        raw_name = name.encode("latin-1")
                    except UnicodeError:
                        raw_name = name.encode("utf-8")
                    keys = name, raw_name
                named = self.named()
                key = next((key for key in keys if key in named), None)
                if key is None:
                    raise _Problem("missing_named_destination", name)
                identity = ("name", key)
                if identity in seen:
                    raise _Problem("cyclic_reference", name)
                seen.add(identity)
                value = named[key]
            elif isinstance(value, PdfDictionary) and not isinstance(value, PdfStream):
                identity = ("dict", id(value))
                if identity in seen:
                    raise _Problem("cyclic_reference", name)
                seen.add(identity)
                value = value.mapping.get(PdfName("D"))
            else:
                break
        if not isinstance(value, PdfArray):
            raise _Problem("invalid_destination", name)
        items = self.array(value)
        if len(items) < 2:
            raise _Problem("invalid_destination", name)
        page = items[0]
        if not isinstance(page, PdfIndirectReference):
            raise _Problem("invalid_destination", name)
        if (page.object_number, page.gen_number) not in self.pages:
            raise _Problem("missing_page", name)
        kind = self.resolve(items[1])
        if not isinstance(kind, PdfName) or kind.name.lstrip("/") not in _VIEWS:
            raise _Problem("invalid_destination", name)
        kind = kind.name.lstrip("/")
        if len(items) != _VIEWS[kind] + 2:
            raise _Problem("invalid_destination", name)
        params = []
        for raw in items[2:]:
            param = self.resolve(raw)
            if isinstance(param, PdfNull) and kind != "FitR":
                params.append(None)
            elif isinstance(param, PdfNumber) and _finite(param.value):
                params.append(param.value)
            else:
                raise _Problem("invalid_destination", name)
        if kind == "XYZ" and params[2] is not None and params[2] < 0:
            raise _Problem("invalid_destination", name)
        if kind == "FitR" and (params[0] >= params[2] or params[1] >= params[3]):
            raise _Problem("invalid_destination", name)

    def actions(self, raw: Any, location: dict, path: str) -> None:
        stack = [(raw, path, 1, False)]
        active, checked = set(), set()
        while stack:
            raw, current_path, depth, leaving = stack.pop()
            self.tick(depth)
            if leaving:
                active.remove(raw)
                checked.add(raw)
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
                if identity in checked:
                    continue
                active.add(identity)
                stack.append((identity, current_path, depth, True))
                kind = self.resolve(action.mapping.get(PdfName("S")))
                if not isinstance(kind, PdfName):
                    raise _Problem("invalid_action")
                if kind == PdfName("GoTo"):
                    dest = action.mapping.get(PdfName("D"))
                    if _absent(self.resolve(dest)):
                        self.add("missing_target", location, current_path + ".D")
                    else:
                        self.check(
                            lambda: self.destination(dest),
                            location,
                            current_path + ".D",
                        )
                following = self.resolve(action.mapping.get(PdfName("Next")))
                if not _absent(following):
                    children = (
                        self.array(following)
                        if isinstance(following, PdfArray)
                        else [following]
                    )
                    self.budget.check(
                        len(stack) + len(children),
                        "max_container_items",
                        "action validation stack",
                    )
                    stack.extend(
                        (child, current_path + f".Next[{index}]", depth + 1, False)
                        for index, child in reversed(list(enumerate(children)))
                    )
            except _Problem as problem:
                self.add(problem.code, location, current_path, problem.name)

    def targets(self, entry: PdfDictionary, location: dict, *, required: bool) -> None:
        dest, action = (
            entry.mapping.get(PdfName("Dest")),
            entry.mapping.get(PdfName("A")),
        )
        has_dest, has_action = (
            not _absent(self.resolve(dest)),
            not _absent(self.resolve(action)),
        )
        if has_dest and has_action:
            self.add("conflicting_targets", location, "Dest/A")
        if has_dest:
            self.check(lambda: self.destination(dest), location, "Dest")
        if has_action:
            self.actions(action, location, "A")
        if required and not has_dest and not has_action:
            self.add("missing_target", location, "Dest/A")

    def annotations(self, selected: list[int]) -> None:
        for index in selected:
            self.tick()
            location = {"source": "annotation", "page_index": index}
            try:
                page = self.pdf._get_page_dict(index)
                if not isinstance(page, PdfDictionary):
                    raise _Problem("invalid_structure")
                entries = self.resolve(page.mapping.get(PdfName("Annots")))
                if _absent(entries):
                    continue
                if not isinstance(entries, PdfArray):
                    raise _Problem("invalid_structure")
                for offset, raw in enumerate(self.array(entries)):
                    self.tick()
                    item_location = {**location, "annotation_index": offset}
                    try:
                        entry = self.resolve(raw)
                        if not isinstance(entry, PdfDictionary) or isinstance(
                            entry, PdfStream
                        ):
                            raise _Problem("invalid_structure")
                        if self.resolve(
                            entry.mapping.get(PdfName("Subtype"))
                        ) == PdfName("Link"):
                            self.targets(entry, item_location, required=True)
                    except _Problem as problem:
                        self.add(problem.code, item_location, "Annots", problem.name)
            except _Problem as problem:
                self.add(problem.code, location, "Annots", problem.name)

    def outlines(self) -> None:
        roots = (
            self.document._outlines._items
            if self.document._outlines is not None
            else self.pdf._outlines_data
        )
        self.budget.check(len(roots), "max_container_items", "outline validation roots")
        stack = [(item, (index,)) for index, item in reversed(list(enumerate(roots)))]
        seen = set()
        while stack:
            item, path = stack.pop()
            self.tick(len(path))
            location = {"source": "outline", "outline_path": path}
            if id(item) in seen:
                self.add("cyclic_reference", location, "children")
                continue
            seen.add(id(item))
            if isinstance(item, OutlineItem):
                loaded, target, page, children = (
                    item._loaded_target,
                    item.destination,
                    item.page_index,
                    item.children,
                )
            elif isinstance(item, dict):
                loaded, target, page, children = (
                    item.get("loaded_target"),
                    item.get("target"),
                    item.get("page_index"),
                    item.get("children", []),
                )
            else:
                self.add("invalid_structure", location, "children")
                continue
            try:
                if loaded is not None:
                    key, raw = loaded
                    self.targets(
                        PdfDictionary({PdfName(key): raw}), location, required=False
                    )
                elif target is not None:
                    self.typed_target(target, location)
                elif page is not None and (
                    type(page) is not int or not 0 <= page < self.pdf.page_count
                ):
                    self.add("missing_page", location, "page_index")
            except _Problem as problem:
                self.add(problem.code, location, "target", problem.name)
            if not isinstance(children, (list, tuple)):
                self.add("invalid_structure", location, "children")
                continue
            self.budget.check(
                len(stack) + len(children),
                "max_container_items",
                "outline validation stack",
            )
            stack.extend(
                (child, (*path, index))
                for index, child in reversed(list(enumerate(children)))
            )

    def typed_target(self, target: Any, location: dict) -> None:
        is_action = isinstance(target, GoToAction)
        target = target.destination if is_action else target
        path = "A.D" if is_action else "Dest"
        if isinstance(target, str):
            try:
                raw = PdfString(target.encode("latin-1"))
            except UnicodeError:
                raise _Problem("invalid_destination") from None
            self.check(lambda: self.destination(raw), location, path)
        elif isinstance(target, Destination):
            dest = target
            if type(dest.page) is not int or not 0 <= dest.page < self.pdf.page_count:
                raise _Problem("missing_page")
            try:
                kind, params = dest._spec()
            except NotImplementedError:
                raise _Problem("invalid_destination") from None
            if any(value is not None and not _finite(value) for value in params):
                raise _Problem("invalid_destination")
            raw = PdfArray(
                [
                    self.pdf._cos_doc.reference(
                        self.pdf._page_reference_numbers()[dest.page]
                    ),
                    PdfName(kind),
                    *[
                        PdfNull() if value is None else PdfNumber(value)
                        for value in params
                    ],
                ]
            )
            self.check(lambda: self.destination(raw), location, path)
        elif is_action or not isinstance(target, Action):
            raise _Problem("invalid_destination")


def _absent(value: Any) -> bool:
    return value is None or isinstance(value, PdfNull)


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate(
    document: Any, pages: Any, include_outlines: bool, include_named_destinations: bool
) -> list[LinkValidationIssue]:
    """Return diagnostics in page order, outline preorder, then definition order."""
    for name, value in (
        ("include_outlines", include_outlines),
        ("include_named_destinations", include_named_destinations),
    ):
        if type(value) is not bool:
            raise PdfValidationException(f"{name} must be a boolean")
    pdf = document._engine_pdf
    indices = page_indexes(pages, pdf.page_count, limits=pdf._load_limits)
    _LoadBudget(pdf._load_limits).check(
        len(indices) if indices is not None else pdf.page_count,
        "max_container_items",
        "link validation page selection",
    )
    selected = list(
        dict.fromkeys(indices if indices is not None else range(pdf.page_count))
    )
    if not selected and not include_outlines and not include_named_destinations:
        return []
    reader = _Reader(document)
    reader.budget.check(
        len(selected), "max_container_items", "link validation page selection"
    )
    reader.annotations(selected)
    if include_outlines:
        reader.outlines()
    if include_named_destinations:
        try:
            named = reader.named()
            for key, value in named.items():
                name = key.decode("latin-1") if isinstance(key, bytes) else key
                location = {"source": "named_destination", "destination_name": name}
                path = "Names.Dests" if isinstance(key, bytes) else "Dests"
                reader.check(lambda: reader.destination(value), location, path)
            for path, code in reader.name_problems:
                reader.add(code, {"source": "named_destination"}, path)
        except _Problem as problem:
            reader.add(
                problem.code, {"source": "named_destination"}, "Dests", problem.name
            )
    return reader.issues
