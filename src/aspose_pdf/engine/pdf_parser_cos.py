# PDF COS Parser

"""Implementation of a minimal PDF COS parser.

The parser focuses on the essential PDF structures required for the SDK:

* ``startxref`` – locate the cross‑reference table.
* Traditional ``xref`` table (not streams) – map object numbers to file offsets.
* ``trailer`` dictionary – contains the ``Root`` reference.
* PDF objects – numbers, booleans, null, strings, names, arrays, dictionaries,
  streams and indirect references.

Object bodies are loaded **lazily** on first access via ``PdfDocument.objects``
(uncompressed ``xref`` offsets plus object-stream entries). Memory scales with
objects actually read, not the full ``xref`` size.
"""

from __future__ import annotations

import io
import logging
import mmap
import re
import zlib
from collections.abc import MutableMapping
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfDocument,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import PdfParseException

logger = logging.getLogger("aspose_pdf")


def _cos_buffer_rfind(buf, needle: bytes) -> int:
    """Return the last index of *needle* in *buf*, or -1.

    Supports :class:`bytes`, :class:`mmap.mmap`, and other buffers; falls back to
    a byte scan when ``rfind`` is not available (e.g. :class:`memoryview`).
    """

    finder = getattr(buf, "rfind", None)
    if callable(finder):
        return finder(needle)

    nlen = len(needle)
    blen = len(buf)
    if nlen == 0:
        return blen
    if nlen > blen:
        return -1
    for start in range(blen - nlen, -1, -1):
        matched = True
        for j in range(nlen):
            if buf[start + j] != needle[j]:
                matched = False
                break
        if matched:
            return start
    return -1


def _cos_dictionary_bytes_at(data: bytes, start: int) -> Optional[bytes]:
    """Return the PDF dictionary bytes starting at *start*, using balanced ``<<``/``>>``.

    A DOTALL non-greedy regex such as ``<<.*?>>`` stops at the first ``>>``, which
    breaks trailers (and other dicts) that contain nested dictionaries.
    """
    if start < 0 or start + 2 > len(data) or data[start : start + 2] != b"<<":
        return None
    depth = 1
    i = start + 2
    n = len(data)
    while i < n:
        if i + 1 < n and data[i : i + 2] == b"<<":
            depth += 1
            i += 2
            continue
        if i + 1 < n and data[i : i + 2] == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return data[start:i]
            continue
        i += 1
    return None


_PDF_WS = b" \t\n\r\f\x00"
_ENDSTREAM_KW = b"endstream"


def _skip_pdf_whitespace(data, pos: int, limit: int) -> int:
    while pos < limit and data[pos] in _PDF_WS:
        pos += 1
    return pos


def _skip_pdf_whitespace_and_comments(data, pos: int, limit: int) -> int:
    """Whitespace and ``% … EOL`` comments (PDF 1.7) before ``endobj``."""
    while pos < limit:
        if data[pos] in _PDF_WS:
            pos += 1
            continue
        if data[pos] == 0x25:  # '%'
            pos += 1
            while pos < limit and data[pos] not in b"\r\n":
                pos += 1
            continue
        break
    return pos


def _find_rightmost_endstream_token(data, stream_start: int, content_end: int) -> int:
    """Locate the closing ``endstream`` keyword within ``[stream_start, content_end)``.

    Without a trusted ``/Length``, naive substring search can match ``endstream`` bytes
    inside stream content or in a later object. We bound the search to the
    current object's body and prefer the **rightmost** token-shaped occurrence (closest to
    ``endobj``), which matches usual PDF layout when the payload contains false positives.
    """
    if stream_start > content_end:
        raise PdfParseException("malformed stream: invalid object span")
    window = data[stream_start:content_end]
    wlen = len(window)
    tok, tlen = _ENDSTREAM_KW, len(_ENDSTREAM_KW)
    pos = wlen
    while pos > 0:
        rel = window.rfind(tok, 0, pos)
        if rel < 0:
            break
        before_ok = rel == 0 or window[rel - 1] in _PDF_WS
        after_idx = rel + tlen
        after_ok = after_idx == wlen or (
            window[after_idx] in _PDF_WS or window[after_idx] in b"()<>[]{}/%"
        )
        if before_ok and after_ok:
            return stream_start + rel
        pos = rel
    raise PdfParseException("endstream not found in object")


