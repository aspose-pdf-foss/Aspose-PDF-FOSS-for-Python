"""Read and author AcroForm calculation sequences using existing field identities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfStream,
    encode_pdf_text_string,
)
from .form_validation import _field_index, _Reader

_CO = PdfName("CO")


class _OrderReader(_Reader):
    """Index field metadata without reading values, appearances or scripts."""

    def __init__(self, document):
        super().__init__(document._engine_pdf, attributes=("FT",))
        self.budget = _LoadBudget(document.load_limits)
        self.references = {}

    def resolve(self, value):
        seen = set()
        while isinstance(value, PdfIndirectReference):
            self.budget.check(
                len(seen) + 1, "max_nesting_depth", "calculation order references"
            )
            key = value.object_number, value.gen_number
            if key in seen:
                raise PdfParseException("Calculation order reference contains a cycle")
            seen.add(key)
            value = self.pdf._resolve(value)
            if value is None:
                raise PdfParseException("Calculation order reference was not found")
        if isinstance(value, PdfDictionary):
            self.budget.check(
                len(value.mapping),
                "max_container_items",
                "calculation order dictionary",
            )
        return value

    def array(self, value, context):
        value = self.resolve(value)
        if value is None or isinstance(value, PdfNull):
            return []
        if not isinstance(value, PdfArray):
            raise PdfParseException(f"{context} must be an array")
        self.budget.check(len(value.items), "max_container_items", context)
        return value.items

    def acroform(self):
        if self.pdf._cos_doc is None:
            return None
        root = self.resolve(self.pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary) or isinstance(root, PdfStream):
            raise PdfParseException("Calculation order catalog must be a dictionary")
        acro = self.resolve(root.mapping.get(PdfName("AcroForm")))
        if acro is None or isinstance(acro, PdfNull):
            return None
        if not isinstance(acro, PdfDictionary) or isinstance(acro, PdfStream):
            raise PdfParseException("Calculation order AcroForm must be a dictionary")
        return acro


def get_order(document) -> list[str]:
    """Return stored field names in order, including repeated entries."""
    reader = _OrderReader(document)
    acro = reader.acroform()
    if acro is None:
        return []
    references = reader.array(acro.mapping.get(_CO), "calculation order")
    if not references:
        return []
    fields = _field_index(reader, strict=True)
    names = {id(field): name for name, (field, _attributes, _kind) in fields.items()}
    result = []
    for reference in references:
        if not isinstance(reference, PdfIndirectReference):
            raise PdfParseException(
                "Calculation order entries must be indirect field references"
            )
        field = reader.resolve(reference)
        name = names.get(id(field))
        if name is None:
            raise PdfParseException(
                "Calculation order references a field outside the form"
            )
        result.append(name)
    return result


def set_order(document, values) -> None:
    """Validate the complete sequence before replacing the AcroForm /CO array."""
    reader = _OrderReader(document)
    if values is None:
        values = ()
    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(
        values, Iterable
    ):
        raise TypeError("calculation_order must be an iterable of field names or None")
    names = []
    size = 0
    for index, name in enumerate(values, 1):
        reader.budget.check(
            index, "max_container_items", "calculation order field names"
        )
        if not isinstance(name, str):
            raise TypeError("calculation_order field names must be strings")
        if not name:
            raise PdfValidationException(
                "Calculation order field names must not be empty"
            )
        reader.budget.check(
            len(name), "max_object_bytes", "calculation order field name"
        )
        try:
            encoded_size = len(encode_pdf_text_string(name))
        except UnicodeError as exc:
            raise PdfValidationException(
                "Calculation order field name contains invalid Unicode"
            ) from exc
        reader.budget.check(
            encoded_size, "max_object_bytes", "calculation order field name"
        )
        size += encoded_size
        reader.budget.check(
            size, "max_total_decoded_bytes", "calculation order field names"
        )
        names.append(name)
    acro = reader.acroform()
    if not names:
        if acro is not None:
            acro.mapping.pop(_CO, None)
        return
    fields = _field_index(reader, strict=True, references=reader.references)
    references = []
    for name in names:
        if name not in fields:
            raise KeyError(f"Field '{name}' not found")
        reference = reader.references.get(id(fields[name][0]))
        if reference is None:
            raise PdfValidationException(
                "Calculation order requires indirect field dictionaries"
            )
        references.append(reference)
    reader.budget.check(
        len(acro.mapping) + int(_CO not in acro.mapping),
        "max_container_items",
        "calculation order AcroForm entries",
    )
    acro.mapping[_CO] = PdfArray(references)
