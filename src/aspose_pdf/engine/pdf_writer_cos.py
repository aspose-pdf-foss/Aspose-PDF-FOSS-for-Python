# PDF COS Writer
# This writer serialises a PdfDocument (COS object model) into a valid PDF byte stream.

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfDocument,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfObject,
    PdfStream,
    PdfString,
)
from .encryption import EncryptionUtils


def _is_signature(d: PdfDictionary) -> bool:
    """Whether *d* is a signature value dictionary (ISO 32000-1 12.8.1)."""
    type_name = d.mapping.get(PdfName("Type"))
    return (
        isinstance(type_name, PdfName) and type_name.name == "/Sig"
    ) or PdfName("ByteRange") in d.mapping


@dataclass(frozen=True)
class WriterEncryption:
    """The security handler, applied as :class:`PdfCosWriter` serialises.

    Encryption in PDF is per *object*: every string and every stream payload is
    enciphered with a key derived from the file key and the object's own number
    and generation (ISO 32000-1 7.6.2). That makes serialisation the only place
    it can happen, because that is where an object's number is known.

    Four things stay in the clear, and each for its own reason. The
    ``/Encrypt`` dictionary holds the values a reader needs *before* it has a
    key, so it is named in *exempt*. A cross-reference stream is how a reader
    finds that dictionary, so ISO 32000-1 7.5.8.2 leaves it plain. A
    signature's ``/Contents`` is written over the file after encryption, so
    enciphering it would destroy the signature. And the ``/Metadata`` stream is
    plain whenever the handler advertises ``/EncryptMetadata false``, which is
    the entire point of that entry.
    """

    key: bytes
    algorithm: str
    exempt: frozenset[int] = field(default_factory=frozenset)
    encrypt_metadata: bool = True

    def apply(self, obj_num: int, gen_num: int, data: bytes) -> bytes:
        """Return *data* enciphered with the key for this object."""
        return EncryptionUtils.encrypt_object_data(
            self.key, self.algorithm, obj_num, data, gen_num
        )