def _extract_stream_bytes(
    data,
    stream_start: int,
    content_end: int,
    declared_length: Optional[int],
) -> bytes:
    """Stream payload bytes for ``stream`` … ``endstream`` within one indirect object."""
    if stream_start > content_end:
        raise PdfParseException("malformed stream: stream starts after endobj boundary")
    tok, tlen = _ENDSTREAM_KW, len(_ENDSTREAM_KW)

    if declared_length is not None:
        if declared_length < 0:
            raise PdfParseException("stream /Length must be non-negative")
        if stream_start + declared_length > content_end:
            raise PdfParseException(
                "stream /Length extends past object; malformed stream boundaries"
            )
        j = _skip_pdf_whitespace(data, stream_start + declared_length, content_end)
        if j + tlen > content_end or data[j : j + tlen] != tok:
            raise PdfParseException(
                "stream /Length does not align with endstream keyword (malformed stream)"
            )
        k = _skip_pdf_whitespace_and_comments(data, j + tlen, content_end)
        if k != content_end:
            raise PdfParseException("unexpected data after endstream in stream object")
        return bytes(data[stream_start : stream_start + declared_length])

    end_at = _find_rightmost_endstream_token(data, stream_start, content_end)
    k = _skip_pdf_whitespace_and_comments(data, end_at + tlen, content_end)
    if k != content_end:
        raise PdfParseException("unexpected data after endstream in stream object")
    return bytes(data[stream_start:end_at])


class _Tokenizer:
    """A tiny recursive‑descent tokenizer for PDF syntax.

    The source is a ``str`` decoded with ``latin‑1`` so that each byte maps to a
    Unicode code point preserving raw byte values for hex strings and streams.
    """

    def __init__(self, data: str) -> None:
        self.s = data
        self.pos = 0
        self.len = len(data)

    # ---------------------------------------------------------------------
    # Basic helpers
    # ---------------------------------------------------------------------
    def _peek(self) -> str:
        return self.s[self.pos] if self.pos < self.len else ""

    def _consume(self, n: int = 1) -> None:
        self.pos += n

    def _consume_whitespace(self) -> None:
        while self.pos < self.len and self.s[self.pos] in " \t\r\n\0":
            self.pos += 1

    def _match(self, text: str) -> bool:
        if self.s.startswith(text, self.pos):
            self._consume(len(text))
            return True
        return False

    # ---------------------------------------------------------------------
    # Token readers
    # ---------------------------------------------------------------------
    def read(self) -> Any:
        self._consume_whitespace()
        ch = self._peek()
        if ch == "<":
            if self.s.startswith("<<", self.pos):
                return self._read_dictionary()
            else:
                return self._read_hex_string()
        if ch == "[":
            return self._read_array()
        if ch == "(":
            return self._read_literal_string()
        if ch == "/":
            return self._read_name()
        if ch.isdigit() or ch == "-" or ch == "+":
            # Could be a number or an indirect reference; resolve later.
            return self._read_number_or_reference()
        if ch in "tf":
            # true / false
            word = self._read_word()
            if word == "true":
                return PdfBoolean(True)
            if word == "false":
                return PdfBoolean(False)
        if ch == "n":
            word = self._read_word()
            if word == "null":
                return PdfNull()
        raise PdfParseException(f"Unexpected token at position {self.pos}: {ch!r}")

    # ---------------------------------------------------------------------
    # Individual token implementations
    # ---------------------------------------------------------------------
    def _read_word(self) -> str:
        start = self.pos
        while self.pos < self.len and self.s[self.pos].isalpha():
            self.pos += 1
        return self.s[start : self.pos]

    def _read_name(self) -> PdfName:
        self._consume()  # consume '/'
        start = self.pos
        while self.pos < self.len and self.s[self.pos] not in " \t\r\n<>[]()/":
            self.pos += 1
        name = self.s[start : self.pos]
        return PdfName(name)

    def _read_number(self) -> PdfNumber:
        start = self.pos
        # optional sign
        if self._peek() in "+-":
            self._consume()
        while self.pos < self.len and (
            self.s[self.pos].isdigit() or self.s[self.pos] == "."
        ):
            self._consume()
        num_str = self.s[start : self.pos]
        if "." in num_str:
            value: Union[int, float] = float(num_str)
        else:
            value = int(num_str)
        return PdfNumber(value)

    def _read_number_or_reference(self) -> Any:
        # Peek ahead to see if we have "num gen R"
        saved = self.pos
        first = self._read_number()
        self._consume_whitespace()
        # check for second number
        if self._peek().isdigit() or self._peek() in "+-":
            second = self._read_number()
            self._consume_whitespace()
            if self.s.startswith("R", self.pos):
                self._consume()  # consume 'R'
                return PdfIndirectReference(int(first.value), int(second.value))
        # not a reference – restore and return first number
        self.pos = saved
        return self._read_number()

    def _read_literal_string(self) -> PdfString:
        self._consume()  # '(' opening
        result = []
        depth = 1
        while self.pos < self.len and depth > 0:
            ch = self._peek()
            if ch == "\\":
                # escaped character – keep the next char literally
                self._consume(2)
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    self._consume()  # consume final ')'
                    break
            result.append(ch)
            self._consume()
        return PdfString("".join(result))

    def _read_hex_string(self) -> PdfString:
        self._consume()  # '<' opening
        hex_chars = []
        while self.pos < self.len:
            ch = self._peek()
            if ch == ">":
                self._consume()
                break
            if ch in " \t\r\n":
                self._consume()
                continue
            hex_chars.append(ch)
            self._consume()
        hex_str = "".join(hex_chars)
        # Pad with a zero if odd length
        if len(hex_str) % 2 == 1:
            hex_str += "0"
        raw = bytes.fromhex(hex_str)
        return PdfString(raw)

    def _read_array(self) -> PdfArray:
        self._consume()  # '['
        items: List[Any] = []
        while True:
            self._consume_whitespace()
            if self._peek() == "]":
                self._consume()
                break
            items.append(self.read())
        return PdfArray(items)

    def _read_dictionary(self) -> PdfDictionary:
        self._consume(2)  # '<<'
        mapping: Dict[PdfName, Any] = {}
        while True:
            self._consume_whitespace()
            if self.s.startswith(">>", self.pos):
                self._consume(2)
                break
            key = self._read_name()
            value = self.read()
            mapping[key] = value
        return PdfDictionary(mapping)


