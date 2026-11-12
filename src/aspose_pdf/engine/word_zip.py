"""Bounded, deterministic CSV archives of positioned words per page."""

from __future__ import annotations

import json
import zipfile

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _write_entry
from .csv_export import CsvWriter
from .link_export import _json_tokens
from .text_selection import page_indexes
from .word_extraction import extract_words


def _manifest(records, delimiter, encoding, budget, total):
    """Keep source provenance separate from the unchanged CSV cell strings."""
    value = dict(
        schema_version=2, delimiter=delimiter, encoding=encoding, pages=records
    )
    with _BoundedBuffer(
        budget, "max_object_bytes", "word ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(value):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages=None,
    clip=None,
    skip_artifacts=False,
    delimiter=",",
    encoding="utf-8",
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate options and pages, then extract and package one page at a time."""
    if not isinstance(clip, (type(None), (list, tuple))):
        raise PdfValidationException("clip must be a sequence of four numbers")
    if not isinstance(skip_artifacts, bool):
        raise PdfValidationException("skip_artifacts must be a boolean")
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
    CsvWriter(delimiter, encoding, budget, context="CSV words", limit_object_bytes=True)
    try:
        delimiter.encode(encoding)
    except UnicodeError as exc:
        raise PdfValidationException(
            "CSV delimiter cannot be encoded using the requested encoding"
        ) from exc
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(int(include_manifest), "max_container_items", "word ZIP entries")
    
    records = []
    number = total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "word ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=method, compresslevel=compresslevel
        ) as archive:
            for index in selection:
                # Extract words for this page using the full document
                words = extract_words(
                    document,
                    pages=[index],
                    clip=clip,
                    skip_artifacts=skip_artifacts,
                )
                
                # Export to CSV
                writer = CsvWriter(
                    delimiter,
                    encoding,
                    budget,
                    context="CSV words",
                    limit_object_bytes=True,
                )
                # Write header row
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
                data = writer.finish()
                
                number += 1
                total += len(data)
                
                budget.check(
                    number + int(include_manifest),
                    "max_container_items",
                    "word ZIP entries",
                )
                budget.check(
                    total, "max_total_decoded_bytes", "word ZIP selected files"
                )
                
                name = f"words-page-{number:04d}.csv"
                _write_entry(archive, name, data, method, compresslevel)
                
                if include_manifest:
                    records.append(
                        dict(
                            archive_name=name,
                            page_index=index + 1,
                            word_count=len(words),
                            size=len(data),
                        )
                    )
                
                del writer, data
            
            if include_manifest:
                data = _manifest(records, delimiter, encoding, budget, total)
                _write_entry(archive, "manifest.json", data, method, compresslevel)
        
        return output.getvalue()
