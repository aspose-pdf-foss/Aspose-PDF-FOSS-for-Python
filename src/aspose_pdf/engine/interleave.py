"""Assemble front/back scans by importing each source once and ordering pages."""

from __future__ import annotations

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from . import page_labels
from .bookmark_split import _snapshot
from .cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from .simple_pdf import (
    _UNRESOLVED_DESTINATION,
    SimplePdf,
    action_from_cos,
    destination_from_cos,
)


def _check_objects(pdf: SimplePdf) -> None:
    """Bound the imported output before it can be returned to the caller."""
    doc, budget = pdf._cos_doc, pdf._load_budget
    budget.check_objects(len(doc.objects))
    budget.check_object_id(max(doc.objects, default=0))
    for value in doc.objects.values():
        if isinstance(value, PdfStream):
            budget.check(
                len(value.content), "max_object_bytes", "interleaved stream bytes"
            )
    for content in pdf.page_contents:
        budget.check(
            len(content), "max_content_stream_bytes", "interleaved page content"
        )
        budget.check(
            len(content), "max_decoded_stream_bytes", "interleaved page content"
        )
    budget.check(
        sum(map(len, pdf.page_contents)),
        "max_total_decoded_bytes",
        "interleaved page content",
    )


def _import_source(result: SimplePdf, source: SimplePdf, outlines: list | None) -> None:
    """Keep live outline targets and source state while using the shared importer."""
    original = source._outlines_data
    roots = original if outlines is None else outlines
    snapshot, _starts = _snapshot(source, roots, 1)
    result._load_budget.check(
        len(result._outlines_data) + len(snapshot),
        "max_container_items",
        "interleaved bookmark roots",
    )
    stack = [(entry, 1) for entry in [*result._outlines_data, *snapshot]]
    count = 0
    while stack:
        entry, depth = stack.pop()
        count += 1
        result._load_budget.check(
            depth, "max_nesting_depth", "interleaved bookmark depth"
        )
        result._load_budget.check(count, "max_container_items", "interleaved bookmarks")
        stack.extend((child, depth + 1) for child in entry["children"])
    try:
        source._outlines_data = snapshot
        result.append(source)
    finally:
        source._outlines_data = original
    _check_objects(result)


def _order_pages(pdf: SimplePdf, order: list[int]) -> None:
    """Permute a freshly imported flat tree without changing page identities."""
    pdf._ensure_page_cache()
    refs = list(pdf._page_refs)
    if len(refs) != len(order):
        raise PdfParseException(
            "Imported page count does not match the interleave plan"
        )
    labels = pdf._page_label_values()
    catalog = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    tree_ref = catalog.mapping.get(PdfName("Pages"))
    tree = pdf._resolve(tree_ref)
    if not isinstance(tree, PdfDictionary):
        raise PdfParseException("Imported document has no page tree")
    ordered = [refs[index] for index in order]
    tree.mapping[PdfName("Kids")] = PdfArray(
        [pdf._cos_doc.reference(number) for number in ordered]
    )
    tree.mapping[PdfName("Count")] = PdfNumber(len(order))
    for number in ordered:
        pdf._cos_doc.objects[number].mapping[PdfName("Parent")] = tree_ref
    pdf.pages = [pdf.pages[index] for index in order]
    pdf.page_contents = [pdf.page_contents[index] for index in order]
    pdf._content_obj_ids = [pdf._content_obj_ids[index] for index in order]
    pdf._page_obj_ids = ordered
    pdf._page_refs = list(ordered)
    pdf._page_cache_valid = True
    old_to_new = {old: new for new, old in enumerate(order)}
    pdf._page_image_map = {
        old_to_new[index]: names for index, names in pdf._page_image_map.items()
    }
    for name in ("_image_matrix_map", "_image_rect_map"):
        setattr(
            pdf,
            name,
            {
                (old_to_new[index], image): value
                for (index, image), value in getattr(pdf, name).items()
            },
        )
    if labels is not None:
        ranges = page_labels.ranges_from_values([labels[index] for index in order])
        pdf._load_budget.check(
            2 * len(ranges), "max_container_items", "interleaved page labels"
        )
        pdf.set_page_label_ranges(ranges)
    # Imported destinations name page objects, so only the numeric views need
    # refreshing. Links, widgets and structure /Pg references already follow.
    pdf._outlines_data = pdf.outline_items()
    stack = list(pdf._outlines_data)
    while stack:
        item = stack.pop()
        loaded = item.get("loaded_target")
        if loaded is not None:
            key, target = loaded
            if key == "A":
                typed = action_from_cos(
                    pdf._resolve(target), pdf._resolve, pdf._page_index_of
                )
            else:
                typed = destination_from_cos(target, pdf._resolve, pdf._page_index_of)
                if typed is _UNRESOLVED_DESTINATION:
                    typed = None
            item["target"] = typed
        stack.extend(item.get("children", ()))
    pdf._extracted_text = None
    pdf._page_text_cursor = 0
    pdf._isolated_page_contents.clear()
    pdf._authored_font_cache.clear()


def interleave(
    first: SimplePdf,
    second: SimplePdf,
    *,
    reverse_other: bool = False,
    strict: bool = True,
    first_outlines: list | None = None,
    second_outlines: list | None = None,
) -> SimplePdf:
    """Return independent alternating pages, keeping optional unmatched tails."""
    first._ensure_not_disposed()
    second._ensure_not_disposed()
    if not isinstance(reverse_other, bool) or not isinstance(strict, bool):
        raise PdfValidationException("reverse_other and strict must be booleans")
    fronts, backs = len(first.pages), len(second.pages)
    if strict and fronts != backs:
        raise PdfValidationException(
            f"Interleave requires equal page counts: {fronts} and {backs}; "
            "use strict=False to retain unmatched pages"
        )
    budget = _LoadBudget(first.load_limits)
    budget.check_pages(fronts + backs)
    budget.check(fronts + backs, "max_container_items", "interleaved page order")
    budget.check_objects(fronts + backs + 2)
    order = []
    for index in range(max(fronts, backs)):
        if index < fronts:
            order.append(index)
        if index < backs:
            order.append(fronts + (backs - index - 1 if reverse_other else index))
    result = SimplePdf()
    result._load_limits, result._load_budget = first.load_limits, budget
    result.metadata = dict(first.metadata)
    result.pdf_version = max(first.pdf_version, second.pdf_version)
    try:
        for source, outlines in ((first, first_outlines), (second, second_outlines)):
            _import_source(result, source, outlines)
        _order_pages(result, order)
        if result._outlines_data:
            result._inject_outlines_to_cos(result._outlines_data)
        result._sync_metadata_to_cos()
        result._sync_attachments_to_cos()
        _check_objects(result)
    except BaseException:
        result.dispose()
        raise
    return result
