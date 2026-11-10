"""Package individual PDF pages as bounded, deterministic ZIP archives."""

from __future__ import annotations

import json
import zipfile

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _write_entry
from .text_selection import page_indexes


def _create_part_from_page(document, page_index):
    """Create a single-page document from a page reference."""
    # Use extract_pages to get a proper copy of the page with all its resources
    result = document._engine_pdf.extract_pages([page_index])
    from aspose_pdf import Document

    part = Document(limits=document.load_limits)
    part._engine_pdf = result
    return part


def _manifest(records, budget, total):
    """Serialize small provenance records incrementally under the byte policy."""
    manifest = dict(schema_version=1, pages=records)
    with _BoundedBuffer(
        budget, "max_object_bytes", "page PDF ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(manifest):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages=None,
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate the whole selection, then serialize one page at a time."""
    if not isinstance(compression, str) or compression.strip().lower() not in {
        "deflate",
        "stored",
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
    zip_method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(
        len(selection) + int(include_manifest),
        "max_container_items",
        "page PDF ZIP entries",
    )
    plan = list(selection)
    records = []
    total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "page PDF ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=zip_method, compresslevel=compresslevel
        ) as archive:
            for number, index in enumerate(plan, 1):
                part = _create_part_from_page(document, index)
                try:
                    with _BoundedBuffer(
                        budget,
                        "max_object_bytes",
                        "page PDF ZIP PDF bytes",
                        decoded_prefix=total,
                    ) as payload:
                        part.save(payload)
                        data = payload.getvalue()
                    budget.check(len(data), "max_object_bytes", "page PDF ZIP PDF bytes")
                    total += len(data)
                    budget.check(
                        total, "max_total_decoded_bytes", "page PDF ZIP selected files"
                    )
                    name = f"page-{number:04d}.pdf"
                    _write_entry(archive, name, data, zip_method, compresslevel)
                    if include_manifest:
                        records.append(
                            dict(
                                archive_name=name,
                                page_index=index + 1,
                                size=len(data),
                            )
                        )
                finally:
                    part.dispose()
                del data
            if include_manifest:
                data = _manifest(records, budget, total)
                _write_entry(archive, "manifest.json", data, zip_method, compresslevel)
        return output.getvalue()