class PdfCosParser:
    """Parse a PDF file (bytes) into a :class:`PdfDocument`.

    The implementation supports both classic cross‑reference tables and
    PDF 1.5+ XRef Streams and Object Streams. It builds xref metadata up front
    and materializes each object body when first read through ``doc.objects``.
    """

    def __init__(self, data) -> None:
        # Accept bytes, mmap, memoryview, or file-like. Avoid a full-stream
        # read() when the source is mmap-able (real file) or BytesIO (buffer view).
        if isinstance(data, mmap.mmap):
            self._data = data
        elif hasattr(data, "read"):
            mm: Optional[mmap.mmap] = None
            if not isinstance(data, io.BytesIO):
                try:
                    fd = data.fileno()
                except (AttributeError, OSError):
                    fd = None
                else:
                    try:
                        mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
                    except OSError:
                        mm = None
            if mm is not None:
                self._data = mm
            elif isinstance(data, io.BytesIO):
                pos = data.tell()
                whole = data.getvalue()
                # Use the underlying buffer (not read()) so we don't allocate a
                # second full copy; honour the current stream position like read().
                self._data = whole[pos:] if pos else whole
            else:
                raw = data.read()
                if not isinstance(raw, (bytes, bytearray)):
                    raise TypeError("file read() must return bytes")
                self._data = bytes(raw) if isinstance(raw, bytearray) else raw
        else:
            self._data = data
        self._objects: Dict[int, Any] = {}
        self._compressed_objects: Dict[
            int, Tuple[int, int]
        ] = {}  # obj_num -> (objstm_num, index)
        self.trailer: PdfDictionary = PdfDictionary()

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def parse(self) -> PdfDocument:
        doc = PdfDocument()
        try:
            self._compressed_objects.clear()
            startxref_offset = self._find_startxref()
            xref_offset = self._read_int_at(startxref_offset)

            # Process xref chain (may include multiple /Prev sections)
            all_xref: Dict[int, int] = {}
            current_offset = xref_offset
            trailer_dict = None

            while current_offset is not None:
                xref_table, trailer = self._parse_xref_section(current_offset)
                # Earlier entries take precedence over later (newer updates first)
                for obj_num, off in xref_table.items():
                    if obj_num not in all_xref:
                        all_xref[obj_num] = off
                if trailer_dict is None:
                    trailer_dict = trailer
                # Check for /Prev
                prev_ref = trailer.mapping.get(PdfName("Prev"))
                if isinstance(prev_ref, PdfNumber):
                    current_offset = int(prev_ref.value)
                else:
                    current_offset = None

            doc.xref_table = all_xref
            doc.trailer = trailer_dict
            self.trailer = trailer_dict

        except (PdfParseException, ValueError, IndexError, zlib.error):
            # Recover only when the primary xref/startxref path failed for
            # xref-typical reasons. Broader ``Exception`` hid non-xref bugs
            # and produced partial/wrong graphs.
            self._compressed_objects.clear()
            all_xref, trailer_dict = self._reconstruct_xref()
            doc.xref_table = all_xref
            doc.trailer = trailer_dict
            self.trailer = trailer_dict

        xref_used = {num: off for num, off in all_xref.items() if off > 0}
        lazy_map = LazyPdfObjectStore(self, xref_used, dict(self._compressed_objects))
        doc.objects = lazy_map
        self._objects = lazy_map

        # If Root is still missing, scan object numbers (load one at a time)
        if PdfName("Root") not in doc.trailer:
            candidate_ids = sorted(set(xref_used) | set(self._compressed_objects))
            for obj_num in candidate_ids:
                try:
                    obj = lazy_map[obj_num]
                except KeyError:
                    continue
                if isinstance(obj, PdfDictionary) and PdfName("Type") in obj.mapping:
                    if obj.mapping[PdfName("Type")] == PdfName("Catalog"):
                        doc.trailer[PdfName("Root")] = PdfIndirectReference(obj_num)
                        break

        return doc

    def get_object(self, ref: PdfIndirectReference) -> Any:
        """Retrieve a parsed object by its indirect reference."""
        return self._objects.get(ref.object_number)

    # ---------------------------------------------------------------------
    # XRef Section Parsing
    # ---------------------------------------------------------------------
    def _reconstruct_xref(self) -> Tuple[Dict[int, int], PdfDictionary]:
        """Scans the entire file for 'obj' markers to reconstruct a missing/corrupted XRef."""
        logger.warning(
            "XRef table missing or corrupted. Initiating full-file reconstruction scan."
        )
        xref_table: Dict[int, int] = {}
        trailer = PdfDictionary()

        # Scan for "N G obj"
        obj_regex = re.compile(rb"(\d+)\s+(\d+)\s+obj")
        for match in obj_regex.finditer(self._data):
            obj_num = int(match.group(1))
            offset = match.start()
            # In a multi-update PDF, later objects override earlier ones
            xref_table[obj_num] = offset

        # Scan for "trailer" to find Root/Info (balanced dict — nested << >> safe)
        kw = b"trailer"
        pos = 0
        while True:
            idx = self._data.find(kw, pos)
            if idx == -1:
                break
            j = idx + len(kw)
            while j < len(self._data) and self._data[j] in b" \t\r\n":
                j += 1
            dict_bytes = _cos_dictionary_bytes_at(self._data, j)
            if dict_bytes is not None:
                try:
                    t = self._parse_dictionary(dict_bytes)
                    for k, v in t.mapping.items():
                        trailer[k] = v
                except Exception:
                    pass
            pos = idx + 1

        self._rebuild_compressed_objects_from_objstms(xref_table)
        return xref_table, trailer

    def _object_stream_members_in_order(self, objstm: PdfStream) -> List[int]:
        """Object numbers stored in *objstm* header (PDF order), or empty if unusable."""
        from .filters import StreamDecoder

        n_obj = objstm.mapping.get(PdfName("N"))
        first_obj = objstm.mapping.get(PdfName("First"))
        if not isinstance(n_obj, PdfNumber) or not isinstance(first_obj, PdfNumber):
            return []
        n = int(n_obj.value)
        if n <= 0:
            return []

        filter_obj = objstm.mapping.get(PdfName("Filter"))
        filter_name = None
        if isinstance(filter_obj, PdfName):
            filter_name = filter_obj.name.lstrip("/")
        decode_parms = objstm.mapping.get(PdfName("DecodeParms"))

        content = objstm.content
        if filter_name:
            try:
                content = StreamDecoder.decode(content, filter_name, decode_parms)
            except Exception:
                return []

        try:
            text = content.decode("latin-1")
        except Exception:
            return []

        tokenizer = _Tokenizer(text)
        nums: List[int] = []
        try:
            for _ in range(n):
                tokenizer._consume_whitespace()
                obj_num = int(tokenizer._read_number().value)
                tokenizer._consume_whitespace()
                tokenizer._read_number()
                nums.append(obj_num)
        except (ValueError, IndexError, AttributeError):
            return []
        return nums

    def _rebuild_compressed_objects_from_objstms(
        self, xref_table: Dict[int, int]
    ) -> None:
        """Register object-stream members when xref metadata was rebuilt by scanning."""
        for stm_obj_num, off in xref_table.items():
            if off <= 0:
                continue
            try:
                obj = self._parse_object_at(off)
            except (PdfParseException, ValueError, IndexError, KeyError):
                continue
            if not isinstance(obj, PdfStream):
                continue
            if obj.mapping.get(PdfName("Type")) != PdfName("ObjStm"):
                continue
            for idx, member_num in enumerate(self._object_stream_members_in_order(obj)):
                if member_num == stm_obj_num:
                    continue
                if xref_table.get(member_num, 0) > 0:
                    continue
                self._compressed_objects[member_num] = (stm_obj_num, idx)

    def _parse_xref_section(self, offset: int) -> Tuple[Dict[int, int], PdfDictionary]:
        """Parse an xref section at the given offset.

        Detects whether it's a traditional xref table or an XRef Stream.
        """
        if self._data[offset : offset + 4] == b"xref":
            return self._parse_traditional_xref(offset)
        else:
            # XRef Stream (PDF 1.5+)
            return self._parse_xref_stream(offset)

    def _parse_xref_stream(self, offset: int) -> Tuple[Dict[int, int], PdfDictionary]:
        """Parse an XRef Stream (PDF 1.5+)."""
        from .filters import StreamDecoder

        stream_obj = self._parse_object_at(offset)
        if not isinstance(stream_obj, PdfStream):
            raise PdfParseException(f"Expected XRef Stream at offset {offset}")

        # The stream dictionary IS the trailer
        trailer = PdfDictionary(stream_obj.mapping)

        # Get /W array (field widths)
        w_obj = stream_obj.mapping.get(PdfName("W"))
        if not isinstance(w_obj, PdfArray) or len(w_obj.items) != 3:
            raise PdfParseException("XRef Stream missing or invalid /W array")
        w: List[int] = []
        for x in w_obj.items:
            if not isinstance(x, PdfNumber):
                raise PdfParseException(
                    "XRef Stream /W entries must be numbers",
                )
            wi = int(x.value)
            if wi < 0:
                raise PdfParseException(
                    "XRef Stream /W entries must be non-negative",
                )
            w.append(wi)
        entry_size = sum(w)
        if entry_size <= 0:
            raise PdfParseException(
                "XRef Stream /W widths must sum to a positive entry size",
            )

        # Get /Size (required for subsection layout)
        size_obj = stream_obj.mapping.get(PdfName("Size"))
        if not isinstance(size_obj, PdfNumber):
            raise PdfParseException("XRef Stream missing or invalid /Size")
        size = int(size_obj.value)
        if size < 1:
            raise PdfParseException("XRef Stream /Size must be at least 1")

        # Get /Index (optional, default [0, Size])
        index_obj = stream_obj.mapping.get(PdfName("Index"))
        if isinstance(index_obj, PdfArray):
            index_data = [
                int(x.value) if isinstance(x, PdfNumber) else 0 for x in index_obj.items
            ]
        else:
            index_data = [0, size]

        # Decode stream content
        filter_obj = stream_obj.mapping.get(PdfName("Filter"))
        filter_name = None
        if isinstance(filter_obj, PdfName):
            filter_name = filter_obj.name.lstrip("/")
        decode_parms = stream_obj.mapping.get(PdfName("DecodeParms"))

        content = stream_obj.content
        if filter_name:
            content = StreamDecoder.decode(content, filter_name, decode_parms)

        # Parse entries
        xref_table: Dict[int, int] = {}
        pos = 0

        # Process subsections from /Index
        for i in range(0, len(index_data), 2):
            start_obj = index_data[i]
            count = index_data[i + 1] if i + 1 < len(index_data) else 0

            for j in range(count):
                obj_num = start_obj + j
                if pos + entry_size > len(content):
                    break

                # Read fields
                field1 = self._read_field(content, pos, w[0]) if w[0] > 0 else 1
                field2 = self._read_field(content, pos + w[0], w[1])
                field3 = (
                    self._read_field(content, pos + w[0] + w[1], w[2])
                    if w[2] > 0
                    else 0
                )
                pos += entry_size

                if field1 == 0:
                    # Free object
                    pass
                elif field1 == 1:
                    # Uncompressed object: field2 = offset, field3 = gen
                    xref_table[obj_num] = field2
                elif field1 == 2:
                    # Compressed object: field2 = ObjStm number, field3 = index
                    self._compressed_objects[obj_num] = (field2, field3)

        return xref_table, trailer

    def _read_field(self, data: bytes, offset: int, width: int) -> int:
        """Read a big-endian integer field from binary data."""
        if width == 0:
            return 0
        value = 0
        for i in range(width):
            if offset + i < len(data):
                value = (value << 8) | data[offset + i]
        return value

    # ---------------------------------------------------------------------
    # Object Stream Parsing
    # ---------------------------------------------------------------------
    def _parse_object_stream(self, objstm: PdfStream) -> Dict[int, Any]:
        """Parse an Object Stream and extract all contained objects."""
        from .filters import StreamDecoder

        # Get /N (number of objects) and /First (offset of first object data)
        n_obj = objstm.mapping.get(PdfName("N"))
        first_obj = objstm.mapping.get(PdfName("First"))

        if not isinstance(n_obj, PdfNumber) or not isinstance(first_obj, PdfNumber):
            return {}

        n = int(n_obj.value)
        first = int(first_obj.value)

        # Decode stream
        filter_obj = objstm.mapping.get(PdfName("Filter"))
        filter_name = None
        if isinstance(filter_obj, PdfName):
            filter_name = filter_obj.name.lstrip("/")
        decode_parms = objstm.mapping.get(PdfName("DecodeParms"))

        content = objstm.content
        if filter_name:
            content = StreamDecoder.decode(content, filter_name, decode_parms)

        text = content.decode("latin-1")

        # Parse header: N pairs of "obj_num offset"
        tokenizer = _Tokenizer(text)
        pairs = []
        for _ in range(n):
            tokenizer._consume_whitespace()
            obj_num = int(tokenizer._read_number().value)
            tokenizer._consume_whitespace()
            obj_offset = int(tokenizer._read_number().value)
            pairs.append((obj_num, obj_offset))

        # Parse objects; do not drop per-object tokenizer failures silently.
        result: Dict[int, Any] = {}
        for obj_num, obj_offset in pairs:
            abs_offset = first + obj_offset
            if abs_offset < 0 or abs_offset > len(text):
                raise PdfParseException(
                    f"Object stream: object {obj_num} body offset out of range "
                    f"(first={first}, relative={obj_offset}, abs={abs_offset}, "
                    f"decoded_length={len(text)})"
                )
            try:
                obj_tokenizer = _Tokenizer(text[abs_offset:])
                obj = obj_tokenizer.read()
                result[obj_num] = obj
            except (ValueError, IndexError) as e:
                raise PdfParseException(
                    f"Failed to parse object {obj_num} in object stream "
                    f"(stream body offset {abs_offset})"
                ) from e

        return result

    # ---------------------------------------------------------------------
    # Traditional XRef Parsing
    # ---------------------------------------------------------------------
    def _parse_traditional_xref_row(self, pos: int) -> Tuple[int, int, bytes, int]:
        """Parse one xref data row, skipping blank lines and '%' comments.

        Returns ``(byte_offset, generation, flag_first_byte, new_pos)``.
        ``flag_first_byte`` is ``b'n'`` or ``b'f'`` for in-use vs free.
        """
        while True:
            if pos >= len(self._data):
                raise PdfParseException("Incomplete xref entry")
            while pos < len(self._data) and self._data[pos] in b" \t\r\n":
                pos += 1
            if pos >= len(self._data):
                raise PdfParseException("Incomplete xref entry")
            line_start = pos
            while pos < len(self._data) and self._data[pos] not in b"\r\n":
                pos += 1
            raw = self._data[line_start:pos]
            while pos < len(self._data) and self._data[pos] in b"\r\n":
                pos += 1
            line = raw.strip()
            if not line:
                continue
            if line.startswith(b"%"):
                continue
            parts = line.split()
            if len(parts) < 3:
                raise PdfParseException("Invalid xref entry line")
            try:
                offset_val = int(parts[0])
                gen_val = int(parts[1])
            except ValueError as e:
                raise PdfParseException("Invalid xref entry line") from e
            flag_token = parts[2]
            if not flag_token:
                raise PdfParseException("Invalid xref entry line")
            return offset_val, gen_val, flag_token[:1], pos

    def _parse_traditional_xref(
        self, offset: int
    ) -> Tuple[Dict[int, int], PdfDictionary]:
        """Parse a traditional xref table and trailer."""
        pos = offset + 4
        # Skip possible whitespace / newline after 'xref'
        while pos < len(self._data) and self._data[pos] in b" \t\r\n":
            pos += 1
        xref_table: Dict[int, int] = {}
        # Parse sections: start_obj count
        while True:
            while pos < len(self._data) and self._data[pos] in b" \t\r\n":
                pos += 1
            if pos + 7 <= len(self._data) and self._data[pos : pos + 7] == b"trailer":
                break
            # Read "start count"
            m = re.match(rb"(\d+)\s+(\d+)", self._data[pos:])
            if not m:
                raise PdfParseException("Invalid xref subsection header")
            start_obj = int(m.group(1))
            count = int(m.group(2))
            pos += m.end()
            # Skip newline after header
            while pos < len(self._data) and self._data[pos] in b"\r\n":
                pos += 1
            # Read each entry line (spacing between fields may vary)
            for i in range(count):
                offset_val, _gen, flag, pos = self._parse_traditional_xref_row(pos)
                if flag == b"n":
                    xref_table[start_obj + i] = offset_val
        # At this point 'trailer' keyword should be present
        if self._data[pos : pos + 7] != b"trailer":
            raise PdfParseException("trailer keyword not found after xref table")
        pos += len(b"trailer")
        # Skip whitespace before dictionary
        while pos < len(self._data) and self._data[pos] in b" \t\r\n":
            pos += 1
        # Extract the dictionary text (it starts with << and ends with >>)
        dict_start = pos
        dict_bytes = _cos_dictionary_bytes_at(self._data, dict_start)
        if dict_bytes is None:
            raise PdfParseException("Unable to parse trailer dictionary")
        trailer_dict = self._parse_dictionary(dict_bytes)
        return xref_table, trailer_dict

    # ---------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------
    def _find_startxref(self) -> int:
        # Search backwards for the literal "startxref"
        idx = _cos_buffer_rfind(self._data, b"startxref")
        if idx == -1:
            raise PdfParseException("startxref not found in PDF data")
        return idx + len(b"startxref")

    def _read_int_at(self, pos: int) -> int:
        # Skip whitespace and read an integer until newline or whitespace
        while pos < len(self._data) and self._data[pos] in b" \t\r\n":
            pos += 1
        end = pos
        while end < len(self._data) and self._data[end] in b"0123456789":
            end += 1
        return int(self._data[pos:end])

    def _parse_dictionary(self, data: bytes) -> PdfDictionary:
        text = data.decode("latin-1")
        tokenizer = _Tokenizer(text)
        return tokenizer._read_dictionary()

    def _parse_object_at(self, offset: int) -> Any:
        # Find the "obj" keyword after the object number and generation
        obj_header_match = re.match(rb"(\d+)\s+(\d+)\s+obj", self._data[offset:])
        if not obj_header_match:
            raise PdfParseException(f"Object header not found at offset {offset}")
        header_len = obj_header_match.end()
        content_start = offset + header_len
        # Find the position of "endobj"
        end_match = re.search(rb"endobj", self._data[content_start:])
        if not end_match:
            raise PdfParseException("endobj not found for object")
        content_end = content_start + end_match.start()
        raw_content = self._data[content_start:content_end]
        # Decode for tokenizing – use latin-1 to keep raw bytes intact
        text = raw_content.decode("latin-1")
        tokenizer = _Tokenizer(text)
        obj = tokenizer.read()
        # If the object is a dictionary followed by a stream, handle it
        tokenizer._consume_whitespace()
        if isinstance(obj, PdfDictionary) and tokenizer.s.startswith(
            "stream", tokenizer.pos
        ):
            # Consume 'stream' and following EOL
            tokenizer._consume(len("stream"))
            # According to spec, a newline follows the keyword
            if tokenizer._peek() in "\r\n":
                # Consume possible CRLF sequence
                if (
                    tokenizer._peek() == "\r"
                    and tokenizer.s[tokenizer.pos + 1 : tokenizer.pos + 2] == "\n"
                ):
                    tokenizer._consume(2)
                else:
                    tokenizer._consume()
            # Length entry gives byte count
            length_obj = obj.mapping.get(PdfName("Length"))
            if isinstance(length_obj, PdfNumber):
                length = int(length_obj.value)
            else:
                # Fallback – locate endstream within this object only
                length = None
            # Extract stream bytes from original data (preserve raw bytes)
            stream_start = content_start + tokenizer.pos
            stream_bytes = _extract_stream_bytes(
                self._data, stream_start, content_end, length
            )
            # Construct PdfStream object
            stream_obj = PdfStream(content=bytes(stream_bytes), mapping=obj.mapping)
            return stream_obj
        return obj

    # ---------------------------------------------------------------------
    # End of class
    # ---------------------------------------------------------------------


