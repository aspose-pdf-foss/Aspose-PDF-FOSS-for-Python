"""Embed annotation-owned files and read them without scanning page content."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from aspose_pdf.attachments import AF_RELATIONSHIPS, FileSpecification
from aspose_pdf.exceptions import PdfValidationException

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)


def _text(pdf: Any, value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    size = 2
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise PdfValidationException(f"{label} contains invalid Unicode")
        size += 4 if code > 0xFFFF else 2
        pdf._load_budget.check(size, "max_object_bytes", f"attachment {label}")
    pdf._load_budget.check(size, "max_object_bytes", f"attachment {label}")
    return value


def add_file_attachment(
    pdf: Any,
    page_index: int,
    rect: Any,
    file: FileSpecification,
    *,
    contents: str | None,
    title: str | None,
    icon: str,
    compress: bool,
) -> int:
    """Validate all input before adding an annotation and its private Filespec."""
    from .simple_pdf import _format_pdf_date

    pdf._ensure_not_disposed()
    if not isinstance(file, FileSpecification):
        raise TypeError("file must be a FileSpecification")
    if not isinstance(compress, bool):
        raise TypeError("compress must be a boolean")
    if (
        isinstance(rect, (str, bytes, bytearray))
        or not isinstance(rect, Sequence)
        or len(rect) != 4
    ):
        raise PdfValidationException("rect must contain four finite numbers")
    try:
        if any(isinstance(value, bool) for value in rect):
            raise ValueError
        bounds = tuple(float(value) for value in rect)
    except (TypeError, ValueError, OverflowError):
        raise PdfValidationException("rect must contain four finite numbers") from None
    if (
        not all(math.isfinite(value) for value in bounds)
        or bounds[2] <= bounds[0]
        or bounds[3] <= bounds[1]
    ):
        raise PdfValidationException(
            "rect must have finite coordinates and positive width and height"
        )
    name = _text(pdf, file.name, "filename")
    if not name:
        raise PdfValidationException("An attachment filename must not be empty")
    icon = _text(pdf, icon, "icon").lstrip("/")
    if not icon:
        raise PdfValidationException("An attachment icon name must not be empty")
    note = _text(
        pdf, (file.description or name) if contents is None else contents, "contents"
    )
    if title is not None:
        _text(pdf, title, "title")
    meta: dict[str, Any] = {"compress": compress}
    for key, value in (
        ("mime", file.mime_type),
        ("description", file.description),
        ("relationship", file.relationship),
    ):
        if value is not None:
            meta[key] = _text(pdf, value, key)
    if file.mime_type is not None and not file.mime_type.isascii():
        raise PdfValidationException("Attachment MIME type must be ASCII")
    if file.relationship is not None and file.relationship not in AF_RELATIONSHIPS:
        raise PdfValidationException("Unsupported attachment relationship")
    for key, value in (
        ("creation_date", file.creation_date),
        ("mod_date", file.mod_date),
    ):
        if value is not None:
            if not isinstance(value, datetime):
                raise TypeError(f"{key} must be a datetime or None")
            meta[key] = _text(pdf, _format_pdf_date(value), key)
    budget = pdf._load_budget
    size = len(file.contents)
    budget.check_input(size)
    budget.check(size, "max_decoded_stream_bytes", "attachment payload bytes")
    budget.check(size * 2 + 1024, "max_codec_work_bytes", "attachment working buffers")
    page = pdf._get_page_dict(page_index)
    if not isinstance(page, PdfDictionary):
        raise PdfValidationException("Page index out of range")
    raw_annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
    if raw_annots is not None and not isinstance(raw_annots, (PdfArray, PdfNull)):
        raise PdfValidationException("Page annotations must be an array")
    annots = raw_annots if isinstance(raw_annots, PdfArray) else PdfArray([])
    budget.check(
        len(annots.items) + 1, "max_container_items", "page annotation entries"
    )
    budget.check(
        7 if title is None else 8, "max_container_items", "file attachment dictionary"
    )
    budget.check(
        8 if file.description else 7,
        "max_container_items",
        "annotation file properties",
    )
    budget.check(2, "max_nesting_depth", "annotation file properties")
    store = pdf._cos_doc
    budget.check_objects(len(store.objects) + 3)
    maximum = max(max(store.objects, default=0), max(store.generations, default=0))
    budget.check_object_id(maximum + 3)
    budget.reserve_decoded(size, "authored attachment bytes")
    fs_ref = pdf._embed_attachment(name, file.contents, meta, budget=budget)
    mapping = {
        PdfName("Type"): PdfName("Annot"),
        PdfName("Subtype"): PdfName("FileAttachment"),
        PdfName("Rect"): PdfArray([PdfNumber(value) for value in bounds]),
        PdfName("Contents"): PdfString(note),
        PdfName("Name"): PdfName(icon),
        PdfName("FS"): fs_ref,
        PdfName("P"): pdf._make_page_ref(page_index),
    }
    if title is not None:
        mapping[PdfName("T")] = PdfString(title)
    reference = store.register_object(PdfDictionary(mapping))
    annots.items.append(reference)
    page.mapping[PdfName("Annots")] = annots
    return len(annots.items) - 1


def file_specification(
    pdf: Any, page_index: int, annotation_index: int
) -> FileSpecification | None:
    """Decode only this annotation's embedded stream; external targets return None."""
    from .simple_pdf import CosExtractor

    pdf._ensure_not_disposed()
    entries = pdf._annotation_refs(page_index)
    if not 0 <= annotation_index < len(entries):
        raise PdfValidationException("Annotation index out of range")
    annotation = pdf._resolve(entries[annotation_index])
    if not isinstance(annotation, PdfDictionary) or pdf._resolve(
        annotation.mapping.get(PdfName("Subtype"))
    ) != PdfName("FileAttachment"):
        raise PdfValidationException("This is not a file attachment annotation")
    spec = pdf._resolve(annotation.mapping.get(PdfName("FS")))
    if not isinstance(spec, PdfDictionary):
        return None
    embedded = pdf._resolve(spec.mapping.get(PdfName("EF")))
    if embedded is None or isinstance(embedded, PdfNull):
        return None
    if not isinstance(embedded, PdfDictionary):
        raise PdfValidationException("Attachment /EF must be a dictionary")
    name = None
    for key in ("UF", "F", "Unix", "Mac", "DOS"):
        value = pdf._resolve(spec.mapping.get(PdfName(key)))
        if isinstance(value, PdfString):
            name = decode_pdf_text_string(value)
            break
    if not name:
        raise PdfValidationException("Embedded annotation file has no filename")
    for key in ("UF", "F", "Unix", "Mac", "DOS"):
        raw = embedded.mapping.get(PdfName(key))
        stream = pdf._resolve(raw)
        if isinstance(stream, PdfStream):
            data = pdf._decode_cos_stream(
                stream, raw if isinstance(raw, PdfIndirectReference) else None
            )
            reader = CosExtractor(
                pdf._cos_doc, b"", limits=pdf._load_limits, budget=pdf._load_budget
            )
            meta = reader._read_filespec_meta(spec, stream)
            return FileSpecification(
                name,
                data,
                mime_type=meta.get("mime"),
                description=meta.get("description"),
                creation_date=meta.get("creation_date"),
                mod_date=meta.get("mod_date"),
                relationship=meta.get("relationship"),
            )
    raise PdfValidationException("Attachment /EF has no embedded file stream")
