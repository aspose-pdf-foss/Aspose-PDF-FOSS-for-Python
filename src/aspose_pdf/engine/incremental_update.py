# Incremental Update Module
"""Provides utilities for creating PDF incremental updates.

The incremental update mechanism appends new objects, a cross‑reference
section and a trailer to an existing PDF file without rewriting the
original content. This is required for digital signatures, audit trails
and efficient modifications.

Typical usage:
    with open('doc.pdf', 'rb') as f:
        original = f.read()
    updates = {
        10: b'10 0 obj\n<< /Type /Page /Parent 1 0 R >>\nendobj\n',
    }
    new_pdf = append_incremental_update(original, updates)
    with open('doc_updated.pdf', 'wb') as f:
        f.write(new_pdf)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any
from aspose_pdf.exceptions import PdfParseException


@dataclass
class IncrementalUpdate:
    """Generate an incremental update section for an existing PDF.

    Parameters
    ----------
    original_data: bytes
        The full byte content of the original PDF file.

    The constructor parses the original PDF to locate the last ``%%EOF``
    marker, the previous ``startxref`` value and the highest existing
    object number.
    """

    original_data: bytes
    original_eof_offset: int = field(init=False)
    next_obj_num: int = field(init=False)
    modified_objects: Dict[int, bytes] = field(default_factory=dict)
    xref_entries: List[Tuple[int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.original_eof_offset = self.find_last_eof()
        startxref = self.find_startxref()
        self._parse_existing_objects(startxref)
        # The next object number is one greater than the highest existing.
        self.next_obj_num = max([0] + [obj for obj, _, _ in self.xref_entries]) + 1

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def find_last_eof(self) -> int:
        """Return the start index of the last ``%%EOF`` marker."""
        pos = self.original_data.rfind(b"%%EOF")
        if pos == -1:
            raise PdfParseException("PDF does not contain an %%EOF marker")
        return pos

    def find_startxref(self) -> int:
        """Return the integer value after the last ``startxref`` keyword."""
        pattern = re.compile(rb"startxref\s*(\d+)")
        matches = list(pattern.finditer(self.original_data))
        if not matches:
            raise PdfParseException("PDF does not contain a startxref")
        return int(matches[-1].group(1))

    def _parse_existing_objects(self, startxref: int) -> None:
        """Populate ``xref_entries`` with existing object metadata."""
        data = self.original_data
        xref_start = startxref
        if xref_start + 4 > len(data) or data[xref_start : xref_start + 4] != b"xref":
            # Fallback: extract object numbers via a simple regex.
            obj_numbers = set(
                int(m.group(1)) for m in re.finditer(rb"(\d+)\s+0\s+obj", data)
            )
            for num in sorted(obj_numbers):
                self.xref_entries.append((num, 0, 0))
            return

        trailer_pos = data.find(b"trailer", xref_start)
        if trailer_pos == -1:
            trailer_pos = len(data)
        xref_blob = data[xref_start:trailer_pos]
        raw_lines = []
        for raw in xref_blob.split(b"\n"):
            line = raw.strip(b"\r")
            if line.strip():
                raw_lines.append(line)

        current_obj: int | None = None
        subsection_remaining = 0
        for line in raw_lines:
            stripped = line.strip()
            if stripped == b"xref" or stripped.startswith(b"xref"):
                continue
            parts = stripped.split()
            # Subsection header is exactly two integers; xref rows have three tokens
            # (10-digit offset, generation, n/f).
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                current_obj = int(parts[0])
                subsection_remaining = int(parts[1])
                continue
            if len(parts) >= 3 and subsection_remaining > 0 and current_obj is not None:
                try:
                    offset = int(parts[0])
                    generation = int(parts[1])
                except ValueError:
                    continue
                self.xref_entries.append((current_obj, offset, generation))
                current_obj += 1
                subsection_remaining -= 1
                if subsection_remaining == 0:
                    current_obj = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_next_object_number(self) -> int:
        """Return and reserve the next free object number."""
        obj_num = self.next_obj_num
        self.next_obj_num += 1
        return obj_num

    def add_object(self, obj_num: int, obj_bytes: bytes) -> None:
        """Add or replace an object in the incremental update.

        Parameters
        ----------
        obj_num: int
            Object number to be added or replaced.
        obj_bytes: bytes
            Full object definition, including the ``obj`` and ``endobj`` keywords.
        """
        self.modified_objects[obj_num] = obj_bytes

    def add_new_object(self, obj_bytes: bytes) -> int:
        """Add a brand‑new object and return its assigned number."""
        obj_num = self.get_next_object_number()
        self.add_object(obj_num, obj_bytes)
        return obj_num

    def build_incremental_xref(self, base_offset: int) -> bytes:
        """Create an xref section for the appended objects.

        Parameters
        ----------
        base_offset: int
            Absolute byte offset in the **combined** file where the first new
            object body begins (i.e. ``len(original_data)`` for appended updates).
        """
        if not self.modified_objects:
            return b""

        sorted_nums = sorted(self.modified_objects.keys())
        offsets: Dict[int, int] = {}
        current = base_offset
        for obj_num in sorted_nums:
            obj_data = self.modified_objects[obj_num]
            offsets[obj_num] = current
            current += len(obj_data)

        lines = [b"xref"]
        i = 0
        while i < len(sorted_nums):
            run_start_idx = i
            while i + 1 < len(sorted_nums) and sorted_nums[i + 1] == sorted_nums[i] + 1:
                i += 1
            run = sorted_nums[run_start_idx : i + 1]
            first = run[0]
            count = len(run)
            lines.append(f"{first} {count}".encode("ascii"))
            for obj_num in run:
                off = offsets[obj_num]
                # 20-byte xref lines (PDF 1.7); match PdfCosWriter / common viewers.
                lines.append(f"{off:010d} 00000 n \r".encode("latin-1"))
            i += 1
        lines.append(b"")
        return b"\n".join(lines)

    def build_incremental_trailer(
        self, prev_xref: int, new_size: int, xref_offset: int
    ) -> bytes:
        """Create a trailer referencing the previous xref table.

        Parameters
        ----------
        prev_xref: int
            Byte offset of the previous ``startxref`` value.
        new_size: int
            Updated ``/Size`` entry (total number of objects).
        xref_offset: int
            Byte offset where the new ``xref`` section begins.
        """
        trailer = (
            b"trailer\n<<\n"
            b"/Size " + str(new_size).encode("ascii") + b"\n"
            b"/Prev " + str(prev_xref).encode("ascii") + b"\n"
            b">>\n"
            b"startxref\n" + str(xref_offset).encode("ascii") + b"\n"
            b"%%EOF\n"
        )
        return trailer

    def generate(self) -> bytes:
        """Return the full incremental update bytes.

        The output consists of the new objects, a cross‑reference section and
        a trailer. The caller is responsible for appending this to the
        original PDF data.
        """
        if not self.modified_objects:
            return b""

        objects_bytes = b"".join(
            self.modified_objects[obj] for obj in sorted(self.modified_objects)
        )
        # New objects are appended after the full original file (after %%EOF, etc.).
        append_origin = len(self.original_data)
        xref_bytes = self.build_incremental_xref(append_origin)
        xref_offset = append_origin + len(objects_bytes)

        max_from_xref = max((obj for obj, _, _ in self.xref_entries), default=0)
        max_modified = max(self.modified_objects.keys())
        # /Size is highest object number + 1 (slot 0 is the free list head).
        new_size = max(self.next_obj_num, max_modified + 1, max_from_xref + 1)
        prev_startxref = self.find_startxref()

        # Carry forward /Root, /Info, /ID, etc. Losing them makes the newest
        # trailer the only one readers see first — invalid for routine loads.
        from .cos import PdfDictionary, PdfName, PdfNumber
        from .pdf_parser_cos import PdfCosParser
        from .pdf_writer_cos import PdfCosWriter

        prev_doc = PdfCosParser(self.original_data).parse()
        trailer_dict = PdfDictionary(dict(prev_doc.trailer.mapping))
        trailer_dict.mapping[PdfName("Size")] = PdfNumber(new_size)
        trailer_dict.mapping[PdfName("Prev")] = PdfNumber(prev_startxref)
        writer = PdfCosWriter(prev_doc)
        serialized = writer.serialize_object(trailer_dict)
        trailer_bytes = (
            b"trailer\n"
            + serialized.encode("latin-1")
            + b"\n"
            + b"startxref\n"
            + str(xref_offset).encode("ascii")
            + b"\n"
            + b"%%EOF\n"
        )

        return objects_bytes + xref_bytes + trailer_bytes


class IncrementalWriter:
    """Utility that appends incremental updates to an existing PDF.

    The writer expects a *pdf_document* object that provides ``original_data``
    (the original PDF bytes) and an ``updates`` mapping where keys are object
    numbers and values are the full object definitions.
    """

    def __init__(self, pdf_document: Any) -> None:
        self.pdf_document = pdf_document

    def write_incremental(self) -> bytes:
        """Return the original PDF with the incremental update appended."""
        original = getattr(self.pdf_document, "original_data", None)
        if original is None:
            raise AttributeError("pdf_document must have attribute original_data")
        updates: Dict[int, bytes] = getattr(self.pdf_document, "updates", {})

        incremental = IncrementalUpdate(original)
        for obj_num, obj_bytes in updates.items():
            incremental.add_object(obj_num, obj_bytes)

        inc_bytes = incremental.generate()
        return original + inc_bytes

    def _build_signature_placeholder(self, byte_range_start: int) -> bytes:
        """Create a simple ``/Sig`` placeholder for digital signatures.

        This placeholder follows the typical structure used by many PDF
        libraries and can be later replaced with a real signature value.
        """
        placeholder = (
            b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
            b"/ByteRange [" + str(byte_range_start).encode("ascii") + b" 0 0 0] "
            b"/Contents <" + b"0" * 8192 + b"> >>\n"
        )
        return placeholder


def append_incremental_update(original_pdf: bytes, updates: Dict[int, bytes]) -> bytes:
    """Convenience function to append *updates* to *original_pdf*.

    Parameters
    ----------
    original_pdf: bytes
        The original PDF file content.
    updates: dict[int, bytes]
        Mapping of object numbers to their new definitions.

    Returns
    -------
    bytes
        The combined PDF data with an incremental update section.
    """
    if not updates:
        return original_pdf

    inc = IncrementalUpdate(original_pdf)
    for obj_num, obj_bytes in updates.items():
        inc.add_object(obj_num, obj_bytes)
    return original_pdf + inc.generate()
