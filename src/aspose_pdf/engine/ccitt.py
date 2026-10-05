"""CCITT Group 3 and Group 4 (ITU-T T.4 / T.6) decoder.

A pure-Python decoder for the ``CCITTFaxDecode`` filter: one-dimensional
Modified Huffman (``K = 0``), two-dimensional Modified READ (``K > 0``) and
pure two-dimensional Modified Modified READ (``K < 0``, Group 4).

A row is carried as its **changing elements** -- the positions where the colour
changes, which is what T.4 is written in terms of -- rather than as a list of
pixels, so ``b1`` and ``b2`` are a lookup rather than a scan and the vertical
and pass modes read the way the standard states them. Internally ``0`` is
white and ``1`` is black, and the first run of every row is a white run; only
``_pack_row`` knows about the PDF ``BlackIs1`` convention.
"""

from __future__ import annotations

from itertools import repeat
from typing import ClassVar

from aspose_pdf.exceptions import PdfParseException, PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

# Import the standard Huffman tables
try:
    from .ccitt_tables import (
        BLACK_MAKEUP,
        BLACK_TERM,
        EXTENDED_MAKEUP,
        WHITE_MAKEUP,
        WHITE_TERM,
    )
except ImportError:
    # Fallback to empty if not found (should not happen in prod)
    WHITE_TERM = {}
    WHITE_MAKEUP = {}
    BLACK_TERM = {}
    BLACK_MAKEUP = {}
    EXTENDED_MAKEUP = {}

#: The longest Huffman code in any of the tables, in bits.
_MAX_CODE_BITS = 14

# The two-dimensional modes of T.4 clause 4.2.1.3 / T.6 clause 2.
_PASS = "P"
_HORIZONTAL = "H"
_EXTENSION = "X"
_EOL = "EOL"


