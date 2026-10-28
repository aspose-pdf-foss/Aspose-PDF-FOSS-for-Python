"""Create independently editable, cropped pages from displayed PDF spreads."""

from __future__ import annotations

import math

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException
from aspose_pdf.load_limits import _LoadBudget

from . import page_labels
from .blank_pages import _number
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    format_pdf_number,
)
from .flatten import flatten
from .interleave import _import_source, _order_pages
from .simple_pdf import SimplePdf
from .stamps import visible_box
from .text_selection import page_indexes
from .text_split import _check_result


def _halves(pdf, index, layout, ratio, gutter):
    """Map visible left/right or top/bottom rectangles into PDF coordinates."""
    x0, y0, x1, y1 = visible_box(pdf, index)
    width, height = x1 - x0, y1 - y0
    if (
        not all(math.isfinite(value) for value in (x0, y0, x1, y1, width, height))
        or min(width, height) <= 0
    ):
        raise PdfValidationException(
            "A spread must have finite positive visible dimensions"
        )
    turn = pdf.get_page_rotation(index) % 360
    if turn not in (0, 90, 180, 270):
        raise PdfValidationException("Spread rotation must be a multiple of 90 degrees")
    displayed_width, displayed_height = (
        (height, width) if turn in (90, 270) else (width, height)
    )
    extent = displayed_width if layout == "horizontal" else displayed_height
    cut = extent * ratio
    before, after = cut - gutter / 2, cut + gutter / 2
    if not 0 < before < after < extent and not (
        gutter == 0 and 0 < before == after < extent
    ):
        raise PdfValidationException(
            "split_ratio and gutter must leave two positive page regions"
        )
    if layout == "horizontal":
        regions = (
            (0, 0, before, displayed_height),
            (after, 0, displayed_width, displayed_height),
        )
    else:
        regions = (
            (0, displayed_height - before, displayed_width, displayed_height),
            (0, 0, displayed_width, displayed_height - after),
        )

    return _map_regions(x0, y0, width, height, turn, regions)


