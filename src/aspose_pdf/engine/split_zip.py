"""Package consecutive PDF batches while retaining only one imported part."""

from __future__ import annotations

import json
import zipfile
from contextlib import closing

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _write_entry
from .page_count_split import validate_pages_per_part


def _manifest(records, pages_per_part, budget, total):
    """Record one-based, inclusive source ranges without duplicating page lists."""
    value = dict(schema_version=1, pages_per_part=pages_per_part, parts=records)
    with _BoundedBuffer(
        budget, "max_object_bytes", "split ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(value):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages_per_part=1,
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate archive options, then serialize and dispose each batch in turn."""
    validate_pages_per_part(pages_per_part)
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
    method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    count = document.page_count
    budget.check_pages(count)
    budget.check(
        (count + pages_per_part - 1) // pages_per_part + int(include_manifest),
        "max_container_items",
        "split ZIP entries",
    )
    records = []
    total = start = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "split ZIP output bytes"
    ) as output:
        with (
            zipfile.ZipFile(
                output, "w", compression=method, compresslevel=compresslevel
            ) as archive,
            closing(document.iter_split_every(pages_per_part)) as parts,
        ):
            for number, part in enumerate(parts, 1):
                with (
                    part,
                    _BoundedBuffer(
                        budget,
                        "max_object_bytes",
                        "split ZIP PDF bytes",
                        decoded_prefix=total,
                    ) as payload,
                ):
                    part.save(payload)
                    data = payload.getvalue()
                    name = f"part-{number:04d}.pdf"
                    _write_entry(archive, name, data, method, compresslevel)
                    total += len(data)
                    stop = start + part.page_count
                    if include_manifest:
                        records.append(
                            dict(
                                archive_name=name,
                                start_page=start + 1,
                                end_page=stop,
                                page_count=part.page_count,
                                size=len(data),
                            )
                        )
                    start = stop
                    del data
                del part, payload
            if include_manifest:
                data = _manifest(records, pages_per_part, budget, total)
                _write_entry(archive, "manifest.json", data, method, compresslevel)
        return output.getvalue()
