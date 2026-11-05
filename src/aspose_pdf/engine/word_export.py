"""Encode positioned words as bounded JSON and CSV reports."""

from __future__ import annotations

import io
from collections.abc import Iterable

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.text import TextWord

from .csv_export import CsvWriter
from .link_export import _json_tokens


def to_json(words: Iterable[TextWord], budget: _LoadBudget) -> bytes:
    """Encode one engine record at a time with public one-based page numbers."""
    with io.BytesIO() as output:

        def write(data: bytes) -> None:
            size = output.tell() + len(data)
            for limit in (
                "max_codec_work_bytes",
                "max_decoded_stream_bytes",
                "max_total_decoded_bytes",
            ):
                budget.check(size, limit, "word JSON bytes")
            output.write(data)

        try:
            write(b'{"schema_version":1,"words":[')
            for index, word in enumerate(words):
                if index:
                    write(b",")
                record = dict(
                    page_index=word.page_index + 1,
                    text=word.text,
                    rect=word.rect,
                    quad=word.quad,
                )
                for token in _json_tokens(record):
                    write(token.encode("utf-8"))
            write(b"]}\n")
        except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
            raise PdfValidationException(
                "Words cannot be encoded as UTF-8 JSON"
            ) from exc
        return output.getvalue()


def to_csv(words: Iterable[TextWord], writer: CsvWriter) -> bytes:
    """Keep word order, one-based pages and full geometry in flat CSV rows."""
    writer.writerow(("page_number", "text", "x0", "y0", "x1", "y1", "quad"))
    for word in words:
        writer.writerow(
            [
                str(word.page_index + 1),
                word.text,
                *[str(value) for value in word.rect],
                "".join(_json_tokens(word.quad)),
            ]
        )
    return writer.finish()
