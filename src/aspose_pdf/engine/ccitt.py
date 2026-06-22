"""CCITT Group 4 (T.6) decoder implementation.

This module provides a pure Python decoder for CCITT Group 4 compressed image data.
"""

from __future__ import annotations

from aspose_pdf.exceptions import PdfParseException

# Import the standard Huffman tables
try:
    from .ccitt_tables import WHITE_TERM, WHITE_MAKEUP, BLACK_TERM, BLACK_MAKEUP
except ImportError:
    # Fallback to empty if not found (should not happen in prod)
    WHITE_TERM = {}
    WHITE_MAKEUP = {}
    BLACK_TERM = {}
    BLACK_MAKEUP = {}


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


class Decoder:
    # Pre-computed lookup tables for Huffman codes (code, bits) -> length
    _WHITE_LOOKUP = {}
    _BLACK_LOOKUP = {}

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
            return lookup

        cls._WHITE_LOOKUP = build(WHITE_TERM, WHITE_MAKEUP)
        cls._BLACK_LOOKUP = build(BLACK_TERM, BLACK_MAKEUP)

    @staticmethod
    def _read_run_length(reader, color):
        # Read Huffman
        length = 0
        while True:
            # Simple bit-by-bit reading is slow but robust for 1-shot
            # We traverse the keys.
            # Optimization: Try matching common lengths (2-13 bits).
            # Here we iterate.
            _found = False
            code = 0
            bits = 0

            # Read bits one at a time, matching the accumulated (code, bits)
            # against the run-length lookup table (codes are at most 13 bits).
            while bits < 13:  # Max code length is 13
                code = (code << 1) | reader.read_bit()
                bits += 1

                lut = Decoder._WHITE_LOOKUP if color == 0 else Decoder._BLACK_LOOKUP
                if (code, bits) in lut:
                    run_len = lut[(code, bits)]
                    length += run_len
                    if run_len < 64:  # Terminating
                        return length
                    # Else Makeup, continue loop
                    code = 0
                    bits = 0
                    # Makeup code: keep the same color and continue decoding.
                    # White and black runs use separate generated tables.
                    break
            else:
                # Failed to match in max bits
                raise PdfParseException("Invalid Huffman Code")

            if bits > 0:  # Loop continued due to Makeup break
                continue

    @staticmethod
    def _full_decode_row(reader, cur_line, ref_line, cols):
        # We need to know current color state.
        # Start of line: White (0).
        current_color = 0

        while len(cur_line) < cols:
            # Modes.

            # V0 (1)
            if reader.peek_bit() == 1:
                reader.read_bit()
                # Find b1 (change on Ref)
                # b1 search starts from 'current position' + relative offset?
                # "b1 is first changing element on ref line to right of a0 and of opposite color to a0"
                # a0 is our current pos. Color of a0 is `current_color`?
                # No, a0 is transition.
                # Color BEFORE a0 was `1-current_color`.
                # Color AT a0 is `current_color`.
                # Wait. `current_color` is the color we are WRITING.
                # So previous was opposite.
                # So we look for change from `1-current_color`?
                # No. Ref line and Cur line are synced at a0.
                # We look for b1 on Ref.
                # b1 must be opposite color to a0.
                # a0 has `current_color`.
                # So b1 must be `1-current_color`.
                # So finding b1: look for `(1-current_color)`.
                # But wait. Ref line might not have a0 as transition.
                # V0 means "transition at same place".
                # So Ref line DOES have transition at same place? Not necessarily.
                # V0 means "distance a1-a0 = b1-a0".
                # "First changing element on ref line" -> b1.

                # Correct Logic:
                # a0_pos = len(cur_line).
                # b1_pos = Decoder._find_b1(ref_line, a0_pos, current_color) ... Wait.
                # _find_b1 finds first pixel != color.
                # Current color at a0 is `current_color`.
                # So we want first pixel != current_color.
                # Yes.

                b1_pos = Decoder._find_b1(ref_line, len(cur_line), current_color)
                run_len = b1_pos - len(cur_line)
                Decoder._append_run(cur_line, run_len, current_color)

                # After V0, we have handled a run of `current_color`.
                # Next run will be `opposite`.
                current_color = 1 - current_color
                continue

            # Check 0...
            reader.read_bit()

            # 01...
            if reader.peek_bit() == 1:
                reader.read_bit()
                # 01...
                if reader.read_bit() == 1:
                    # 011 -> VR1
                    # a1 = b1 + 1
                    b1 = Decoder._find_b1(ref_line, len(cur_line), current_color)
                    len_run = (b1 + 1) - len(cur_line)
                    Decoder._append_run(cur_line, len_run, current_color)
                    current_color = 1 - current_color
                else:
                    # 010 -> VL1
                    # a1 = b1 - 1
                    b1 = Decoder._find_b1(ref_line, len(cur_line), current_color)
                    len_run = (b1 - 1) - len(cur_line)
                    Decoder._append_run(cur_line, len_run, current_color)
                    current_color = 1 - current_color
                continue

            # 00...
            reader.read_bit()
            if reader.read_bit() == 1:
                # 001 -> Horizontal
                # "Run length a0a1... Run length a1a2".
                # First run is `current_color`.
                len1 = Decoder._read_run_length(reader, current_color)
                Decoder._append_run(cur_line, len1, current_color)
                current_color = 1 - current_color

                # Second run is `new_current_color` (which is 1-old).
                len2 = Decoder._read_run_length(reader, current_color)
                Decoder._append_run(cur_line, len2, current_color)
                current_color = 1 - current_color
                continue

            # 000...
            # Check 4th bit
            if reader.read_bit() == 1:
                # 0001 -> Pass
                # Find b2.
                # b1 = find_b1(len, current).
                # b2 = find_b1(b1, 1-current).
                b2 = Decoder._find_b2(ref_line, len(cur_line), current_color)

                len_run = b2 - len(cur_line)
                Decoder._append_run(cur_line, len_run, current_color)

                # Pass mode does NOT switch color!
                # "Start new coding ... with a0 set at b2".
                # This moves a0.
                # But does color Switch?
                # No. We passed a transition on Ref, but we haven't placed a transition on Cur.
                # So we continue with `current_color`.
                continue

            # 0000... Extensions
            raise PdfParseException("Unsupported extension")

    @staticmethod
    def _append_run(line, length, color):
        if length > 0:
            line.extend([color] * length)

    @staticmethod
    def decode(data, params):
        Decoder._init_lookups()
        cols = int(params.get("Columns", 1728))
        rows = int(params.get("Rows", 0))
        k = int(params.get("K", 0))
        black_is_1 = bool(params.get("BlackIs1", False))

        if k >= 0:
            return data

        reader = _BitReader(data)
        ref_line = [0] * cols
        output_rows = []

        curr_idx = 0
        while (rows == 0 or curr_idx < rows) and reader.byte_pos < len(data):
            cur_line = []
            Decoder._full_decode_row(reader, cur_line, ref_line, cols)
            # Pad/Truncate
            if len(cur_line) > cols:
                cur_line = cur_line[:cols]
            elif len(cur_line) < cols:
                cur_line.extend([0] * (cols - len(cur_line)))

            output_rows.append(cur_line)
            ref_line = cur_line
            curr_idx += 1

        return Decoder._pack_rows(output_rows, cols, black_is_1)

    @staticmethod
    def _find_b1(ref_line, a0, color):
        for i in range(a0, len(ref_line)):
            if ref_line[i] != color:
                return i
        return len(ref_line)

    @staticmethod
    def _find_b2(ref_line, a0, color):
        # b2 is first changing element after b1
        b1 = Decoder._find_b1(ref_line, a0, color)
        color_b1 = 1 - color
        return Decoder._find_b1(ref_line, b1, color_b1)

    @staticmethod
    def _pack_rows(rows, cols, black_is_1):
        res = bytearray()
        # Ensure byte alignment handling if needed, but standard PDF G4 is byte-aligned per row?
        # No, PDF G4 usually treats data as continuous stream, but Rows are usually byte-padded?
        # G4 itself doesn't pad rows in the stream.
        # But the output bitmap (device) usually expects byte-aligned rows.
        # Aspose `StreamDecoder` returns bytes.
        # Standard: 1-bit-per-pixel, MSB first, 0=Black? (Depends on BlackIs1).
        # Params `black_is_1`: If True, 1=Black. If False, 0=Black.
        # Our decoder produces 0/1. We used 0=White, 1=Black internally.
        # If black_is_1 is False (default): White=1, Black=0.
        # So we need to invert if black_is_1 is False.
        # Invert: pixel = 1 - pixel.

        bytes_per_row = (cols + 7) // 8

        for row in rows:
            # Pad row to byte boundary if short (or just cols)
            # Row length should be `cols`.
            # If short, pad with White? (0).
            if len(row) < cols:
                row.extend([0] * (cols - len(row)))

            r_bytes = bytearray(bytes_per_row)
            for i, pixel in enumerate(row):
                if i >= len(row):
                    break  # Should not happen

                # Internal: 1=Black, 0=White.
                # Target:
                # If BlackIs1=True: 1=Black (match).
                # If BlackIs1=False: 0=Black (invert).

                val = pixel if black_is_1 else (1 - pixel)

                if val:
                    byte_idx = i // 8
                    bit_shift = 7 - (i % 8)
                    r_bytes[byte_idx] |= 1 << bit_shift

            res.extend(r_bytes)

        return bytes(res)


def decode_group4(
    data: bytes, width: int, height: int, *, black_is_1: bool = False
) -> bytes:
    """Decode CCITT Group‑4 (T.6) compressed image data.

    This convenience wrapper constructs the parameter dictionary expected by
    :pymeth:`Decoder.decode` and forces ``K`` to ``-1`` (Group‑4).  The optional
    ``black_is_1`` flag mirrors the PDF ``BlackIs1`` entry – when ``False`` the
    output follows the PDF convention where ``0`` is black and ``1`` is white.

    Args:
        data:   The raw CCITT‑encoded byte stream.
        width:  Number of pixels per row.
        height: Number of rows.
        black_is_1: If ``True`` the output uses ``1`` for black pixels.

    Returns:
        A ``bytes`` object containing the decoded 1‑bit bitmap.
    """
    params = {
        "Columns": width,
        "Rows": height,
        "K": -1,
        "BlackIs1": black_is_1,
    }
    return Decoder.decode(data, params)
