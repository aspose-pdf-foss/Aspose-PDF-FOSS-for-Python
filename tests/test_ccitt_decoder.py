"""Tests for the CCITT Group 3 and Group 4 decoder implementation.

``K >= 0`` (Modified Huffman, and mixed 1-D/2-D) used to take a fast path that
returned the input unchanged -- it handed the *encoded* bytes back as if they
were a bitmap. It is decoded now, so these tests check that a one-dimensional
row round-trips, that the code tables are prefix-free (which is what lets a code
be read one bit at a time), and that data the decoder cannot follow is reported
rather than passed through.
"""

import pytest

from aspose_pdf.engine.ccitt import Decoder, decode_group4
from aspose_pdf.engine.ccitt_tables import (
    BLACK_MAKEUP,
    BLACK_TERM,
    EXTENDED_MAKEUP,
    WHITE_MAKEUP,
    WHITE_TERM,
)
from aspose_pdf.exceptions import PdfParseException


def _encode_1d(runs, colors):
    """Pack ``(run, colour)`` pairs as a Modified Huffman bitstream."""
    bits = ""
    for run, color in zip(runs, colors):
        terminating = WHITE_TERM if color == 0 else BLACK_TERM
        makeups = WHITE_MAKEUP if color == 0 else BLACK_MAKEUP
        remaining = run
        while remaining >= 64:
            makeup = max(length for length in makeups if length <= remaining)
            code, bits_count = makeups[makeup]
            bits += format(code, f"0{bits_count}b")
            remaining -= makeup
        code, bits_count = terminating[remaining]
        bits += format(code, f"0{bits_count}b")
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def test_one_dimensional_rows_are_decoded():
    # Three white, two black, three white. With BlackIs1 the ink is where the
    # bits are set, so columns 3 and 4 of the eight.
    data = _encode_1d([3, 2, 3], [0, 1, 0])
    out = Decoder.decode(data, {"K": 0, "Columns": 8, "Rows": 1, "BlackIs1": True})
    assert out == bytes([0b00011000])


def test_one_dimensional_run_longer_than_a_terminating_code():
    # 100 black pixels needs a make-up code (64) plus a terminating one (36).
    data = _encode_1d([0, 100, 28], [0, 1, 0])
    out = Decoder.decode(data, {"K": 0, "Columns": 128, "Rows": 1, "BlackIs1": True})
    assert out == bytes([0xFF] * 12) + bytes([0xF0]) + bytes([0x00] * 3)


def test_black_is_1_false_inverts_the_row():
    data = _encode_1d([3, 2, 3], [0, 1, 0])
    out = Decoder.decode(data, {"K": 0, "Columns": 8, "Rows": 1, "BlackIs1": False})
    assert out == bytes([0b11100111])


def test_code_tables_are_prefix_free_within_a_colour():
    # A code that prefixes another makes a one-bit-at-a-time read stop early,
    # which is how the black table's run 8 used to be read as run 2.
    for terminating, makeups in ((WHITE_TERM, WHITE_MAKEUP), (BLACK_TERM, BLACK_MAKEUP)):
        codes: dict[tuple[int, int], int] = {}
        for table in (terminating, makeups, EXTENDED_MAKEUP):
            for run, (code, bits) in table.items():
                assert (code, bits) not in codes, f"duplicate code for run {run}"
                codes[(code, bits)] = run
        for code, bits in codes:
            for other, other_bits in codes:
                if other_bits > bits:
                    assert (other >> (other_bits - bits)) != code, (
                        f"{code:0{bits}b} prefixes {other:0{other_bits}b}"
                    )


def test_every_run_length_has_a_code():
    assert sorted(WHITE_TERM) == sorted(BLACK_TERM) == list(range(64))
    makeups = [64 * n for n in range(1, 28)]
    assert sorted(WHITE_MAKEUP) == sorted(BLACK_MAKEUP) == makeups
    assert sorted(EXTENDED_MAKEUP) == [1792 + 64 * n for n in range(13)]


def test_undecodable_data_is_reported_not_returned():
    # Bits matching no code: the caller must not get them back as samples.
    with pytest.raises(PdfParseException, match="Invalid Huffman Code"):
        Decoder.decode(b"\x00\x00\x00\x00", {"K": 0, "Columns": 64, "Rows": 1})


def test_decode_malformed_data_falls_back_to_original():
    """When the bitstream cannot be decoded the implementation catches the
    exception and returns the original data.
    """
    # An empty byte string will cause the BitReader to raise EOFError immediately.
    raw = b""
    params = {"K": -1, "Columns": 8, "Rows": 1}
    result = Decoder.decode(raw, params)
    assert result == raw


def test_pack_row_inverts_when_black_is_1_false():
    """When ``black_is_1`` is ``False`` the packer inverts the pixel values.

    An all-white row (internal value ``0``) should become a byte of ``0xFF``
    because the PDF convention treats ``1`` as white when ``BlackIs1`` is
    ``False``. This is the packer the decoder itself calls for every row.
    """
    assert Decoder._pack_row([0] * 8, 8, black_is_1=False) == bytes([0xFF])


def test_pack_row_no_invert_when_black_is_1_true():
    """When ``black_is_1`` is ``True`` the output follows the internal pixel
    representation directly (``0`` -> white, ``1`` -> black).
    """
    assert Decoder._pack_row([0] * 8, 8, black_is_1=True) == bytes([0x00])


def test_decode_group4_empty_returns_empty():
    """The convenience wrapper should return an empty ``bytes`` object when the
    input stream is empty.
    """
    assert decode_group4(b"", 8, 0, black_is_1=False) == b""
