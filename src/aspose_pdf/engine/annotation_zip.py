"""Bounded, deterministic CSV archives of annotation metadata per page."""

from __future__ import annotations

import json
import zipfile

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .annotation_csv import export as _export_annotations
from .attachment_zip import _BoundedBuffer, _write_entry
from .csv_export import CsvWriter
from .text_selection import page_indexes


def _manifest(records, delimiter, encoding, budget, total):
    """Keep source provenance separate from the unchanged CSV cell strings."""
    value = dict(
        schema_version=2, delimiter=delimiter, encoding=encoding, pages=records
    )
    with _BoundedBuffer(
        budget, "max_object_bytes", "annotation ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(value):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages=None,
    subtypes=None,
    delimiter=",",
    encoding="utf-8",
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate options and pages, then extract and package one page at a time."""
    if not isinstance(subtypes, (type(None), str, list, tuple)):
        raise PdfValidationException("subtypes must be a subtype string, AnnotationType, or iterable of those")
    if not isinstance(delimiter, str) or not delimiter:
        raise PdfValidationException("delimiter must be a non-empty string")
    if not isinstance(encoding, str) or not encoding.strip():
        raise PdfValidationException("encoding must be a non-empty string")
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
    if not isinstance(include_manifest, bool):
        raise PdfValidationException("include_manifest must be a boolean")

    method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    
    # Validate options
    CsvWriter(delimiter, encoding, budget, context="CSV annotations", limit_object_bytes=True)
    try:
        delimiter.encode(encoding)
    except UnicodeError as exc:
        raise PdfValidationException(
            "CSV delimiter cannot be encoded using the requested encoding"
        ) from exc
    
    # Get page selection
    count = document.page_count
    if pages is None:
        budget.check(count, "max_container_items", "annotation export page selection")
        selection = list(range(count))
    else:
        # The @page_api decorator has already converted one-based indices to zero-based.
        # page_indexes handles both raw iterables and _EngineSelection (which is iterable).
        index_list = page_indexes(pages, count, limits=budget.limits)
        selection = list(index_list) if index_list is not None else []
    
    budget.check_pages(len(selection))
    budget.check(int(include_manifest), "max_container_items", "annotation ZIP entries")
    
    records = []
    number = total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "annotation ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=method, compresslevel=compresslevel
        ) as archive:
            for index in selection:
                # Export annotations for this page
                csv_data = _export_annotations(
                    document,
                    pages=[index],
                    subtypes=subtypes,
                    delimiter=delimiter,
                    encoding=encoding,
                )
                
                number += 1
                total += len(csv_data)
                
                budget.check(
                    number + int(include_manifest),
                    "max_container_items",
                    "annotation ZIP entries",
                )
                budget.check(
                    total, "max_total_decoded_bytes", "annotation ZIP selected files"
                )
                
                name = f"annotations-page-{number:04d}.csv"
                _write_entry(archive, name, csv_data, method, compresslevel)
                
                if include_manifest:
                    # Count annotations for this page
                    from .annotation_json import iter_records
                    annotations = list(iter_records(
                        document._engine_pdf,
                        [index],
                        subtypes,
                        property_names=("Subj", "CreationDate", "M"),
                    ))
                    # index is zero-based, so page_index is index + 1
                    records.append(
                        dict(
                            archive_name=name,
                            page_index=index + 1,
                            annotation_count=len(annotations),
                            size=len(csv_data),
                        )
                    )
                
                del csv_data
            
            if include_manifest:
                data = _manifest(records, delimiter, encoding, budget, total)
                _write_entry(archive, "manifest.json", data, method, compresslevel)
        
        return output.getvalue()
