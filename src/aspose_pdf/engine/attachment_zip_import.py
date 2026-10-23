"""Bounded, transactional import of document-level attachments from ZIP files."""

from __future__ import annotations

import io
import json
import os
import posixpath
import stat
import struct
import zipfile
import zlib
from datetime import datetime
from typing import TYPE_CHECKING

from aspose_pdf.attachments import AF_RELATIONSHIPS
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _MANIFEST_NAME, _fit_name, _safe_name, _text
from .form_json import _check_limits

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import BinaryIO

    from aspose_pdf.document import Document


def _fail(message: str) -> None:
    raise PdfValidationException(f"Invalid attachment ZIP: {message}")


def _read(source, budget: _LoadBudget) -> bytes:
    """Buffer input from the current position, without closing caller streams."""
    enabled = [
        limit
        for limit in (budget.limits.max_input_bytes, budget.limits.max_codec_work_bytes)
        if limit is not None
    ]
    limit = min(enabled) if enabled else None

    def check(size):
        budget.check(size, "max_input_bytes", "attachment ZIP input bytes")
        budget.check(size, "max_codec_work_bytes", "attachment ZIP input bytes")

    def read(stream):
        output = bytearray()
        while True:
            amount = (
                64 * 1024 if limit is None else min(64 * 1024, limit + 1 - len(output))
            )
            chunk = stream.read(amount)
            if not isinstance(chunk, (bytes, bytearray)):
                raise TypeError("stream read() must return bytes")
            if not chunk:
                return bytes(output)
            check(len(output) + len(chunk))
            output.extend(chunk)

    if isinstance(source, (bytes, bytearray)):
        check(len(source))
        return bytes(source)
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return read(stream)
    if hasattr(source, "read"):
        return read(source)
    raise TypeError("source must be a path, bytes or a readable binary stream")


def _directory(data: bytes, budget: _LoadBudget) -> None:
    """Count actual central records before ZipFile allocates one object per entry."""
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        _fail("missing end-of-central-directory record")
    disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4H2LH", data, end + 4
    )
    if end + 22 + comment != len(data):
        _fail("truncated comment or trailing archive data")
    if disk or start_disk or disk_count != count:
        _fail("multi-disk archives are not supported")
    if count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        _fail("ZIP64 central directories are not supported")
    budget.check(count, "max_container_items", "attachment ZIP entries")
    cursor = end - size
    if cursor < 0 or offset > cursor:
        _fail("invalid central directory bounds")
    actual = 0
    while cursor < end:
        if cursor + 46 > end or data[cursor : cursor + 4] != b"PK\x01\x02":
            _fail("invalid central directory entry")
        name, extra, note = struct.unpack_from("<3H", data, cursor + 28)
        actual += 1
        budget.check(actual, "max_container_items", "attachment ZIP entries")
        budget.check(
            name + extra + note, "max_object_bytes", "ZIP entry metadata bytes"
        )
        if struct.unpack_from("<H", data, cursor + 34)[0]:
            _fail("multi-disk entry is not supported")
        cursor += 46 + name + extra + note
    if cursor != end or actual != count:
        _fail("central directory length/count mismatch")


def _entries(
    archive: zipfile.ZipFile, data: bytes, budget: _LoadBudget
) -> dict[str, zipfile.ZipInfo]:
    """Reject ambiguous names and special files; retain ordinary files in ZIP order."""
    seen, entries = set(), {}
    for info in archive.infolist():
        name = _text(info.orig_filename, budget, "ZIP member name")
        if "\x00" in name or not name or name in seen:
            _fail("empty, NUL-containing or duplicate member name")
        seen.add(name)
        if info.flag_bits & (1 | 0x40 | 0x2000):
            _fail("encrypted ZIP entries are not supported")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _fail("only stored and deflated ZIP entries are supported")
        mode = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            _fail("symlinks and other special ZIP entries are not supported")
        # Validate structure even for directories and unselected attachments.
        # Opening without reading checks names, offsets and overlapping records.
        with archive.open(info):
            pass
        flags, method = struct.unpack_from("<2H", data, info.header_offset + 6)
        if flags != info.flag_bits or method != info.compress_type:
            _fail("local and central entry headers disagree")
        name_size, extra_size = struct.unpack_from("<2H", data, info.header_offset + 26)
        budget.check(
            name_size + extra_size, "max_object_bytes", "ZIP local metadata bytes"
        )
        if info.is_dir() or name.endswith("\\") or mode == stat.S_IFDIR:
            if info.file_size:
                _fail("directory entry contains file data")
            continue
        entries[name] = info
    return entries


