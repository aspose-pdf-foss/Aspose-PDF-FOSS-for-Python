"""Bounded, deterministic CSV archives of geometrically inferred text tables."""

from __future__ import annotations

import json
import zipfile

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _write_entry
from .csv_export import CsvWriter
from .text_selection import page_indexes


def _manifest(records, delimiter, encoding, budget, total):
    """Keep source provenance separate from the unchanged CSV cell strings."""
    value = dict(
        schema_version=1, delimiter=delimiter, encoding=encoding, tables=records
    )
    with _BoundedBuffer(
        budget, "max_object_bytes", "table ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(value):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages=None,
    clip_to_page=True,
    delimiter=",",
    encoding="utf-8",
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate options and pages, then extract and package one page at a time."""
    from aspose_pdf.table_extraction import _page_tables

    if not isinstance(clip_to_page, bool):
        raise PdfValidationException("clip_to_page must be a boolean")
    if not isinstance(include_manifest, bool):
        raise PdfValidationException("include_manifest must be a boolean")
    if not isinstance(compression, str) or compression.strip().lower() not in {
        "stored",
        "deflate",
    }:
        raise PdfValidationException("compression must be 'deflate' or 'stored'")
    if (
        isinstance(compresslevel, bool)
        or not isinstance(compresslevel, int)
        or not 0 <= compresslevel <= 9
    ):
        raise PdfValidationException("compresslevel must be an integer from 0 to 9")
    method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    # Validate even for empty selections, before asking the layout analyzer to read.
    CsvWriter(delimiter, encoding, budget, context="CSV table", limit_object_bytes=True)
    try:
        delimiter.encode(encoding)
    except UnicodeError as exc:
        raise PdfValidationException(
            "CSV delimiter cannot be encoded using the requested encoding"
        ) from exc
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(int(include_manifest), "max_container_items", "table ZIP entries")
    records = []
    number = total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "table ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=method, compresslevel=compresslevel
        ) as archive:
            for index in selection:
                for table_index, table in enumerate(
                    _page_tables(document._engine_pdf, index, clip_to_page)
                ):
                    number += 1
                    budget.check(
                        number + int(include_manifest),
                        "max_container_items",
                        "table ZIP entries",
                    )
                    budget.check(
                        table.row_count, "max_container_items", "CSV table rows"
                    )
                    budget.check(
                        table.row_count * table.column_count,
                        "max_container_items",
                        "CSV table cells",
                    )
                    writer = CsvWriter(
                        delimiter,
                        encoding,
                        budget,
                        context="CSV table",
                        limit_object_bytes=True,
                    )
                    for row in table.rows:
                        writer.writerow(row)
                    data = writer.finish()
                    total += len(data)
                    budget.check(
                        total, "max_total_decoded_bytes", "table ZIP selected files"
                    )
                    name = f"table-{number:04d}.csv"
                    _write_entry(archive, name, data, method, compresslevel)
                    if include_manifest:
                        records.append(
                            dict(
                                archive_name=name,
                                page_index=index,
                                table_index=table_index,
                                row_count=table.row_count,
                                column_count=table.column_count,
                                size=len(data),
                            )
                        )
                    del writer, data
            if include_manifest:
                data = _manifest(records, delimiter, encoding, budget, total)
                _write_entry(archive, "manifest.json", data, method, compresslevel)
        return output.getvalue()
