"""Shared bounded CSV encoding for PDF metadata reports."""

from __future__ import annotations

import codecs
import csv

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import encode_pdf_text_string


def _text_size(value: str, budget: _LoadBudget, label: str) -> int:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    budget.check(len(value), "max_object_bytes", label)
    try:
        size = len(encode_pdf_text_string(value))
    except UnicodeError as exc:
        raise PdfValidationException(f"{label} contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", label)
    return size


class CsvWriter:
    """Prepare CSV bytes with one encoder state and bounded records/output."""

    def __init__(
        self, delimiter: str, encoding: str, budget: _LoadBudget, *, context: str
    ) -> None:
        if not isinstance(encoding, str):
            raise TypeError("encoding must be a string")
        try:
            "".encode(encoding)
            self.encoder = codecs.getincrementalencoder(encoding)(errors="strict")
        except (LookupError, UnicodeError, ValueError, TypeError) as exc:
            raise PdfValidationException(
                "encoding must name a supported text encoding"
            ) from exc
        if not isinstance(delimiter, str):
            raise TypeError("delimiter must be a string")
        if len(delimiter) != 1 or delimiter in '\r\n"\x00':
            raise PdfValidationException(
                "delimiter must be one character other than a quote, newline or NUL"
            )
        _text_size(delimiter, budget, "CSV delimiter")
        self.encoding = encoding
        self.delimiter = delimiter
        self.budget = budget
        self.context = context
        self.data = bytearray()
        self.text_bytes = 0

    def _append(self, text: str, *, final: bool = False) -> None:
        data = self.encoder.encode(text, final=final)
        size = len(self.data) + len(data)
        self.budget.check(
            size, "max_decoded_stream_bytes", f"{self.context} output bytes"
        )
        self.budget.check(
            size, "max_total_decoded_bytes", f"{self.context} output bytes"
        )
        self.data.extend(data)

    def write(self, text: str) -> int:
        for offset in range(0, len(text), 65536):
            self._append(text[offset : offset + 65536])
        return len(text)

    def writerow(self, record: list[str] | tuple[str, ...]) -> None:
        self.budget.check(len(record), "max_container_items", f"{self.context} columns")
        characters = len(record) - 1 + 2
        for value in record:
            self.text_bytes += _text_size(value, self.budget, "CSV cell")
            self.budget.check(
                self.text_bytes, "max_total_decoded_bytes", f"{self.context} cell text"
            )
            quoted = any(char in value for char in (self.delimiter, '"', "\r", "\n"))
            quoted = quoted or (len(record) == 1 and not value)
            characters += len(value) + value.count('"') + 2 * quoted
            self.budget.check(
                characters,
                "max_decoded_stream_bytes",
                f"{self.context} record characters",
            )
        try:
            csv.writer(self, delimiter=self.delimiter, lineterminator="\r\n").writerow(
                record
            )
        except (UnicodeError, LookupError, ValueError, TypeError, csv.Error) as exc:
            raise PdfValidationException(
                f"{self.context} cannot be encoded as CSV using {self.encoding!r}"
            ) from exc

    def finish(self) -> bytes:
        try:
            self._append("", final=True)
        except (UnicodeError, LookupError, ValueError, TypeError) as exc:
            raise PdfValidationException(
                f"{self.context} cannot be encoded as CSV using {self.encoding!r}"
            ) from exc
        return bytes(self.data)
