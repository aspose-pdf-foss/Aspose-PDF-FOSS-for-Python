"""Tests for the CCITT Group 4 decoder implementation.

The decoder has a fast‑path that returns the raw input data unchanged when the
parameter ``K`` is non‑negative.  The test suite primarily verifies this fast‑
path and also checks that the decoder gracefully falls back to the original
data when it encounters malformed input.
"""

from aspose_pdf.engine.ccitt import Decoder
from aspose_pdf.engine.ccitt import decode_group4


def test_decode_returns_input_when_k_is_non_negative():
    """When ``K`` is >= 0 the decoder should return the original byte string.

    The PDF specification uses a positive ``K`` value for CCITT 1D and a
    negative value for Group 4.  The implementation short‑circuits the decode
    path for non‑negative ``K``.
    """
    raw = b"\xaa\x55\xff"
    params = {"K": 0, "Columns": 8, "Rows": 1}
    result = Decoder.decode(raw, params)
    assert result is raw or result == raw


def test_decode_defaults_to_k_zero_and_returns_input():
    """If ``K`` is omitted the default is ``0`` which triggers the fast‑path."""
    raw = b"\x00\x01\x02"
    params = {"Columns": 8, "Rows": 1}
    assert Decoder.decode(raw, params) == raw


def test_decode_malformed_data_falls_back_to_original():
    """When the bitstream cannot be decoded the implementation catches the
    exception and returns the original data.
    """
    # An empty byte string will cause the BitReader to raise EOFError immediately.
    raw = b""
    params = {"K": -1, "Columns": 8, "Rows": 1}
    result = Decoder.decode(raw, params)
    assert result == raw


def test_pack_rows_inverts_when_black_is_1_false():
    """When ``black_is_1`` is ``False`` the packer inverts the pixel values.

    An all‑white row (internal value ``0``) should become a byte of ``0xFF``
    because the PDF convention treats ``1`` as white when ``BlackIs1`` is
    ``False``.
    """
    rows = [[0] * 8]
    result = Decoder._pack_rows(rows, 8, black_is_1=False)
    assert result == bytes([0xFF])


def test_pack_rows_no_invert_when_black_is_1_true():
    """When ``black_is_1`` is ``True`` the output follows the internal pixel
    representation directly (``0`` -> white, ``1`` -> black).
    """
    rows = [[0] * 8]
    result = Decoder._pack_rows(rows, 8, black_is_1=True)
    assert result == bytes([0x00])


def test_decode_group4_empty_returns_empty():
    """The convenience wrapper should return an empty ``bytes`` object when the
    input stream is empty.
    """
    assert decode_group4(b"", 8, 0, black_is_1=False) == b""