class PdfCosWriter:
    """Serialize a :class:`PdfDocument` to a PDF byte sequence.

    The implementation follows the basic PDF 1.7 structure:
    * Header ``%PDF-x.y``
    * Sequential indirect objects with offsets recorded
    * Cross-reference table (xref)
    * Trailer dictionary containing at least ``/Size`` and optionally ``/Root``
    * ``startxref`` pointer and ``%%EOF`` marker
    """

    def __init__(
        self,
        doc: PdfDocument,
        pdf_version: str = "1.7",
        *,
        use_object_streams: bool = False,
        encryption: WriterEncryption | None = None,
    ) -> None:
        self.doc = doc
        self.pdf_version = pdf_version
        self.use_object_streams = use_object_streams
        self.encryption = encryption
        # (number, generation) of the object being serialised, or None where
        # nothing is encrypted -- the trailer, an unencrypted document, an
        # exempt object, or a caller using this writer as a plain serialiser.
        self._crypt_obj: tuple[int, int] | None = None

    def _crypt_for(self, obj_number: int, gen_number: int = 0):
        """The object identity to encrypt with, or ``None`` to write in clear."""
        if self.encryption is None or obj_number in self.encryption.exempt:
            return None
        return (obj_number, gen_number)

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
        optimisation - the goal is a clear and correct PDF representation.
        """
        buffer = bytearray()
        # Header
        buffer.extend(f"%PDF-{self.pdf_version}\n".encode("ascii"))

        # Serialize objects and record byte offsets
        offsets: dict[int, int] = {}
        for obj_number in sorted(self.doc.objects.keys()):
            obj = self.doc.objects[obj_number]
            offsets[obj_number] = len(buffer)
            buffer.extend(f"{obj_number} 0 obj\n".encode())
            self._crypt_obj = self._crypt_for(obj_number)
            try:
                if isinstance(obj, PdfStream):
                    # Stream content is binary: emit the raw bytes verbatim so
                    # the written length matches /Length. (Decoding to latin1
                    # and re-encoding as UTF-8 would expand any byte >= 0x80.)
                    self._extend_stream_bytes(buffer, obj)
                else:
                    buffer.extend(self.serialize_object(obj).encode("utf-8"))
            finally:
                self._crypt_obj = None
            # Ensure a newline before endobj
            if not buffer.endswith(b"\n"):
                buffer.extend(b"\n")
            buffer.extend(b"endobj\n")

        # Record the start of the xref table
        xref_offset = len(buffer)
        # Size is highest object number + 1 (object 0 is the free object)
        size = max(self.doc.objects.keys(), default=0) + 1
        buffer.extend(f"xref\n0 {size}\n".encode())
        # Entry for object 0 (free entry)
        buffer.extend(b"0000000000 65535 f \n")
        for i in range(1, size):
            off = offsets.get(i, 0)
            buffer.extend(f"{off:010d} 00000 n \n".encode())

        # Trailer
        buffer.extend(b"trailer\n")
        trailer_dict = self._prepare_trailer_dict(size)
        buffer.extend(self._serialize_dictionary(trailer_dict).encode("utf-8"))
        buffer.extend(b"\n")
        buffer.extend(f"startxref\n{xref_offset}\n%%EOF".encode())
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
        # The /Encrypt dictionary joins it: a reader has to read that before it
        # has a key, so it can be neither encrypted nor buried in an object
        # stream that is (ISO 32000-1 7.5.7). Whatever is kept out of the
        # object stream still has to be written -- ``unpacked_nums`` below
        # reads this same set.
        standalone = {catalog_num}
        if self.encryption is not None:
            standalone.update(self.encryption.exempt)

        packable = [
            (num, objects[num])
            for num in sorted(objects.keys())
            if not isinstance(objects[num], PdfStream) and num not in standalone
        ]
        if not packable:
            return None

        max_existing = max(objects.keys())
        objstm_num = max_existing + 1
        xref_num = max_existing + 2

        # --- Build the object stream -------------------------------------
        # Strings inside an object stream are not enciphered individually: the
        # stream is encrypted as a whole, under its own object number
        # (ISO 32000-1 7.5.7). So the bodies are serialised in the clear here
        # and the packed result is encrypted once, below.
        bodies = [
            (num, self.serialize_object(obj).encode("latin-1"))
            for num, obj in packable
        ]
        header_parts: list[str] = []
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

        offsets: dict[int, int] = {}
        unpacked_nums = [
            num
            for num in sorted(objects.keys())
            if isinstance(objects[num], PdfStream) or num in standalone
        ]
        for num in unpacked_nums:
            offsets[num] = len(buffer)
            buffer.extend(f"{num} 0 obj\n".encode())
            obj = objects[num]
            self._crypt_obj = self._crypt_for(num)
            try:
                if isinstance(obj, PdfStream):
                    self._extend_stream_bytes(buffer, obj)
                else:
                    buffer.extend(self.serialize_object(obj).encode("utf-8"))
            finally:
                self._crypt_obj = None
            if not buffer.endswith(b"\n"):
                buffer.extend(b"\n")
            buffer.extend(b"endobj\n")

        offsets[objstm_num] = len(buffer)
        buffer.extend(f"{objstm_num} 0 obj\n".encode())
        self._crypt_obj = self._crypt_for(objstm_num)
        try:
            self._extend_stream_bytes(buffer, objstm)
        finally:
            self._crypt_obj = None
        buffer.extend(b"\nendobj\n")

        # --- Build the cross-reference stream ----------------------------
        xref_offset = len(buffer)
        offsets[xref_num] = xref_offset
        size = xref_num + 1
        packed_index = {num: i for i, (num, _data) in enumerate(bodies)}

        entries: list[tuple] = []
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
        for key_name in ("Root", "Info", "ID", "Encrypt"):
            val = self.doc.trailer.mapping.get(PdfName(key_name))
            if val is not None:
                xref_map[PdfName(key_name)] = val
        xref_stream = PdfStream(content=xref_content, mapping=xref_map)

        buffer.extend(f"{xref_num} 0 obj\n".encode())
        # ``_crypt_obj`` stays None here: the cross-reference stream is what a
        # reader parses to find /Encrypt, so neither it nor the /ID beside it
        # can be enciphered (ISO 32000-1 7.5.8.2).
        self._extend_stream_bytes(buffer, xref_stream)
        buffer.extend(b"\nendobj\n")
        buffer.extend(f"startxref\n{xref_offset}\n%%EOF".encode())
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

    def serialize_indirect(
        self, obj_number: int, obj: PdfObject, gen_number: int = 0
    ) -> str:
        """Serialise *obj* as the body of indirect object *obj_number*.

        The plain :meth:`serialize_object` has no object identity to work with,
        so it cannot encrypt; anything an encrypted document emits outside
        :meth:`write` -- an incremental revision, say -- has to come through
        here instead.
        """
        self._crypt_obj = self._crypt_for(obj_number, gen_number)
        try:
            return self.serialize_object(obj)
        finally:
            self._crypt_obj = None

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
        # Fallback - use repr (unlikely to be called)
        return repr(obj)

    def _serialize_string(self, s: PdfString) -> str:
        raw = s.value
        if self._crypt_obj is not None and self.encryption is not None:
            raw = self.encryption.apply(*self._crypt_obj, raw)
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
        parts: list[str] = []
        clear_contents = self._crypt_obj is not None and _is_signature(d)
        # Sort keys for deterministic output
        for key in sorted(d.mapping.keys(), key=lambda k: k.name):
            value = d.mapping[key]
            if clear_contents and key.name == "/Contents":
                # The CMS blob is patched into the file *after* it is written,
                # over the whole byte range the signature covers. Enciphering
                # the placeholder would leave the signer writing plaintext into
                # a slot every reader decrypts.
                saved, self._crypt_obj = self._crypt_obj, None
                try:
                    text = self.serialize_object(value)
                finally:
                    self._crypt_obj = saved
            else:
                text = self.serialize_object(value)
            parts.append(f"{key.name} {text}")
        inner = " ".join(parts)
        return f"<< {inner} >>"

    def _serialize_stream(self, stream: PdfStream) -> str:
        """Serialise a stream object as text, latin-1 standing in for bytes.

        This is the path an *appended revision* takes -- an incremental update
        emits object bodies one at a time rather than through :meth:`write` --
        so it has to encipher the payload and declare its length exactly as the
        full write does. Emitting the plaintext here instead would leave a
        stream every reader decrypts into noise.
        """
        payload, dict_repr = self._stream_parts(stream)
        return f"{dict_repr}\nstream\n{payload.decode('latin1')}\nendstream"

    def _stream_payload(self, stream: PdfStream) -> bytes:
        """The bytes to write for *stream*, enciphered where the handler says.

        A cross-reference stream is never encrypted -- it is what a reader
        parses to find the ``/Encrypt`` dictionary -- and neither is
        ``/Metadata`` under ``/EncryptMetadata false``.
        """
        content = stream.content
        if self._crypt_obj is None or self.encryption is None or not content:
            return content
        stream_type = stream.mapping.get(PdfName("Type"))
        if isinstance(stream_type, PdfName):
            if stream_type.name == "/XRef":
                return content
            if (
                stream_type.name == "/Metadata"
                and not self.encryption.encrypt_metadata
            ):
                return content
        return self.encryption.apply(*self._crypt_obj, content)

    def _stream_parts(self, stream: PdfStream) -> tuple[bytes, str]:
        """The bytes to write for *stream* and the dictionary that describes them.

        ``/Length`` is the length of what is actually written, which for an
        encrypted document is the ciphertext -- AES pads and prefixes an IV, so
        it is longer than the payload in the graph. The entry is emitted from a
        copy of the dictionary rather than written back into it: the object in
        memory still holds plaintext, and a ``/Length`` describing the
        ciphertext would be a lie to everything that reads the graph after the
        save.
        """
        payload = self._stream_payload(stream)
        mapping = dict(stream.mapping)
        mapping[PdfName("Length")] = PdfNumber(len(payload))
        return payload, self._serialize_dictionary(PdfDictionary(mapping))

    def _extend_stream_bytes(self, buffer: bytearray, stream: PdfStream) -> None:
        """Append a stream object to *buffer* with its content as raw bytes."""
        payload, dict_repr = self._stream_parts(stream)
        buffer.extend(dict_repr.encode("latin-1"))
        buffer.extend(b"\nstream\n")
        buffer.extend(payload)
        buffer.extend(b"\nendstream")

    # The writer does not maintain any mutable state beyond the document reference.
    # All methods are pure transformations.

    # End of PdfCosWriter


# PDF COS Writer