def _selection(names, entries: dict, budget: _LoadBudget) -> list[str]:
    if names is None:
        return list(entries)
    if isinstance(names, str):
        names = (names,)
    elif isinstance(names, (bytes, bytearray)):
        _fail("names must be a string or iterable of strings")
    try:
        iterator = iter(names)
    except TypeError:
        _fail("names must be a string or iterable of strings")
    selected = {}
    for count, name in enumerate(iterator, 1):
        budget.check(count, "max_container_items", "ZIP import selection")
        _text(name, budget, "selected ZIP member name")
        if name not in entries:
            raise KeyError(f"No importable ZIP member named {name!r}")
        selected[name] = None
    return list(selected)


class _Decoder:
    """Account for compressed input and each decoded byte before staging attachments."""

    def __init__(self, data: bytes, archive: zipfile.ZipFile, budget: _LoadBudget):
        self.data, self.archive, self.budget = data, archive, budget
        self.total = 0

    def read(self, info: zipfile.ZipInfo, *, manifest: bool = False) -> bytes:
        budget = self.budget
        budget.check(info.file_size, "max_decoded_stream_bytes", "ZIP member bytes")
        budget.check(
            self.total + info.file_size, "max_total_decoded_bytes", "ZIP decoded bytes"
        )
        budget.check(
            len(self.data) + self.total + info.file_size,
            "max_codec_work_bytes",
            "ZIP import working bytes",
        )
        if manifest:
            budget.check(info.file_size, "max_object_bytes", "ZIP manifest bytes")
        ratio = budget.limits.max_compression_ratio
        if ratio is not None and info.file_size > max(1, info.compress_size) * ratio:
            budget._raise(
                "ZIP compression ratio",
                (info.file_size + max(1, info.compress_size) - 1)
                // max(1, info.compress_size),
                "max_compression_ratio",
                ratio,
            )
        header = info.header_offset
        flags, method = struct.unpack_from("<2H", self.data, header + 6)
        if flags != info.flag_bits or method != info.compress_type:
            _fail("local and central entry headers disagree")
        name_size, extra_size = struct.unpack_from("<2H", self.data, header + 26)
        start = header + 30 + name_size + extra_size
        end = start + info.compress_size
        if end > len(self.data):
            _fail("truncated compressed member")
        decoded = bytearray()
        decoder = zlib.decompressobj(-15) if method == zipfile.ZIP_DEFLATED else None
        for offset in range(start, end, 64 * 1024):
            pending = self.data[offset : min(offset + 64 * 1024, end)]
            while True:
                chunk = decoder.decompress(pending, 64 * 1024) if decoder else pending
                size = len(decoded) + len(chunk)
                if size > info.file_size:
                    _fail("decoded member exceeds its declared size")
                decoded.extend(chunk)
                if decoder is None:
                    break
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    _fail("trailing data in compressed member")
                if not pending and len(chunk) < 64 * 1024:
                    break
        if decoder is not None and not decoder.eof:
            _fail("truncated deflate stream")
        if len(decoded) != info.file_size or zlib.crc32(decoded) != info.CRC:
            _fail("member size or CRC mismatch")
        self.total += len(decoded)
        return bytes(decoded)


