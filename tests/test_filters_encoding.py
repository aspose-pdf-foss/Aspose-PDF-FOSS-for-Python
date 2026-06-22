"""Round-trip tests for StreamEncoder (inverse of StreamDecoder).

For every supported filter (and filter chain), encoding then decoding must
return the original bytes.
"""

import random

import pytest

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.engine.filters import StreamDecoder, StreamEncoder


def _roundtrip(data, filters, parms=None):
    encoded = StreamEncoder.encode(data, filters, parms)
    return StreamDecoder.decode(encoded, filters, parms)


# Representative payloads: empty, tiny, text, highly repetitive, and pseudo
# random (incompressible). The random one exercises LZW dictionary growth and
# the 4096-entry reset, plus RunLength's literal path.
_PAYLOADS = [
    b"",
    b"A",
    b"Hello PDF World!",
    b"ab" * 5000,
    b"\x00" * 4096,
    bytes((i * 7 + 3) % 256 for i in range(9000)),
    bytes(random.Random(1234).randrange(256) for _ in range(20000)),
]


@pytest.mark.parametrize("data", _PAYLOADS)
@pytest.mark.parametrize(
    "filt",
    [
        "FlateDecode",
        "LZWDecode",
        "ASCII85Decode",
        "ASCIIHexDecode",
        "RunLengthDecode",
    ],
)
def test_single_filter_roundtrip(filt, data):
    assert _roundtrip(data, filt) == data


@pytest.mark.parametrize("data", _PAYLOADS)
@pytest.mark.parametrize("early_change", [0, 1])
def test_lzw_early_change_roundtrip(data, early_change):
    parms = {"EarlyChange": early_change}
    assert _roundtrip(data, "LZWDecode", parms) == data


@pytest.mark.parametrize("data", _PAYLOADS)
def test_filter_chain_roundtrip(data):
    # Decode order is ASCIIHex then Flate, so encode runs Flate then ASCIIHex.
    filters = ["ASCIIHexDecode", "FlateDecode"]
    encoded = StreamEncoder.encode(data, filters)
    # The outer (first) filter is ASCIIHex, so the encoded bytes are hex text.
    assert encoded.endswith(b">")
    assert StreamDecoder.decode(encoded, filters, None) == data


@pytest.mark.parametrize("data", _PAYLOADS)
def test_triple_chain_roundtrip(data):
    filters = ["ASCII85Decode", "LZWDecode", "RunLengthDecode"]
    assert _roundtrip(data, filters) == data


def test_ascii85_canonical_form():
    enc = StreamEncoder.encode(b"Hello World!", "ASCII85Decode")
    assert enc.startswith(b"<~") and enc.endswith(b"~>")


def test_asciihex_uppercase_terminated():
    enc = StreamEncoder.encode(b"\x01\xab\xcd", "ASCIIHexDecode")
    assert enc == b"01ABCD>"


def test_runlength_repeat_is_compact():
    enc = StreamEncoder.encode(b"X" * 100, "RunLengthDecode")
    # 100 = 78 + 22 -> two repeat runs (128 max each) + EOD, far smaller than 100.
    assert len(enc) < 10
    assert StreamDecoder.decode(enc, "RunLengthDecode", None) == b"X" * 100


def test_flate_with_predictor_rejected():
    with pytest.raises(PdfValidationException, match="predictor"):
        StreamEncoder.encode(b"data", "FlateDecode", {"Predictor": 12, "Columns": 2})


def test_image_codecs_not_encodable():
    for filt in ("DCTDecode", "CCITTFaxDecode", "JBIG2Decode", "JPXDecode"):
        with pytest.raises(PdfValidationException, match="not supported"):
            StreamEncoder.encode(b"\x00\x01\x02", filt)


def test_crypt_not_encodable():
    with pytest.raises(PdfValidationException, match="Crypt"):
        StreamEncoder.encode(b"data", "Crypt")


def test_unknown_filter_rejected():
    with pytest.raises(PdfValidationException, match="Unsupported or unknown"):
        StreamEncoder.encode(b"data", "MadeUpFilter")


def test_empty_filters_passthrough():
    assert StreamEncoder.encode(b"data", None) == b"data"
    assert StreamEncoder.encode(b"data", []) == b"data"


def test_abbreviated_names_roundtrip():
    data = b"abbreviation test " * 20
    for filt in ("Fl", "LZW", "A85", "AHx", "RL"):
        assert _roundtrip(data, filt) == data
