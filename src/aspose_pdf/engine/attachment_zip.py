"""Export embedded-file payloads as a deterministic ZIP with portable flat names."""

from __future__ import annotations

import io
import json
import posixpath
import unicodedata
import zipfile
from collections.abc import Iterable
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

if TYPE_CHECKING:
    from aspose_pdf.document import Document

_NAME_BYTES = 240
_MANIFEST_NAME = "manifest.json"
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"} | {
    f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}


class _BoundedBuffer(io.BytesIO):
    """Check archive/manifest growth before BytesIO allocates its output buffer."""

    def __init__(
        self,
        budget: _LoadBudget,
        limit: str,
        context: str,
        *,
        decoded_prefix: int | None = None,
    ) -> None:
        super().__init__()
        self.budget, self.limit, self.context = budget, limit, context
        self.size = 0
        self.decoded_prefix = decoded_prefix

    def write(self, data: bytes | bytearray | memoryview) -> int:
        size = max(self.size, self.tell() + len(data))
        self.budget.check(size, self.limit, self.context)
        if self.decoded_prefix is not None:
            self.budget.check(size, "max_decoded_stream_bytes", self.context)
            self.budget.check(
                self.decoded_prefix + size,
                "max_total_decoded_bytes",
                "ZIP selected payload and manifest bytes",
            )
        written = super().write(data)
        self.size = size
        return written


def _text(value: str, budget: _LoadBudget, context: str) -> str:
    """Bound source text before normalizing it or producing JSON strings."""
    if not isinstance(value, str):
        raise PdfValidationException(f"{context} must be a string")
    budget.check(len(value), "max_object_bytes", context)
    size = 0
    for offset in range(0, len(value), 4096):
        size += len(value[offset : offset + 4096].encode("utf-8", "surrogatepass"))
        budget.check(size, "max_object_bytes", context)
    return value


def _truncate(value: str, size: int) -> str:
    """Shorten a filename by UTF-8 bytes without cutting a Unicode character."""
    return value.encode("utf-8")[:size].decode("utf-8", "ignore")


def _fit_name(name: str, tag: str = "") -> str:
    """Reserve bytes for a collision suffix while retaining a usable extension."""
    stem, extension = posixpath.splitext(name)
    if len((stem + tag + extension).encode("utf-8")) > _NAME_BYTES:
        extension = _truncate(extension, 80)
        stem = _truncate(stem, _NAME_BYTES - len((tag + extension).encode("utf-8")))
    return (stem + tag + extension).rstrip(" .") or "attachment"


def _safe_name(name: str) -> str:
    """Remove path components, controls, reserved characters and device names."""
    name = unicodedata.normalize("NFC", name.replace("\\", "/").rsplit("/", 1)[-1])
    name = (
        "".join(
            "_"
            if char in '<>:"/\\|?*'
            or unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            else char
            for char in name
        ).strip(" .")
        or "attachment"
    )
    if name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
        name = "_" + name
    return _fit_name(name)


def _selection(
    document: Document, names: str | Iterable[str] | None, budget: _LoadBudget
) -> list[str]:
    """Validate the complete name selection, preserving explicit order once per name."""
    attachments = document._engine_pdf.attachments
    if names is None:
        budget.check(
            len(attachments), "max_container_items", "ZIP attachment selection"
        )
        for name in attachments:
            _text(name, budget, "attachment name")
        return sorted(attachments)
    if isinstance(names, str):
        names = (names,)
    elif isinstance(names, (bytes, bytearray)):
        raise PdfValidationException("names must be a string or iterable of strings")
    try:
        iterator = iter(names)
    except TypeError:
        raise PdfValidationException(
            "names must be a string or iterable of strings"
        ) from None
    selected = {}
    for count, name in enumerate(iterator, 1):
        budget.check(count, "max_container_items", "ZIP attachment selection")
        _text(name, budget, "attachment name")
        if name not in attachments:
            raise KeyError(f"No attachment named {name!r}")
        selected[name] = None
    return list(selected)


def _json_value(output: _BoundedBuffer, value: str | int | None) -> None:
    """Escape long JSON strings in bounded pieces instead of one large allocation."""
    if isinstance(value, str):
        output.write(b'"')
        for offset in range(0, len(value), 4096):
            text = json.dumps(value[offset : offset + 4096], ensure_ascii=True)[1:-1]
            output.write(text.encode("ascii"))
        output.write(b'"')
    else:
        output.write(json.dumps(value).encode("ascii"))