class LazyPdfObjectStore(MutableMapping[int, Any]):
    """Object-number → COS object map that parses from a :class:`PdfCosParser` on demand."""

    def __init__(
        self,
        parser: PdfCosParser,
        xref_offsets: Dict[int, int],
        compressed: Dict[int, Tuple[int, int]],
    ) -> None:
        self._parser = parser
        self._xref_offsets = dict(xref_offsets)
        self._compressed = dict(compressed)
        self._cache: Dict[int, Any] = {}

    @property
    def materialized_count(self) -> int:
        """How many objects have been parsed into memory so far."""
        return len(self._cache)

    def _all_ids(self) -> set[int]:
        return set(self._cache) | set(self._xref_offsets) | set(self._compressed)

    def __bool__(self) -> bool:
        return len(self) > 0

    def __getitem__(self, obj_num: int) -> Any:
        if obj_num in self._cache:
            return self._cache[obj_num]
        if obj_num in self._compressed:
            return self._load_compressed(int(obj_num))
        if obj_num in self._xref_offsets:
            return self._load_uncompressed(int(obj_num))
        raise KeyError(obj_num)

    def _load_uncompressed(self, obj_num: int) -> Any:
        off = self._xref_offsets[obj_num]
        try:
            obj = self._parser._parse_object_at(off)
        except PdfParseException:
            raise
        except (ValueError, IndexError) as e:
            # Tokenizer numeric / bounds failures must not become a bare KeyError
            # Callers need the real defect and object location.
            raise PdfParseException(
                f"Failed to parse PDF object {obj_num} at byte offset {off}"
            ) from e
        self._cache[obj_num] = obj
        return obj

    def _load_compressed(self, obj_num: int) -> Any:
        stm_num, _ = self._compressed[obj_num]
        if stm_num not in self._cache:
            if stm_num not in self._xref_offsets:
                raise KeyError(obj_num)
            self._load_uncompressed(stm_num)
        stm = self._cache[stm_num]
        if not isinstance(stm, PdfStream):
            raise KeyError(obj_num)
        extracted = self._parser._parse_object_stream(stm)
        for k, v in extracted.items():
            self._cache[int(k)] = v
        if obj_num not in self._cache:
            raise KeyError(obj_num)
        return self._cache[obj_num]

    def __setitem__(self, obj_num: int, value: Any) -> None:
        self._cache[int(obj_num)] = value

    def __delitem__(self, obj_num: int) -> None:
        k = int(obj_num)
        self._cache.pop(k, None)
        self._xref_offsets.pop(k, None)
        self._compressed.pop(k, None)

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._all_ids()))

    def __len__(self) -> int:
        return len(self._all_ids())

    def __contains__(self, obj_num: object) -> bool:
        if not isinstance(obj_num, int):
            return False
        return obj_num in self._all_ids()


# PDF COS Parser
