"""Bound and snapshot reusable records for independent AcroForm outputs."""

from __future__ import annotations

import io
from collections.abc import Mapping
from types import MappingProxyType

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import PdfArray, PdfName, PdfNull, PdfStream, encode_pdf_text_string


def validate_options(strict, flatten, password):
    if not isinstance(strict, bool) or not isinstance(flatten, bool):
        raise TypeError("strict and flatten must be booleans")
    if password is not None and not isinstance(password, str):
        raise TypeError("password must be a string or None")
    if password is not None:
        try:
            password.encode("utf-8")
        except UnicodeError as exc:
            raise PdfValidationException("password contains invalid Unicode") from exc


def snapshot_records(records, limits):
    """Freeze flat values and bound iteration before any template is loaded."""
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise TypeError("records must be an iterable of field mappings")
    try:
        iterator = iter(records)
    except TypeError:
        raise TypeError("records must be an iterable of field mappings") from None
    budget = _LoadBudget(limits)
    output = []
    items = retained = 0

    def text(value, label):
        nonlocal retained
        budget.check(len(value), "max_object_bytes", label)
        try:
            size = len(encode_pdf_text_string(value))
        except UnicodeError as exc:
            raise PdfValidationException(f"{label} contains invalid Unicode") from exc
        budget.check(size, "max_object_bytes", label)
        retained += size
        budget.check(retained, "max_total_decoded_bytes", "form batch record text")

    for index, record in enumerate(iterator):
        items += 1
        budget.check(items, "max_container_items", "form batch record items")
        if not isinstance(record, Mapping):
            raise TypeError(f"records[{index}] must be a field mapping")
        budget.check(len(record), "max_container_items", "form batch fields")
        copied = {}
        for name, value in record.items():
            items += 1
            budget.check(items, "max_container_items", "form batch record items")
            if not isinstance(name, str):
                raise TypeError("form batch field names must be strings")
            if name in copied:
                raise PdfValidationException(f"Duplicate form batch field: {name!r}")
            text(name, "form batch field name")
            if isinstance(value, (list, tuple)):
                copied_values = []
                for item in value:
                    items += 1
                    budget.check(items, "max_container_items", "form batch record items")
                    if not isinstance(item, str):
                        raise TypeError("form batch lists must contain only strings")
                    text(item, "form batch field value")
                    copied_values.append(item)
                value = tuple(copied_values)
            elif isinstance(value, str):
                text(value, "form batch field value")
            elif value is not None and not isinstance(value, bool):
                raise TypeError("form batch values must be strings, booleans, lists or None")
            copied[name] = value
        output.append(MappingProxyType(copied))
    return tuple(output), retained


class OutputBuffer(io.BytesIO):
    """Bound retained output bytes before allocating a second serialization buffer."""

    def __init__(self, budget, retained):
        super().__init__()
        self.budget, self.retained = budget, retained

    def write(self, data):
        with self.getbuffer() as existing:
            size = max(existing.nbytes, self.tell() + len(data))
        self.budget.check(size, "max_input_bytes", "form batch output PDF")
        self.budget.check(
            self.retained + size, "max_total_decoded_bytes", "form batch retained bytes"
        )
        return super().write(data)


def snapshot_streams(document):
    """Remember source stream buffers without decoding unchanged assets."""
    return {
        id(obj): obj.content
        for obj in document._engine_pdf._cos_doc.objects.values()
        if isinstance(obj, PdfStream)
    }


def check_document(document, original_streams):
    """Bound newly authored field appearances and output object allocation."""
    pdf = document._engine_pdf
    budget = pdf._load_budget
    cos = pdf._cos_doc
    budget.check_pages(document.page_count)
    budget.check_objects(len(cos.objects))
    budget.check_object_id(max(max(cos.objects, default=0), max(cos.generations, default=0)))
    for obj in cos.objects.values():
        if isinstance(obj, PdfStream):
            budget.check(len(obj.content), "max_object_bytes", "form batch stream bytes")
            if original_streams.get(id(obj)) is not obj.content:
                filters = obj.mapping.get(PdfName("Filter"))
                if filters is None or isinstance(filters, PdfNull) or (
                    isinstance(filters, PdfArray) and not filters.items
                ):
                    budget.check(
                        len(obj.content), "max_decoded_stream_bytes",
                        "form batch generated stream",
                    )
                else:
                    pdf._decode_cos_stream(obj)
    for content in pdf.page_contents:
        budget.check(len(content), "max_content_stream_bytes", "form batch page content")
        budget.check(len(content), "max_decoded_stream_bytes", "form batch page content")
