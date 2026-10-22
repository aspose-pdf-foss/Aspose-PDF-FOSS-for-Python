"""Inspect image paints without decoding pixels or hydrating image caches."""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Iterable, Iterator
from decimal import Decimal, DecimalException, localcontext
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.image_resolution import ImageResolutionIssue
from aspose_pdf.load_limits import _LoadBudget

from .auto_tag import _tokens
from .blank_pages import _number
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    decode_pdf_name,
)
from .inline_image import _scan
from .pdf_matrix import (
    affine_decimal_to_float,
    identity_affine_decimal,
    image_placement_bbox,
    multiply_pdf_affine,
)
from .simple_pdf import CosExtractor
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document

_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")


def _compose(left, right):
    """Compose transforms and reject values outside the public numeric range."""
    try:
        matrix = multiply_pdf_affine(left, right)
    except DecimalException as exc:
        raise PdfValidationException(
            "Image resolution transform is out of range"
        ) from exc
    if not all(math.isfinite(value) for value in affine_decimal_to_float(matrix)):
        raise PdfValidationException("Image resolution transform must be finite")
    return matrix


def _placements(content, initial, budget, work) -> Iterator[tuple]:
    """Yield named or inline paints, retaining each graphics-state transform."""
    matrix = initial
    saved = []
    containers = []
    operands = deque(maxlen=6)
    operand_count = 0
    skip_until = 0
    text = None
    for token, start, end in _tokens(content, budget=budget):
        work[0] += 1
        budget.check(work[0], "max_content_tokens", "image resolution total tokens")
        if start < skip_until:
            continue
        if token in ("[", "<<", "{"):
            containers.append(token)
            continue
        if token in ("]", ">>", "}"):
            expected = {"]": "[", ">>": "<<", "}": "{"}[token]
            if not containers or containers.pop() != expected:
                raise PdfParseException("Unbalanced image resolution content container")
            if not containers:
                operands.append(None)
                operand_count += 1
                budget.check(
                    operand_count, "max_container_items", "image resolution operands"
                )
            continue
        if containers:
            continue
        if token is None or token.startswith("/") or _NUMBER.fullmatch(token):
            operand_count += 1
            budget.check(
                operand_count, "max_container_items", "image resolution operands"
            )
            operands.append(token)
            continue
        if token == "q":
            budget.check(
                len(saved) + 1, "max_nesting_depth", "image resolution graphics state"
            )
            budget.check(
                len(saved) + 1, "max_container_items", "image resolution graphics state"
            )
            saved.append(matrix)
        elif token == "Q":
            if not saved:
                raise PdfParseException("Unbalanced image resolution graphics state")
            matrix = saved.pop()
        elif token == "cm":
            if operand_count != 6 or any(
                value is None or not _NUMBER.fullmatch(value) for value in operands
            ):
                raise PdfParseException(
                    "Image resolution cm requires six finite numbers"
                )
            values = tuple(Decimal(v) for v in operands)
            matrix = _compose(values, matrix)
        elif token == "Do":
            if (
                operand_count != 1
                or not isinstance(operands[-1], str)
                or not operands[-1].startswith("/")
            ):
                raise PdfParseException(
                    "Image resolution Do requires one resource name"
                )
            yield decode_pdf_name(operands[-1])[1:], None, matrix
        elif token == "BI":
            if text is None:
                text = content.decode("latin-1")
            try:
                entries, _data_start, _data_end, skip_until = _scan(
                    text, end, budget.limits
                )
            except (OverflowError, ValueError) as exc:
                raise PdfValidationException("Invalid inline image geometry") from exc
            if entries is None or text[skip_until - 2 : skip_until] != "EI":
                raise PdfParseException("Invalid inline image dictionary")
            yield None, entries, matrix
        operands.clear()
        operand_count = 0
    if containers or saved:
        raise PdfParseException("Unbalanced image resolution content state")


