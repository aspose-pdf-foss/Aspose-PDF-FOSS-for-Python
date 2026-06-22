# PDF COS Writer
# This writer serialises a PdfDocument (COS object model) into a valid PDF byte stream.

from __future__ import annotations

import zlib
from typing import Dict, List

from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfDocument,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfNull,
    PdfObject,
    PdfString,
    PdfStream,
)


class PdfCosWriter:
    """Serialize a :class:`PdfDocument` to a PDF byte sequence.

    The implementation follows the basic PDF 1.7 structure:
    * Header ``%PDF-x.y``
    * Sequential indirect objects with offsets recorded
    * Cross‑reference table (xref)
    * Trailer dictionary containing at least ``/Size`` and optionally ``/Root``
    * ``startxref`` pointer and ``%%EOF`` marker
    """

    def __init__(
        self,
        doc: PdfDocument,
        pdf_version: str = "1.7",
        *,
        use_object_streams: bool = False,
    ) -> None:
        self.doc = doc
        self.pdf_version = pdf_version
        self.use_object_streams = use_object_streams

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def write(self) -> bytes:
        """Serialise the document and return the PDF bytes.

        Emits a classic cross-reference table by default. When the writer was
        created with ``use_object_streams=True`` (and the document has objects
        worth packing), a PDF 1.5+ layout is produced instead: eligible objects
        are bundled into an object stream and located by a cross-reference
        stream — the single biggest file-size lever.
        """
        if self.use_object_streams:
            compressed = self._write_compressed()
            if compressed is not None:
                return compressed
        return self._write_classic()

    def _write_classic(self) -> bytes:
        """Serialise the document with a traditional ``xref`` table.

        The method tracks object offsets, builds the xref table and constructs
        the trailer. It does **not** attempt any compression or object stream
        optimisation – the goal is a clear and correct PDF representation.
        """
        buffer = bytearray()
        # Header
        buffer.extend(f"%PDF-{self.pdf_version}\n".encode("ascii"))

        # Serialize objects and record byte offsets
        offsets: Dict[int, int] = {}
        for obj_number in sorted(self.doc.objects.keys()):
            obj = self.doc.objects[obj_number]
            offsets[obj_number] = len(buffer)
            buffer.extend(f"{obj_number} 0 obj\n".encode("utf-8"))
            if isinstance(obj, PdfStream):
                # Stream content is binary: emit the raw bytes verbatim so the
                # written length matches /Length. (Decoding to latin1 and then
                # re-encoding as UTF-8 would expand any byte >= 0x80.)
                self._extend_stream_bytes(buffer, obj)
            else:
                buffer.extend(self.serialize_object(obj).encode("utf-8"))
            # Ensure a newline before endobj
            if not buffer.endswith(b"\n"):
                buffer.extend(b"\n")
            buffer.extend(b"endobj\n")

        # Record the start of the xref table
        xref_offset = len(buffer)
        # Size is highest object number + 1 (object 0 is the free object)
        size = max(self.doc.objects.keys(), default=0) + 1
        buffer.extend(f"xref\n0 {size}\n".encode("utf-8"))
        # Entry for object 0 (free entry)
        buffer.extend(b"0000000000 65535 f \n")
        for i in range(1, size):
            off = offsets.get(i, 0)
            buffer.extend(f"{off:010d} 00000 n \n".encode("utf-8"))

        # Trailer
        buffer.extend(b"trailer\n")
        trailer_dict = self._prepare_trailer_dict(size)
        buffer.extend(self._serialize_dictionary(trailer_dict).encode("utf-8"))
        buffer.extend(b"\n")
        buffer.extend(f"startxref\n{xref_offset}\n%%EOF".encode("utf-8"))
        return bytes(buffer)

    # ---------------------------------------------------------------------
    # Object-stream / cross-reference-stream layout (PDF 1.5+)
    # ---------------------------------------------------------------------
    def _write_compressed(self) -> bytes | None:
        """Serialise using an object stream + cross-reference stream.

        Eligible (gen-0, non-stream) objects are packed into a single
        ``/ObjStm`` and located by a ``/XRef`` stream; streams and the document
        catalog remain standalone indirect objects. Returns ``None`` when there
        is nothing worth packing so the caller falls back to the classic
        layout. The output round-trips through ``PdfCosParser`` (which reads
        both ``/ObjStm`` and ``/XRef`` streams).
        """
        objects = self.doc.objects
        if not objects:
            return None

        # Keep the document catalog out of the object stream for maximum
        # reader compatibility.
        root_ref = self.doc.trailer.mapping.get(PdfName("Root"))
        catalog_num = (
            root_ref.object_number
            if isinstance(root_ref, PdfIndirectReference)
            else None
        )

        packable = [
            (num, objects[num])
            for num in sorted(objects.keys())
            if not isinstance(objects[num], PdfStream) and num != catalog_num
        ]
        if not packable:
            return None

        max_existing = max(objects.keys())
        objstm_num = max_existing + 1
        xref_num = max_existing + 2

        # --- Build the object stream -------------------------------------
        bodies = [
            (num, self.serialize_object(obj).encode("latin-1"))
            for num, obj in packable
        ]
        header_parts: List[str] = []
        running = 0
        for num, data in bodies:
            header_parts.append(f"{num} {running} ")
            running += len(data) + 1  # +1 for the newline separating bodies
        header_bytes = "".join(header_parts).encode("latin-1")
        first = len(header_bytes)
        body_region = bytearray()
        for _num, data in bodies:
            body_region.extend(data)
            body_region.extend(b"\n")
        objstm_content = zlib.compress(header_bytes + bytes(body_region), 9)
        objstm = PdfStream(
            content=objstm_content,
            mapping={
                PdfName("Type"): PdfName("ObjStm"),
                PdfName("N"): PdfNumber(len(bodies)),
                PdfName("First"): PdfNumber(first),
                PdfName("Filter"): PdfName("FlateDecode"),
                PdfName("Length"): PdfNumber(len(objstm_content)),
            },
        )

        # --- Serialise standalone objects (streams + catalog + ObjStm) ----
        version = self.pdf_version
        try:
            if float(version) < 1.5:
                version = "1.5"
        except (ValueError, TypeError):
            version = "1.5"
        buffer = bytearray()
        buffer.extend(f"%PDF-{version}\n".encode("ascii"))

        offsets: Dict[int, int] = {}
        unpacked_nums = [
            num
            for num in sorted(objects.keys())
            if isinstance(objects[num], PdfStream) or num == catalog_num
        ]
        for num in unpacked_nums:
            offsets[num] = len(buffer)
            buffer.extend(f"{num} 0 obj\n".encode("utf-8"))
            obj = objects[num]
            if isinstance(obj, PdfStream):
                self._extend_stream_bytes(buffer, obj)
            else:
                buffer.extend(self.serialize_object(obj).encode("utf-8"))
            if not buffer.endswith(b"\n"):
                buffer.extend(b"\n")
            buffer.extend(b"endobj\n")

        offsets[objstm_num] = len(buffer)
        buffer.extend(f"{objstm_num} 0 obj\n".encode("utf-8"))
        self._extend_stream_bytes(buffer, objstm)
        buffer.extend(b"\nendobj\n")

        # --- Build the cross-reference stream ----------------------------
        xref_offset = len(buffer)
        offsets[xref_num] = xref_offset
        size = xref_num + 1
        packed_index = {num: i for i, (num, _data) in enumerate(bodies)}

        entries: List[tuple] = []
        for n in range(size):
            if n == 0:
                entries.append((0, 0, 65535))
            elif n in packed_index:
                entries.append((2, objstm_num, packed_index[n]))
            elif n in offsets:
                entries.append((1, offsets[n], 0))
            else:
                entries.append((0, 0, 0))

        def _width(value: int) -> int:
            return max(1, (value.bit_length() + 7) // 8)

        w1 = 1
        w2 = _width(max(f2 for _t, f2, _f3 in entries))
        w3 = _width(max(f3 for _t, _f2, f3 in entries))
        entry_bytes = bytearray()
        for t, f2, f3 in entries:
            entry_bytes.extend(t.to_bytes(w1, "big"))
            entry_bytes.extend(f2.to_bytes(w2, "big"))
            entry_bytes.extend(f3.to_bytes(w3, "big"))
        xref_content = zlib.compress(bytes(entry_bytes), 9)

        xref_map = {
            PdfName("Type"): PdfName("XRef"),
            PdfName("Size"): PdfNumber(size),
            PdfName("W"): PdfArray([PdfNumber(w1), PdfNumber(w2), PdfNumber(w3)]),
            PdfName("Index"): PdfArray([PdfNumber(0), PdfNumber(size)]),
            PdfName("Filter"): PdfName("FlateDecode"),
            PdfName("Length"): PdfNumber(len(xref_content)),
        }
        # Carry the document references the trailer needs into the XRef dict.
        for key_name in ("Root", "Info", "ID"):
            val = self.doc.trailer.mapping.get(PdfName(key_name))
            if val is not None:
                xref_map[PdfName(key_name)] = val
        xref_stream = PdfStream(content=xref_content, mapping=xref_map)

        buffer.extend(f"{xref_num} 0 obj\n".encode("utf-8"))
        self._extend_stream_bytes(buffer, xref_stream)
        buffer.extend(b"\nendobj\n")
        buffer.extend(f"startxref\n{xref_offset}\n%%EOF".encode("utf-8"))
        return bytes(buffer)

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _prepare_trailer_dict(self, size: int) -> PdfDictionary:
        """Return a trailer dictionary ensuring required entries.

        The supplied ``self.doc.trailer`` may already contain entries such as
        ``/Root``. We add ``/Size`` if missing and return a new dictionary that
        merges both.
        """
        trailer = PdfDictionary(dict(self.doc.trailer.mapping))
        # A full rewrite emits every object sequentially, so the computed size
        # (highest object number + 1) is authoritative. Always set it, otherwise
        # a stale /Size carried over from the source trailer can hide objects
        # added after load (e.g. a newly written /Metadata stream).
        trailer.mapping[PdfName("Size")] = PdfNumber(size)
        return trailer

    def serialize_object(self, obj: PdfObject) -> str:
        """Dispatch serialisation based on object type."""
        if isinstance(obj, PdfNull):
            return "null"
        if isinstance(obj, PdfBoolean):
            return "true" if obj.value else "false"
        if isinstance(obj, PdfNumber):
            return str(obj.value)
        if isinstance(obj, PdfString):
            return self._serialize_string(obj)
        if isinstance(obj, PdfName):
            return obj.name
        if isinstance(obj, PdfArray):
            return self._serialize_array(obj)
        if isinstance(
            obj, PdfStream
        ):  # Check PdfStream BEFORE PdfDictionary (subclass)
            return self._serialize_stream(obj)
        if isinstance(obj, PdfDictionary):
            return self._serialize_dictionary(obj)
        if isinstance(obj, PdfIndirectReference):
            return f"{obj.object_number} {obj.gen_number} R"
        # Fallback – use repr (unlikely to be called)
        return repr(obj)

    def _serialize_string(self, s: PdfString) -> str:
        raw = s.value
        # Use hex string notation for any value that contains bytes outside
        # the printable ASCII range — this covers binary data such as file IDs.
        if any(b < 0x20 or b > 0x7E for b in raw):
            return f"<{raw.hex()}>"
        # Safe to use literal-string notation for printable ASCII.
        txt = raw.decode("latin-1")
        txt = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return f"({txt})"

    def _serialize_array(self, arr: PdfArray) -> str:
        items = " ".join(self.serialize_object(item) for item in arr.items)
        return f"[ {items} ]"

    def _serialize_dictionary(self, d: PdfDictionary) -> str:
        parts: List[str] = []
        # Sort keys for deterministic output
        for key in sorted(d.mapping.keys(), key=lambda k: k.name):
            value = d.mapping[key]
            parts.append(f"{key.name} {self.serialize_object(value)}")
        inner = " ".join(parts)
        return f"<< {inner} >>"

    def _serialize_stream(self, stream: PdfStream) -> str:
        # Ensure Length entry is present – required for PDF readers.
        length_key = PdfName("Length")
        if length_key not in stream.mapping:
            stream.mapping[length_key] = PdfNumber(len(stream.content))
        dict_repr = self._serialize_dictionary(stream)
        # Content can be binary, but serialize_object returns str.
        # We need to handle binary content correctly.
        return f"{dict_repr}\nstream\n{stream.content.decode('latin1')}\nendstream"

    def _extend_stream_bytes(self, buffer: bytearray, stream: PdfStream) -> None:
        """Append a stream object to *buffer* with its content as raw bytes.

        ``/Length`` is set to the exact byte length of the content so it always
        aligns with the ``endstream`` keyword, regardless of the bytes written.
        """
        stream.mapping[PdfName("Length")] = PdfNumber(len(stream.content))
        dict_repr = self._serialize_dictionary(stream)
        buffer.extend(dict_repr.encode("latin-1"))
        buffer.extend(b"\nstream\n")
        buffer.extend(stream.content)
        buffer.extend(b"\nendstream")

    # The writer does not maintain any mutable state beyond the document reference.
    # All methods are pure transformations.

    # End of PdfCosWriter


# PDF COS Writer