class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0  # 0=MSB .. 7=LSB

    def read_bit(self) -> int:
        if self.byte_pos >= len(self.data):
            raise EOFError
        val = (self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1
        self.bit_pos += 1
        if self.bit_pos == 8:
            self.bit_pos = 0
            self.byte_pos += 1
        return val

    def read_bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | self.read_bit()
        return v

    def peek_bit(self) -> int:
        if self.byte_pos >= len(self.data):
            raise EOFError
        return (self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1

    # --- positioning, for the lookahead an EOL or a fill run needs ----------

    def tell(self) -> int:
        """The read position in bits."""
        return self.byte_pos * 8 + self.bit_pos

    def seek(self, position: int) -> None:
        self.byte_pos, self.bit_pos = divmod(position, 8)

    def align(self) -> None:
        """Advance to the next byte boundary (``/EncodedByteAlign``)."""
        if self.bit_pos:
            self.bit_pos = 0
            self.byte_pos += 1

    @property
    def exhausted(self) -> bool:
        return self.byte_pos >= len(self.data)


class Decoder:
    # (code, bit length) -> run length, per colour, plus the shared extensions
    _WHITE_LOOKUP: ClassVar[dict[tuple[int, int], int]] = {}
    _BLACK_LOOKUP: ClassVar[dict[tuple[int, int], int]] = {}

    @classmethod
    def _init_lookups(cls):
        if cls._WHITE_LOOKUP:
            return

        def build(term, makeup):
            lookup = {}
            # (code, bits) -> length
            for length, (code, bits) in term.items():
                lookup[(code, bits)] = length
            for length, (code, bits) in makeup.items():
                lookup[(code, bits)] = length
            # The 1792-2560 make-up codes are common to both colours.
            for length, (code, bits) in EXTENDED_MAKEUP.items():
                lookup[(code, bits)] = length
            return lookup

        cls._WHITE_LOOKUP = build(WHITE_TERM, WHITE_MAKEUP)
        cls._BLACK_LOOKUP = build(BLACK_TERM, BLACK_MAKEUP)

    @staticmethod
    def _read_run_length(reader, color):
        """One run length: make-up codes accumulate, a terminating code ends it."""
        length = 0
        while True:
            code = 0
            bits = 0
            lut = Decoder._WHITE_LOOKUP if color == 0 else Decoder._BLACK_LOOKUP
            while bits < _MAX_CODE_BITS:
                code = (code << 1) | reader.read_bit()
                bits += 1
                run = lut.get((code, bits))
                if run is None:
                    continue
                length += run
                if run < 64:
                    return length
                break  # a make-up code: the same colour continues
            else:
                raise PdfParseException("Invalid Huffman Code")

    @staticmethod
    def _read_mode(reader):
        """The next two-dimensional mode code.

        Returns ``_PASS``, ``_HORIZONTAL``, ``_EXTENSION``, ``_EOL``, or the
        vertical offset of ``a1`` from ``b1`` as an ``int`` in ``-3..3``.
        """
        if reader.read_bit():
            return 0  # V0 = 1
        if reader.read_bit():  # 01
            return 1 if reader.read_bit() else -1  # VR1 = 011, VL1 = 010
        if reader.read_bit():  # 001
            return _HORIZONTAL
        if reader.read_bit():  # 0001
            return _PASS
        if reader.read_bit():  # 00001
            return 2 if reader.read_bit() else -2  # VR2, VL2
        if reader.read_bit():  # 000001
            return 3 if reader.read_bit() else -3  # VR3, VL3
        if reader.read_bit():  # 0000001
            return _EXTENSION
        return _EOL  # 00000000 ... an EOL, fill bits, or the end of the block

    @staticmethod
    def _b1_b2(ref, a0, color, cols):
        """``b1`` and ``b2`` on the reference line (T.4 clause 4.2.1.3.1).

        ``ref`` holds the reference line's changing elements in order, so an
        element at an even index starts a black run and one at an odd index
        starts a white run. ``b1`` is the first changing element to the right of
        ``a0`` whose colour is opposite to ``color`` -- that is, the first one
        past ``a0`` whose index has the same parity as ``color``.
        """
        index = 0
        total = len(ref)
        while index < total and ref[index] <= a0:
            index += 1
        if (index & 1) != color:
            index += 1
        b1 = ref[index] if index < total else cols
        b2 = ref[index + 1] if index + 1 < total else cols
        return b1, b2

    @staticmethod
    def _decode_row_1d(reader, cols):
        """A Modified Huffman row, as its changing elements."""
        changes = []
        position = 0
        color = 0
        while position < cols:
            run = Decoder._read_run_length(reader, color)
            position += run
            changes.append(min(position, cols))
            color ^= 1
        return changes

    @staticmethod
    def _decode_row_2d(reader, ref, cols):
        """A Modified READ row: ``(changing elements, ended on an EOL)``.

        The second value says the row stopped at a run of at least seven zero
        bits -- an EOL, the fill before one, or corruption. A row that ends that
        way with no changing element at all is not a row: nothing was coded for
        it, and the caller stops rather than inventing a white one.
        """
        changes = []
        # a0 starts on an imaginary changing element just before the row, so
        # that a changing element at column 0 counts as being to its right.
        a0 = -1
        color = 0
        while a0 < cols:
            b1, b2 = Decoder._b1_b2(ref, a0, color, cols)
            mode = Decoder._read_mode(reader)
            if mode is _PASS:
                # a0 moves to b2 without a changing element of its own, so the
                # run of `color` simply continues past b1 and b2.
                a0 = b2
                continue
            if mode is _HORIZONTAL:
                start = a0 if a0 > 0 else 0
                first = Decoder._read_run_length(reader, color)
                second = Decoder._read_run_length(reader, 1 - color)
                a1 = min(start + first, cols)
                a2 = min(a1 + second, cols)
                changes.append(a1)
                changes.append(a2)
                a0 = a2
                continue
            if mode is _EXTENSION:
                raise PdfParseException(
                    "Unsupported CCITT extension code in a two-dimensional row"
                )
            if mode is _EOL:
                # An end-of-line (or the end of the data) closes the row.
                return changes, True
            a1 = b1 + mode
            if a1 < 0:
                a1 = 0
            elif a1 > cols:
                a1 = cols
            changes.append(a1)
            a0 = a1
            color ^= 1
        return changes, False

    @staticmethod
    def _expand(changes, cols):
        """Changing elements back to one byte per pixel (``1`` = black)."""
        row = bytearray(cols)
        position = 0
        color = 0
        for change in changes:
            change = min(max(change, position), cols)
            if color and change > position:
                row[position:change] = b"\x01" * (change - position)
            position = change
            color ^= 1
        if color and position < cols:
            row[position:cols] = b"\x01" * (cols - position)
        return row

    @staticmethod
    def _skip_eol(reader):
        """Consume one EOL and any fill bits before it; say whether one was there.

        ``/EndOfLine`` is optional in PDF and encoders disagree about it, so an
        EOL is skipped wherever it appears rather than required anywhere.
        """
        start = reader.tell()
        zeros = 0
        try:
            while True:
                if reader.read_bit():
                    if zeros >= 11:
                        return True
                    reader.seek(start)
                    return False
                zeros += 1
                if zeros > 64:
                    reader.seek(start)
                    return False
        except EOFError:
            reader.seek(start)
            return False

    @staticmethod
    def decode(
        data: bytes,
        params: dict,
        limits: PdfLoadLimits | None = None,
    ) -> bytes:
        Decoder._init_lookups()
        limits = _coerce_limits(limits)
        budget = _LoadBudget(limits)
        cols = int(params.get("Columns", 1728))
        rows = int(params.get("Rows", 0))
        k = int(params.get("K", 0))
        black_is_1 = bool(params.get("BlackIs1", False))
        byte_align = bool(params.get("EncodedByteAlign", False))

        if cols <= 0:
            raise PdfResourceLimitException(
                f"Invalid CCITT Columns value: {cols}; expected a positive integer"
            )
        if rows < 0:
            raise PdfResourceLimitException(
                f"Invalid CCITT Rows value: {rows}; expected a non-negative integer"
            )

        # Even when Rows is omitted, one reference row is allocated immediately.
        budget.check_image_pixels(cols, max(rows, 1), "CCITT image")
        bytes_per_row = (cols + 7) // 8
        declared_output_size = bytes_per_row * max(rows, 1)
        budget.check(
            declared_output_size,
            "max_decoded_stream_bytes",
            "CCITT decoded bitmap bytes",
        )
        budget.check(
            (2 * cols) + declared_output_size,
            "max_codec_work_bytes",
            "CCITT decoder working set",
        )

        reader = _BitReader(data)
        ref: list[int] = []
        output = bytearray()
        decoded_rows = 0

        while (rows == 0 or decoded_rows < rows) and not reader.exhausted:
            next_row_count = decoded_rows + 1
            budget.check_image_pixels(cols, next_row_count, "CCITT image")
            budget.check(
                bytes_per_row * next_row_count,
                "max_decoded_stream_bytes",
                "CCITT decoded bitmap bytes",
            )
            budget.check(
                (2 * cols) + (bytes_per_row * next_row_count),
                "max_codec_work_bytes",
                "CCITT decoder working set",
            )

            if byte_align and k >= 0:
                reader.align()

            # An EOL may precede any row; after one, a mixed-mode stream says
            # with a single bit whether the row that follows is 1-D or 2-D.
            saw_eol = Decoder._skip_eol(reader)
            while saw_eol and Decoder._skip_eol(reader):
                # Several EOLs in a row are the RTC/EOFB that ends the block.
                reader.seek(len(data) * 8)
                break
            if reader.exhausted:
                break

            if k < 0:
                two_dimensional = True
            elif k == 0:
                two_dimensional = False
            else:
                try:
                    two_dimensional = not reader.read_bit()
                except EOFError:
                    break

            if byte_align and k < 0:
                reader.align()
                if reader.exhausted:
                    break

            try:
                if two_dimensional:
                    changes, ended_on_eol = Decoder._decode_row_2d(reader, ref, cols)
                    if ended_on_eol and not changes:
                        break
                else:
                    changes = Decoder._decode_row_1d(reader, cols)
            except EOFError:
                break

            row = Decoder._expand(changes, cols)
            output.extend(Decoder._pack_row(row, cols, black_is_1))
            ref = changes
            decoded_rows += 1

        # Nothing at all came out of a non-empty stream: it is not CCITT data
        # this decoder can follow, and the caller says so rather than painting
        # a blank rectangle over whatever the picture was.
        if data and not decoded_rows:
            return b""

        # A short stream leaves the rest of a declared bitmap white rather than
        # handing back a bitmap of the wrong size for its /Height. Padding
        # completes a partial decode; it does not invent one from no rows.
        if rows and 0 < decoded_rows < rows:
            blank = Decoder._pack_row(bytearray(cols), cols, black_is_1)
            output.extend(blank * (rows - decoded_rows))

        return bytes(output)

    @staticmethod
    def _append_run(line, length, color, max_length=None):
        if length < 0:
            raise PdfParseException("Invalid negative CCITT run length")
        if max_length is not None and len(line) + length > max_length:
            raise PdfParseException("CCITT run exceeds the declared row width")
        if line is not None and length > 0:
            line.extend(repeat(color, length))

    @staticmethod
    def _pack_row(row, cols, black_is_1):
        bytes_per_row = (cols + 7) // 8
        packed = bytearray(bytes_per_row)
        row_length = len(row)
        for i in range(cols):
            pixel = row[i] if i < row_length else 0
            val = pixel if black_is_1 else (1 - pixel)
            if val:
                packed[i // 8] |= 1 << (7 - (i % 8))
        return packed


def decode_group4(
    data: bytes,
    width: int,
    height: int,
    *,
    black_is_1: bool = False,
    limits: PdfLoadLimits | None = None,
) -> bytes:
    """Decode CCITT Group-4 (T.6) compressed image data.

    This convenience wrapper constructs the parameter dictionary expected by
    :pymeth:`Decoder.decode` and forces ``K`` to ``-1`` (Group-4).  The optional
    ``black_is_1`` flag mirrors the PDF ``BlackIs1`` entry - when ``False`` the
    output follows the PDF convention where ``0`` is black and ``1`` is white.

    Args:
        data:   The raw CCITT-encoded byte stream.
        width:  Number of pixels per row.
        height: Number of rows.
        black_is_1: If ``True`` the output uses ``1`` for black pixels.
        limits: Optional resource limits for untrusted image data.

    Returns:
        A ``bytes`` object containing the decoded 1-bit bitmap.
    """
    params = {
        "Columns": width,
        "Rows": height,
        "K": -1,
        "BlackIs1": black_is_1,
    }
    return Decoder.decode(data, params, limits=limits)