def _resources(pdf, page, budget):
    """Resolve inherited page resources without accepting malformed overrides."""
    seen = set()
    while isinstance(page, PdfDictionary):
        budget.check(
            len(seen) + 1, "max_nesting_depth", "image resolution resource inheritance"
        )
        budget.check(
            len(seen) + 1,
            "max_container_items",
            "image resolution resource inheritance",
        )
        if id(page) in seen:
            raise PdfParseException("Image resolution resource inheritance cycle")
        seen.add(id(page))
        value = pdf._resolve(page.mapping.get(PdfName("Resources")))
        if value is not None and not isinstance(value, PdfNull):
            if not isinstance(value, PdfDictionary):
                raise PdfParseException(
                    "Image resolution Resources must be a dictionary"
                )
            return value
        page = pdf._resolve(page.mapping.get(PdfName("Parent")))
    return PdfDictionary({})


def _scalar(pdf, value, name):
    """Validate a COS number while preserving exact integer values."""
    value = pdf._resolve(value)
    if not isinstance(value, PdfNumber):
        raise PdfValidationException(f"{name} must be a PDF number")
    _number(value.value, name)
    return value.value


def find_low_resolution_images(
    document: Document, min_dpi: float, pages: Iterable[int] | None
) -> list[ImageResolutionIssue]:
    """Report each placed image below a finite, positive threshold."""
    threshold = _number(min_dpi, "min_dpi")
    if threshold <= 0:
        raise PdfValidationException("min_dpi must be positive")
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(
        len(selection), "max_container_items", "image resolution page selection"
    )
    if not selection:
        return []
    pdf = document._engine_pdf
    pdf._ensure_cos()
    extractor = CosExtractor(
        pdf._cos_doc,
        pdf._raw_bytes or b"",
        stream_decrypt_key=pdf._cos_decrypt_key or pdf.encryption_key,
        stream_decrypt_algorithm=pdf._cos_decrypt_algorithm
        if pdf._cos_decrypt_key
        else pdf.encryption_algorithm,
        limits=budget.limits,
        budget=budget,
    )
    results = []
    work = [0]
    visits = 0
    path_items = 0
    for index in selection:
        page = pdf._get_page_dict(index)
        if not isinstance(page, PdfDictionary):
            raise PdfParseException("Image resolution source page must be a dictionary")
        unit_value = pdf._resolve(page.mapping.get(PdfName("UserUnit")))
        unit = (
            1
            if unit_value is None or isinstance(unit_value, PdfNull)
            else _scalar(pdf, unit_value, "UserUnit")
        )
        if unit <= 0:
            raise PdfValidationException("UserUnit must be positive")
        page_resources = _resources(pdf, page, budget)
        content = (
            pdf.page_contents[index]
            if index < len(pdf.page_contents)
            else extractor._read_content_stream(page.mapping.get(PdfName("Contents")))
        )
        stack = [
            (
                iter(_placements(content, identity_affine_decimal(), budget, work)),
                page_resources,
                (),
                None,
            )
        ]
        active = set()
        placement_index = 0
        while stack:
            paints, resources, path, marker = stack[-1]
            paint = next(paints, None)
            if paint is None:
                stack.pop()
                active.discard(marker)
                continue
            visits += 1
            budget.check(visits, "max_container_items", "image resolution paint visits")
            name, inline, matrix = paint
            if inline is None:
                xobjects = pdf._resolve(resources.mapping.get(PdfName("XObject")))
                if not isinstance(xobjects, PdfDictionary):
                    raise PdfParseException(
                        "Image resolution Do has no XObject dictionary"
                    )
                reference = xobjects.mapping.get(PdfName("/" + name))
                target = pdf._resolve(reference)
                if not isinstance(target, PdfStream):
                    raise PdfParseException(
                        f"Image resolution XObject {name!r} must be a stream"
                    )
                subtype = pdf._resolve(target.mapping.get(PdfName("Subtype")))
                if subtype == PdfName("Form"):
                    child_marker = id(target)
                    if child_marker in active:
                        raise PdfParseException(
                            "Image resolution Form invocation cycle"
                        )
                    budget.check(
                        len(stack) + 1,
                        "max_nesting_depth",
                        "image resolution Form nesting",
                    )
                    budget.check(
                        len(stack) + 1,
                        "max_container_items",
                        "image resolution Form stack",
                    )
                    child_resources = pdf._resolve(
                        target.mapping.get(PdfName("Resources"))
                    )
                    if child_resources is None or isinstance(child_resources, PdfNull):
                        child_resources = page_resources
                    if not isinstance(child_resources, PdfDictionary):
                        raise PdfParseException(
                            "Image resolution Form Resources must be a dictionary"
                        )
                    form_matrix = pdf._resolve(target.mapping.get(PdfName("Matrix")))
                    if form_matrix is not None and not isinstance(form_matrix, PdfNull):
                        if (
                            not isinstance(form_matrix, PdfArray)
                            or len(form_matrix.items) != 6
                        ):
                            raise PdfValidationException(
                                "Image resolution Form Matrix requires six numbers"
                            )
                        values = tuple(
                            Decimal(str(_scalar(pdf, v, "Form Matrix")))
                            for v in form_matrix.items
                        )
                        matrix = _compose(values, matrix)
                    content = extractor._decode_stream(
                        target,
                        reference,
                        max_output_bytes=budget.limits.max_content_stream_bytes,
                    )
                    active.add(child_marker)
                    stack.append(
                        (
                            iter(_placements(content, matrix, budget, work)),
                            child_resources,
                            (*path, name),
                            child_marker,
                        )
                    )
                    continue
                if not isinstance(subtype, PdfName):
                    raise PdfParseException(
                        "Image resolution XObject requires a Subtype name"
                    )
                if subtype != PdfName("Image"):
                    continue
                entries = target
                resource_path = (*path, name)
            else:
                entries = inline
                resource_path = path
            current_index = placement_index
            placement_index += 1
            dimensions = []
            for key in ("Width", "Height"):
                value = _scalar(pdf, entries.mapping.get(PdfName(key)), f"image {key}")
                if value <= 0 or int(value) != value:
                    raise PdfValidationException(
                        "Image resolution dimensions must be positive integers"
                    )
                dimensions.append(int(value))
            width, height = dimensions
            budget.check_image_pixels(width, height, "image resolution")
            with localcontext() as ctx:
                ctx.prec = 80
                determinant = matrix[0] * matrix[3] - matrix[1] * matrix[2]
                if determinant == 0:
                    continue
                physical_unit = Decimal(str(unit))
                axis_x = (matrix[0] ** 2 + matrix[1] ** 2).sqrt() * physical_unit
                axis_y = (matrix[2] ** 2 + matrix[3] ** 2).sqrt() * physical_unit
                resolution_x = Decimal(width) * 72 / axis_x
                resolution_y = Decimal(height) * 72 / axis_y
            dpi_x, dpi_y = float(resolution_x), float(resolution_y)
            if not all(math.isfinite(v) and v > 0 for v in (dpi_x, dpi_y)):
                raise PdfValidationException(
                    "Image resolution DPI is outside the supported numeric range"
                )
            if min(resolution_x, resolution_y) >= Decimal(str(threshold)):
                continue
            bbox = image_placement_bbox(matrix)
            if not all(math.isfinite(v) for v in bbox):
                raise PdfValidationException(
                    "Image resolution bounding box must be finite"
                )
            path_items += len(resource_path)
            budget.check(
                path_items, "max_container_items", "image resolution result paths"
            )
            results.append(
                ImageResolutionIssue(
                    index,
                    current_index,
                    resource_path,
                    width,
                    height,
                    dpi_x,
                    dpi_y,
                    bbox,
                    inline is not None,
                )
            )
    return results
