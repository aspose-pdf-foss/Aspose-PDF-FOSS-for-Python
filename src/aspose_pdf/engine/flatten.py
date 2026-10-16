"""Selective appearance baking without discarding other interactive objects."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfParseException, PdfValidationException

from .content_isolation import isolation_for
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)

_ATTRIBUTES = ("FT", "Ff", "V", "DA", "Q", "RV", "Opt", "MaxLen")


def _selection(
    values: Any,
    *,
    names: bool,
    budget: Any,
    page_count: int | None = None,
) -> set:
    label = "fields" if names else "pages"
    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(
        values, Iterable
    ):
        raise TypeError(f"{label} must be an iterable")
    result = set()
    for count, value in enumerate(values, 1):
        budget.check(count, "max_container_items", f"flatten {label}")
        if names:
            if not isinstance(value, str):
                raise TypeError("field names must be strings")
        else:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError("page indices must be integers")
            if value < 0 or (page_count is not None and value >= page_count):
                raise IndexError(f"Page index out of range: {value}")
        result.add(value)
    return result


@dataclass
class _Node:
    entry: PdfDictionary
    attributes: dict
    children: list


class _FieldTree:
    """Index widgets once and prune only branches emptied by successful baking."""

    def __init__(self, pdf: Any):
        self.pdf = pdf
        self.nodes: list[_Node] = []
        self.names: dict[str, int] = {}
        self.owners: dict[int, int | None] = {}
        self.by_id: dict[int, _Node] = {}
        root = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
        self.root = root if isinstance(root, PdfDictionary) else None
        acro = (
            pdf._resolve(root.mapping.get(PdfName("AcroForm"))) if self.root else None
        )
        self.acro = acro if isinstance(acro, PdfDictionary) else None
        self.roots = (
            self._array(acro.mapping.get(PdfName("Fields"))) if self.acro else []
        )
        # Resolve calculation-order entries before any page is edited, so
        # pruning committed widgets cannot discover a late parser/budget error.
        co = pdf._resolve(acro.mapping.get(PdfName("CO"))) if self.acro else None
        self.calculation_order = (
            [(ref, id(pdf._resolve(ref))) for ref in self._array(co)]
            if isinstance(co, PdfArray)
            else None
        )
        inherited = (
            {key: acro.mapping.get(PdfName(key)) for key in ("DA", "Q")}
            if self.acro
            else {}
        )
        stack = [(ref, "", inherited, None, 1) for ref in reversed(self.roots)]
        nonterminal = set()
        candidates = []
        while stack:
            reference, prefix, attributes, owner, depth = stack.pop()
            pdf._load_budget.check(depth, "max_nesting_depth", "flatten field depth")
            entry = pdf._resolve(reference)
            if not isinstance(entry, PdfDictionary) or isinstance(entry, PdfStream):
                raise PdfParseException("Invalid form field dictionary")
            key = id(entry)
            if key in self.by_id:
                raise PdfParseException(
                    "Form field tree contains a cycle or repeated field"
                )
            pdf._load_budget.check(
                len(self.nodes) + 1, "max_container_items", "flatten field nodes"
            )
            attributes = dict(attributes)
            for attr in _ATTRIBUTES:
                if PdfName(attr) in entry.mapping:
                    attributes[attr] = entry.mapping[PdfName(attr)]
            title = pdf._resolve(entry.mapping.get(PdfName("T")))
            name = prefix
            if isinstance(title, PdfString):
                pdf._load_budget.check(
                    len(title.value), "max_object_bytes", "flatten field name"
                )
                partial = decode_pdf_text_string(title)
                name = f"{prefix}.{partial}" if prefix else partial
                pdf._load_budget.check(
                    len(name), "max_object_bytes", "flatten field name"
                )
                if owner is not None:
                    nonterminal.add(owner)
                owner = key
                candidates.append((name, key))
            children = self._array(entry.mapping.get(PdfName("Kids")))
            node = _Node(entry, attributes, children)
            self.nodes.append(node)
            self.by_id[key] = node
            if pdf._resolve(entry.mapping.get(PdfName("Subtype"))) == PdfName("Widget"):
                self.owners[key] = owner
            pdf._load_budget.check(
                len(stack) + len(children), "max_container_items", "flatten field stack"
            )
            stack.extend(
                (ref, name, attributes, owner, depth + 1) for ref in reversed(children)
            )
        for name, key in candidates:
            if key in nonterminal:
                continue
            if name in self.names:
                raise PdfParseException(
                    "Form contains duplicate fully qualified field names"
                )
            self.names[name] = key

    def _array(self, value: Any) -> list:
        value = self.pdf._resolve(value)
        if not isinstance(value, PdfArray):
            return []
        self.pdf._load_budget.check(
            len(value.items), "max_container_items", "flatten field array"
        )
        return value.items

    def generate(self, widget: PdfDictionary) -> None:
        node = self.by_id.get(id(widget))
        if node is None or self.acro is None:
            return
        pdf, attrs = self.pdf, node.attributes

        def number(key: str, default: int = 0) -> int:
            value = pdf._resolve(attrs.get(key))
            if isinstance(value, PdfNumber) and math.isfinite(value.value):
                return int(value.value)
            return default

        ft = pdf._resolve(attrs.get("FT"))
        pdf._set_widget_appearance(
            widget,
            ft.name.lstrip("/") if isinstance(ft, PdfName) else None,
            number("Ff"),
            number("Q"),
            attrs.get("V"),
            pdf._get_cos_string(pdf._resolve(attrs.get("DA"))),
            self.acro,
            attrs.get("Opt"),
            attrs.get("RV"),
            keep_existing=not pdf._need_appearances_requested(),
            max_length=number("MaxLen") or None,
        )

    def prune(self, baked: set[int]) -> None:
        if not baked or self.acro is None:
            return
        removed = set(baked)
        for node in reversed(self.nodes):
            if not node.children:
                continue
            kept = [
                ref
                for ref in node.children
                if id(self.pdf._resolve(ref)) not in removed
            ]
            if len(kept) == len(node.children):
                continue
            if kept:
                node.entry.mapping[PdfName("Kids")] = PdfArray(kept)
            else:
                removed.add(id(node.entry))
        roots = [ref for ref in self.roots if id(self.pdf._resolve(ref)) not in removed]
        self.acro.mapping[PdfName("Fields")] = PdfArray(roots)
        if self.calculation_order is not None:
            self.acro.mapping[PdfName("CO")] = PdfArray(
                [ref for ref, key in self.calculation_order if key not in removed]
            )
        if not roots and PdfName("XFA") not in self.acro.mapping:
            self.root.mapping.pop(PdfName("AcroForm"), None)
            self.pdf._cos_doc.trailer.mapping.pop(PdfName("AcroForm"), None)


def _appearance(pdf: Any, annot: PdfDictionary) -> tuple[Any, PdfStream] | None:
    ap = pdf._resolve(annot.mapping.get(PdfName("AP")))
    if not isinstance(ap, PdfDictionary):
        return None
    reference = ap.mapping.get(PdfName("N"))
    normal = pdf._resolve(reference)
    if isinstance(normal, PdfDictionary) and not isinstance(normal, PdfStream):
        state = pdf._resolve(annot.mapping.get(PdfName("AS")))
        reference = normal.mapping.get(
            state if isinstance(state, PdfName) else PdfName("Off")
        )
        normal = pdf._resolve(reference)
    return (reference, normal) if isinstance(normal, PdfStream) else None


def flatten(
    pdf: Any,
    *,
    fields: bool | Iterable[str] = True,
    annotations: bool = True,
    pages: Iterable[int] | None = None,
    strict: bool = True,
) -> None:
    """Bake selected widgets/annotations; preserve anything without a usable AP."""
    pdf._ensure_not_disposed()
    if not isinstance(annotations, bool) or not isinstance(strict, bool):
        raise TypeError("annotations and strict must be booleans")
    selected = (
        fields
        if isinstance(fields, bool)
        else _selection(fields, names=True, budget=pdf._load_budget)
    )
    page_set = (
        range(len(pdf.pages))
        if pages is None
        else _selection(
            pages,
            names=False,
            budget=pdf._load_budget,
            page_count=len(pdf.pages),
        )
    )
    if not selected and not annotations:
        return
    pdf._ensure_cos()
    tree = _FieldTree(pdf) if selected else None
    if tree is not None and not isinstance(selected, bool):
        if strict:
            for name in selected:
                if name not in tree.names:
                    raise KeyError(f"Field '{name}' not found")
        selected = {tree.names[name] for name in selected if name in tree.names}
    if not page_set:
        return
    baked = set()
    try:
        for pi in sorted(page_set):
            baked.update(_flatten_page(pdf, pi, tree, selected, annotations))
    finally:
        # A later page can fail its limits or content checks. Only widgets
        # actually removed from page Annots may be pruned from the form tree.
        if tree is not None:
            tree.prune(baked)


def _flatten_page(
    pdf: Any,
    pi: int,
    tree: _FieldTree | None,
    selected: bool | set[int],
    annotations: bool,
) -> set[int]:
    """Commit one page and return only its successfully baked widget IDs."""
    page = pdf._get_page_dict(pi)
    if not isinstance(page, PdfDictionary):
        return set()
    annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
    if not isinstance(annots, PdfArray):
        return set()
    pdf._load_budget.check(
        len(annots.items), "max_container_items", "flatten page annotations"
    )
    kept, drawings = [], []
    page_baked = set()
    for ai, reference in enumerate(annots.items):
        annot = pdf._resolve(reference)
        if not isinstance(annot, PdfDictionary):
            kept.append(reference)
            continue
        widget = pdf._resolve(annot.mapping.get(PdfName("Subtype"))) == PdfName(
            "Widget"
        )
        chosen = (
            selected is True or bool(tree and tree.owners.get(id(annot)) in selected)
            if widget
            else annotations
        )
        if not chosen:
            kept.append(reference)
            continue
        flags = pdf._resolve(annot.mapping.get(PdfName("F")))
        if (
            isinstance(flags, PdfNumber)
            and math.isfinite(flags.value)
            and int(flags.value) & ((1 << 1) | (1 << 5))
        ):
            # Hidden/NoView objects must keep their viewing/printing semantics.
            kept.append(reference)
            continue
        rect = pdf._resolve(annot.mapping.get(PdfName("Rect")))
        values = (
            [pdf._resolve(v) for v in rect.items]
            if isinstance(rect, PdfArray) and len(rect.items) == 4
            else []
        )
        if len(values) != 4 or not all(
            isinstance(v, PdfNumber) and math.isfinite(v.value) for v in values
        ):
            kept.append(reference)
            continue
        coords = [float(v.value) for v in values]
        if widget and tree is not None:
            tree.generate(annot)
        elif not widget:
            pdf.generate_annotation_appearance(pi, ai)
        appearance = _appearance(pdf, annot)
        if appearance is None:
            kept.append(reference)
            continue
        ap_ref, normal = appearance
        matrix = pdf._flatten_appearance_matrix(normal, coords)
        if not all(math.isfinite(value) for value in matrix):
            kept.append(reference)
            continue
        drawings.append((ap_ref, normal, matrix))
        if widget:
            page_baked.add(id(annot))
    if not drawings:
        return set()
    current = pdf.get_page_content(pi)
    isolation = isolation_for(current, budget=pdf._load_budget)
    if not isolation.complete:
        raise PdfValidationException(
            f"Cannot flatten page {pi}: its graphics state cannot be safely isolated"
        )
    inherited = pdf._resolve_resources_cos(page)
    resources = PdfDictionary(
        dict(inherited.mapping) if isinstance(inherited, PdfDictionary) else {}
    )
    old_xobjects = pdf._resolve(resources.mapping.get(PdfName("XObject")))
    xobjects = PdfDictionary(
        dict(old_xobjects.mapping) if isinstance(old_xobjects, PdfDictionary) else {}
    )
    resources.mapping[PdfName("XObject")] = xobjects
    content = bytearray(isolation.closing)
    xi = 0
    for ap_ref, normal, matrix in drawings:
        while PdfName(f"FlatAnnot{xi}") in xobjects.mapping:
            xi += 1
        name = PdfName(f"FlatAnnot{xi}")
        if not isinstance(ap_ref, PdfIndirectReference):
            ap_ref = pdf._cos_doc.register_object(normal)
        xobjects.mapping[name] = ap_ref
        content.extend(
            (
                f"\nq\n{' '.join(f'{v:g}' for v in matrix)} cm\n{name.name} Do\nQ\n"
            ).encode("ascii")
        )
    page.mapping[PdfName("Resources")] = resources
    pdf._append_content_to_cos_page(pi, bytes(content), opening=isolation.opening)
    if not pdf._lazy:
        pdf.page_contents[pi] = isolation.opening + current + b"\n" + bytes(content)
    pdf._extracted_text = None
    page.mapping[PdfName("Annots")] = PdfArray(kept)
    return page_baked