def _manifest(payload: bytes, entries: dict, budget: _LoadBudget) -> dict:
    """Validate the complete version-one export mapping before using its metadata."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("duplicate manifest JSON key")
            result[key] = value
        return result

    def constant(value):
        _fail(f"invalid manifest JSON constant {value}")

    try:
        text = payload.decode("utf-8-sig")
        _check_limits(text, budget, "attachment ZIP manifest")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PdfValidationException("Invalid attachment ZIP manifest JSON") from error
    if not isinstance(value, dict) or value.keys() != {"schema_version", "attachments"}:
        _fail("manifest must contain schema_version and attachments")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("manifest schema_version must be 1")
    records = value["attachments"]
    if not isinstance(records, list):
        _fail("manifest attachments must be an array")
    budget.check(len(records), "max_container_items", "ZIP manifest attachments")
    result, original_names = {}, set()
    fields = {
        "name",
        "archive_name",
        "size",
        "mime_type",
        "description",
        "relationship",
        "creation_date",
        "mod_date",
    }
    for record in records:
        if not isinstance(record, dict) or record.keys() != fields:
            _fail("invalid manifest attachment fields")
        name = _text(record["name"], budget, "manifest attachment name")
        member = _text(record["archive_name"], budget, "manifest archive name")
        if member not in entries or member in result or name in original_names:
            _fail("missing or duplicate manifest attachment")
        if (
            type(record["size"]) is not int
            or record["size"] != entries[member].file_size
        ):
            _fail("manifest attachment size mismatch")
        meta = {}
        for field, key in (
            ("mime_type", "mime"),
            ("description", "description"),
            ("relationship", "relationship"),
            ("creation_date", "creation_date"),
            ("mod_date", "mod_date"),
        ):
            raw = record[field]
            if raw is None:
                continue
            _text(raw, budget, f"manifest {field}")
            if field == "relationship" and raw not in AF_RELATIONSHIPS:
                _fail("invalid manifest associated-file relationship")
            if field in {"creation_date", "mod_date"}:
                try:
                    raw = datetime.fromisoformat(raw)
                except ValueError:
                    _fail(f"invalid manifest {field}")
            meta[key] = raw
        original_names.add(name)
        result[member] = (name, meta)
    if result.keys() != entries.keys():
        _fail("manifest must describe every non-directory attachment exactly once")
    return result


def import_zip(
    document: Document,
    source: str | os.PathLike | bytes | bytearray | BinaryIO,
    *,
    names: str | Iterable[str] | None = None,
    on_conflict: str = "error",
    use_manifest: bool = True,
    compress: bool = True,
) -> int:
    """Stage all selected members and metadata, then update the attachment mappings."""
    if not isinstance(on_conflict, str) or on_conflict not in {
        "error",
        "skip",
        "replace",
        "rename",
    }:
        _fail("on_conflict must be 'error', 'skip', 'replace' or 'rename'")
    if not isinstance(use_manifest, bool) or not isinstance(compress, bool):
        _fail("use_manifest and compress must be booleans")
    budget = _LoadBudget(document.load_limits)
    data = _read(source, budget)
    _directory(data, budget)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = _entries(archive, data, budget)
            decoder = _Decoder(data, archive, budget)
            manifest = entries.pop(_MANIFEST_NAME, None) if use_manifest else None
            metadata = (
                _manifest(decoder.read(manifest, manifest=True), entries, budget)
                if manifest
                else {}
            )
            selected = _selection(names, entries, budget)
            pdf = document._engine_pdf
            used = set(pdf.attachments)
            plan, counters = [], {}
            for member in selected:
                name, meta = metadata.get(member, (_safe_name(member), {}))
                target = name
                if target in used:
                    if on_conflict == "error":
                        _fail(f"attachment name conflict: {name!r}")
                    if on_conflict == "skip":
                        continue
                    if on_conflict == "rename":
                        stem, extension = posixpath.splitext(name)
                        counter = counters.get(name, 2)
                        while target in used:
                            target = (
                                f"{stem}_{counter}{extension}"
                                if member in metadata
                                else _fit_name(name, f"_{counter}")
                            )
                            counter += 1
                        counters[name] = counter
                _text(target, budget, "imported attachment name")
                used.add(target)
                budget.check(
                    len(used), "max_container_items", "resulting PDF attachments"
                )
                plan.append((member, target, {**meta, "compress": compress}))
            payloads, updates = {}, {}
            for member, target, meta in plan:
                payloads[target] = decoder.read(entries[member])
                updates[target] = meta
    except (
        zipfile.BadZipFile,
        NotImplementedError,
        RuntimeError,
        EOFError,
        zlib.error,
        struct.error,
        UnicodeError,
        OSError,
        ValueError,
    ) as error:
        raise PdfValidationException(f"Invalid attachment ZIP: {error}") from error
    # No PDF objects or page streams are touched until all selected data is valid.
    pdf.attachments.update(payloads)
    pdf.attachment_meta.update(updates)
    for name in payloads:
        pdf.attachment_read_meta.pop(name, None)
    return len(plan)
