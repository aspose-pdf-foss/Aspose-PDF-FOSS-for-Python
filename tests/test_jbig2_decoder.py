import struct
import warnings

from aspose_pdf.engine.jbig2 import Decoder


def _make_jbig2_stream(seg_type: int, seg_data: bytes, header: bool = True) -> bytes:
    """Utility to construct a minimal JBIG2 byte stream."""
    stream = b""
    if header:
        stream += b"\x97JBIG2\x00\r"
    # segment number (4 bytes, zero)
    stream += b"\x00\x00\x00\x00"
    stream += bytes([0, seg_type])  # flags, seg_type
    stream += struct.pack(">I", len(seg_data))
    stream += seg_data
    return stream


def test_decode_header_parsing():
    """Decoder should correctly handle a stream with only the JBIG2 file header and no segments."""
    # Stream consists only of the JBIG2 magic header.
    stream = b"\x97JBIG2\x00\r"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = Decoder.decode(stream)
        assert result == b""
        # Ensure a warning about missing Immediate Generic Region is emitted.
        assert any("Immediate Generic Region" in str(item.message) for item in w)


def test_decode_multiple_segments_selects_first_generic_region(monkeypatch):
    """When multiple segments exist, the first Immediate Generic Region should be decoded."""
    # Mock the CCITT decoder to verify it is called for the first generic region.
    called = {}

    def mock_decode(payload, width, height):
        called["used"] = True
        return b"first"

    monkeypatch.setattr(
        "aspose_pdf.engine.jbig2.decode_group4",
        mock_decode,
        raising=False,
    )
    # First segment: generic region with MMR flag.
    region_info = bytearray(13)
    region_info[0] = 0x01
    region_info[5:9] = struct.pack(">I", 2)
    region_info[9:13] = struct.pack(">I", 2)
    seg1 = _make_jbig2_stream(seg_type=0x08, seg_data=bytes(region_info))
    # Second segment: another generic region without MMR.
    payload = b"\xff"
    region_info2 = bytearray(13)
    region_info2[0] = 0x00
    seg2 = _make_jbig2_stream(seg_type=0x08, seg_data=bytes(region_info2) + payload)
    combined = seg1 + seg2
    result = Decoder.decode(combined)
    assert result == b"first"
    assert called.get("used") is True


def test_decode_generic_region_with_mmr(monkeypatch):
    """When the MMR flag is set, Decoder should delegate to `decode_group4`."""
    # Mock the CCITT Group-4 decoder.
    monkeypatch.setattr(
        "aspose_pdf.engine.jbig2.decode_group4",
        lambda payload, width, height: b"decoded",
        raising=False,
    )

    # Build region info: first byte MMR flag set, width=1, height=1.
    region_info = bytearray(13)
    region_info[0] = 0x01  # MMR flag
    region_info[5:9] = struct.pack(">I", 1)  # width
    region_info[9:13] = struct.pack(">I", 1)  # height
    seg_data = bytes(region_info)  # no payload after header
    stream = _make_jbig2_stream(seg_type=0x08, seg_data=seg_data)

    result = Decoder.decode(stream)
    assert result == b"decoded"


def test_decode_generic_region_without_mmr():
    """When the MMR flag is not set, the raw bitmap payload is returned."""
    payload = b"\xaa\xbb\xcc"
    region_info = bytearray(13)
    region_info[0] = 0x00  # MMR flag cleared
    seg_data = bytes(region_info) + payload
    stream = _make_jbig2_stream(seg_type=0x08, seg_data=seg_data)

    result = Decoder.decode(stream)
    # The decoder returns the payload (after 13 bytes info)
    assert result == payload


def test_decode_no_generic_region():
    """If the stream contains no Immediate Generic Region segment, an empty bytes object is returned."""
    # Create a segment of a different type (e.g., 0x01).
    stream = _make_jbig2_stream(seg_type=0x01, seg_data=b"ignored")

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = Decoder.decode(stream)
        assert result == b""
        # We verify warning is emitted
        assert any("Immediate Generic Region" in str(item.message) for item in w)


def test_decode_malformed_segment_returns_empty():
    """A segment that ends prematurely should not raise but return empty."""
    # Truncate the segment length field so the parser cannot read the full payload.
    malformed = (
        b"\x97JBIG2\x00\r" + b"\x00\x00\x00\x00" + bytes([0, 0x08]) + b"\x00\x00"
    )
    result = Decoder.decode(malformed)
    assert result == b""