def _manifest(
    document: Document,
    plan: list[tuple[str, str]],
    budget: _LoadBudget,
    total_bytes: int,
) -> bytes:
    """Build a bounded versioned mapping of archive names to PDF attachment metadata."""
    from aspose_pdf.document import _coerce_date

    pdf = document._engine_pdf
    with _BoundedBuffer(
        budget,
        "max_object_bytes",
        "attachment ZIP manifest bytes",
        decoded_prefix=total_bytes,
    ) as output:
        output.write(b'{"schema_version":1,"attachments":[')
        for position, (name, archive_name) in enumerate(plan):
            meta = {
                **(pdf.attachment_read_meta.get(name) or {}),
                **(pdf.attachment_meta.get(name) or {}),
            }
            record = {
                "name": name,
                "archive_name": archive_name,
                "size": len(pdf.attachments[name]),
            }
            for field, key in (
                ("mime_type", "mime"),
                ("description", "description"),
                ("relationship", "relationship"),
            ):
                value = meta.get(key)
                record[field] = (
                    _text(value, budget, f"attachment {field}")
                    if value is not None
                    else None
                )
            for key in ("creation_date", "mod_date"):
                raw = meta.get(key)
                if isinstance(raw, str):
                    _text(raw, budget, f"attachment {key}")
                date = _coerce_date(raw)
                record[key] = date.isoformat() if date is not None else None
            if position:
                output.write(b",")
            output.write(b"{")
            for field_index, (key, value) in enumerate(record.items()):
                if field_index:
                    output.write(b",")
                _json_value(output, key)
                output.write(b":")
                _json_value(output, value)
            output.write(b"}")
        output.write(b"]}")
        return output.getvalue()


def export_zip(
    document: Document,
    *,
    names: str | Iterable[str] | None = None,
    compression: str = "deflate",
    compresslevel: int = 6,
    include_manifest: bool = False,
) -> bytes:
    """Preflight selected payloads and names, then build a bounded ZIP in memory."""
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
    selected = _selection(document, names, budget)
    budget.check(
        len(selected) + int(include_manifest),
        "max_container_items",
        "attachment ZIP entries",
    )
    used = {_MANIFEST_NAME.casefold()} if include_manifest else set()
    counters = {}
    plan = []
    total_bytes = 0
    for name in selected:
        payload = document._engine_pdf.attachments[name]
        if not isinstance(payload, (bytes, bytearray)):
            raise PdfValidationException(
                "attachment contents must be bytes or bytearray"
            )
        budget.check(
            len(payload), "max_decoded_stream_bytes", "ZIP attachment payload bytes"
        )
        total_bytes += len(payload)
        budget.check(
            total_bytes, "max_total_decoded_bytes", "ZIP selected payload bytes"
        )
        base = _safe_name(name)
        archive_name = base
        counter = counters.get(base.casefold(), 1)
        while archive_name.casefold() in used:
            counter += 1
            archive_name = _fit_name(base, f"_{counter}")
        counters[base.casefold()] = counter
        used.add(archive_name.casefold())
        plan.append((name, archive_name))
    manifest = (
        _manifest(document, plan, budget, total_bytes) if include_manifest else None
    )
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "attachment ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=method, compresslevel=compresslevel
        ) as archive:
            for name, archive_name in plan:
                _write_entry(
                    archive,
                    archive_name,
                    document._engine_pdf.attachments[name],
                    method,
                    compresslevel,
                )
            if manifest is not None:
                _write_entry(archive, _MANIFEST_NAME, manifest, method, compresslevel)
        return output.getvalue()


def _write_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes | bytearray,
    method: int,
    level: int,
) -> None:
    """Use fixed metadata and regular, non-executable file permissions in every ZIP."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = method
    info.file_size = len(payload)
    if hasattr(info, "compress_level"):
        info.compress_level = level
    else:
        # Python 3.11/3.12 expose this setting only under its original name.
        info._compresslevel = level
    with archive.open(info, "w") as member:
        view = memoryview(payload)
        for offset in range(0, len(view), 64 * 1024):
            member.write(view[offset : offset + 64 * 1024])