def _map_regions(x0, y0, width, height, turn, regions):
    """Map displayed rectangles to boxes at the writer's numeric precision."""

    def to_pdf(x, y):
        if turn == 90:
            return x0 + width - y, y0 + x
        if turn == 180:
            return x0 + width - x, y0 + height - y
        if turn == 270:
            return x0 + y, y0 + height - x
        return x0 + x, y0 + y

    boxes = []
    for a, b, c, d in regions:
        corners = [to_pdf(x, y) for x in (a, c) for y in (b, d)]
        xs, ys = zip(*corners, strict=True)
        box = tuple(
            float(format_pdf_number(value))
            for value in (min(xs), min(ys), max(xs), max(ys))
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise PdfValidationException(
                "Split coordinates collapse at the page's numeric precision"
            )
        boxes.append(box)
    return boxes


def _annotations(pdf, index, *, context="spread"):
    page = pdf._get_page_dict(index)
    value = pdf._resolve(page.mapping.get(PdfName("Annots")))
    if value is None or isinstance(value, PdfNull):
        return []
    if not isinstance(value, PdfArray):
        raise PdfValidationException(
            f"{context.capitalize()} page Annots must be an array"
        )
    pdf._load_budget.check(
        len(value.items), "max_container_items", f"{context} annotations"
    )
    entries = []
    seen = set()
    for reference in value.items:
        annotation = pdf._resolve(reference)
        if not isinstance(annotation, PdfDictionary) or isinstance(
            annotation, PdfStream
        ):
            raise PdfValidationException(
                f"{context.capitalize()} annotations must be dictionaries"
            )
        if id(annotation) in seen:
            raise PdfValidationException(
                f"{context.capitalize()} page repeats an annotation object"
            )
        subtype = pdf._resolve(annotation.mapping.get(PdfName("Subtype")))
        if not isinstance(subtype, PdfName) or not subtype.name.lstrip("/"):
            raise PdfValidationException(
                f"{context.capitalize()} annotation Subtype must be a PDF name"
            )
        rectangle = pdf._resolve(annotation.mapping.get(PdfName("Rect")))
        if not isinstance(rectangle, PdfArray) or len(rectangle.items) != 4:
            raise PdfValidationException(
                f"{context.capitalize()} annotation Rect must contain four finite numbers"
            )
        for coordinate in rectangle.items:
            coordinate = pdf._resolve(coordinate)
            if not isinstance(coordinate, PdfNumber):
                raise PdfValidationException(
                    f"{context.capitalize()} annotation Rect must contain four finite numbers"
                )
            _number(coordinate.value, "annotation rectangle coordinate")
        seen.add(id(annotation))
        entries.append(annotation)
    return entries


def _is_widget(pdf, annotation):
    return pdf._resolve(annotation.mapping.get(PdfName("Subtype"))) == PdfName("Widget")


def _copy_annotations(pdf, entries, target_index):
    """Clone annotation identities and reconnect the copied page's local threads."""
    if not entries:
        return
    page = pdf._get_page_dict(target_index)
    page_reference = pdf._cos_doc.reference(pdf._page_reference_numbers()[target_index])
    copies = {}
    for entry in entries:
        clone = PdfDictionary(dict(entry.mapping))
        clone.mapping.pop(PdfName("StructParent"), None)
        clone.mapping[PdfName("P")] = page_reference
        copies[id(entry)] = pdf._cos_doc.register_object(clone)
    for reference in copies.values():
        clone = pdf._resolve(reference)
        for key in ("IRT", "Popup", "Parent"):
            target = pdf._resolve(clone.mapping.get(PdfName(key)))
            if id(target) in copies:
                clone.mapping[PdfName(key)] = copies[id(target)]
    if copies:
        page.mapping[PdfName("Annots")] = PdfArray(list(copies.values()))


def _set_boxes(pdf, index, box):
    pdf.set_page_media_box(index, box)
    for name in ("CropBox", "BleedBox", "TrimBox", "ArtBox"):
        pdf.set_page_box(index, name, box)


def _check(pdf, *, context="spread"):
    """Bound copied content and newly generated unfiltered appearance streams."""
    _check_result(pdf, context=f"{context} split")
    total = 0
    for value in pdf._cos_doc.objects.values():
        if not isinstance(value, PdfStream):
            continue
        filters = pdf._resolve(value.mapping.get(PdfName("Filter")))
        if (
            filters is None
            or isinstance(filters, PdfNull)
            or (isinstance(filters, PdfArray) and not filters.items)
        ):
            size = len(value.content)
            pdf._load_budget.check(
                size, "max_decoded_stream_bytes", f"{context} unfiltered stream bytes"
            )
            total += size
    pdf._load_budget.check(
        total, "max_total_decoded_bytes", f"{context} retained unfiltered streams"
    )
    return total


def split_spreads(
    document, *, pages, layout, reverse, split_ratio, gutter, flatten_fields
):
    """Preflight all geometry, import once, then append halves and reorder once."""
    if not isinstance(layout, str) or layout not in ("horizontal", "vertical"):
        raise PdfValidationException("layout must be 'horizontal' or 'vertical'")
    if not isinstance(reverse, bool) or not isinstance(flatten_fields, bool):
        raise PdfValidationException("reverse and flatten_fields must be booleans")
    split_ratio = _number(split_ratio, "split_ratio")
    gutter = _number(gutter, "gutter")
    if not 0 < split_ratio < 1:
        raise PdfValidationException("split_ratio must be strictly between 0 and 1")
    if gutter < 0:
        raise PdfValidationException("gutter must be non-negative")
    source = document._engine_pdf
    budget = _LoadBudget(document.load_limits)
    count = document.page_count
    selection = page_indexes(pages, count, limits=budget.limits)
    selected = set(range(count) if selection is None else selection)
    total = count + len(selected)
    budget.check_pages(total)
    budget.check(total, "max_container_items", "spread output pages")
    budget.check_objects(2 + total * 2)
    plan = {}
    for index in sorted(selected):
        boxes = _halves(source, index, layout, split_ratio, gutter)
        plan[index] = list(reversed(boxes)) if reverse else boxes
    return _split_pages(document, plan, budget, flatten_fields=flatten_fields)


def _split_pages(document, plan, budget, *, context="spread", flatten_fields=True):
    """Import once, append independent cropped copies, then reorder once."""
    source = document._engine_pdf
    count = document.page_count
    result = SimplePdf()
    result._load_limits, result._load_budget = document.load_limits, budget
    result.metadata = dict(source.metadata)
    result.pdf_version = source.pdf_version
    try:
        _import_source(
            result,
            source,
            None if document._outlines is None else document._outlines._items,
        )
        labels = result._page_label_values()
        with_widgets = [
            index
            for index in plan
            if any(
                _is_widget(result, entry)
                for entry in _annotations(result, index, context=context)
            )
        ]
        if with_widgets:
            if not flatten_fields:
                raise UnsupportedFeatureException(
                    f"{context.capitalize()} pages with form widgets require flatten_fields=True"
                )
            flatten(result, fields=True, annotations=False, pages=with_widgets)
        retained = _check(result, context=context)
        extra = {}
        for index, boxes in plan.items():
            entries = _annotations(result, index, context=context)
            if any(_is_widget(result, entry) for entry in entries):
                raise UnsupportedFeatureException(
                    f"A {context} widget could not be flattened; prepare its visible appearance before splitting"
                )
            original = result._get_page_dict(index)
            content = result.get_page_content(index)
            targets = []
            for box in boxes[1:]:
                budget.check_objects(len(result._cos_doc.objects) + 2 + len(entries))
                budget.check_object_id(
                    max(result._cos_doc.objects, default=0) + 2 + len(entries)
                )
                target = len(result.pages)
                retained += len(content)
                budget.check(
                    retained,
                    "max_total_decoded_bytes",
                    f"{context} retained unfiltered streams",
                )
                result.insert(
                    target,
                    (result.pages[index], content),
                    _source=original,
                )
                result._get_page_dict(target).mapping.pop(
                    PdfName("StructParents"), None
                )
                _copy_annotations(result, entries, target)
                _set_boxes(result, target, box)
                if index in result._page_image_map:
                    result._page_image_map[target] = list(result._page_image_map[index])
                for name in ("_image_matrix_map", "_image_rect_map"):
                    mapping = getattr(result, name)
                    mapping.update(
                        {
                            (target, image): value
                            for (page, image), value in list(mapping.items())
                            if page == index
                        }
                    )
                targets.append(target)
            _set_boxes(result, index, boxes[0])
            extra[index] = targets
        if labels is not None:
            values = [
                *labels,
                *(labels[index] for index, targets in extra.items() for _ in targets),
            ]
            ranges = page_labels.ranges_from_values(values)
            budget.check(
                2 * len(ranges), "max_container_items", f"{context} page labels"
            )
            result.set_page_label_ranges(ranges)
        order = [
            page for index in range(count) for page in [index, *extra.get(index, ())]
        ]
        if order:
            _order_pages(result, order)
        if result._outlines_data:
            result._inject_outlines_to_cos(result._outlines_data)
        result._sync_metadata_to_cos()
        result._sync_attachments_to_cos()
        _check(result, context=context)
    except BaseException:
        result.dispose()
        raise
    return result
