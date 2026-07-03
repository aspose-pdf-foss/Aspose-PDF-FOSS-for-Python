"""Type0 (Identity-H) text editing and redaction-overlay tracking.

Composite-font show strings hold two-byte CIDs: matching decodes them through
the font's ToUnicode CMap (``CidTextCodec``), edits splice whole code units
out of the raw operand so untouched codes stay byte-identical, and the
position tracker resolves advances from the CIDFont ``/W`` array so
``redact_text(overlay=True)`` draws bars over Type0 runs as well.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_edit import (
    CidTextCodec,
    redact_text_in_content,
    replace_text_in_content,
)
from aspose_pdf.engine.text_locate import CompositeFontMetric, locate_matches
from aspose_pdf.exceptions import PdfValidationException

# Two-byte codes for the glyphs of "Hello World" (and nothing else).
_MAP = {
    b"\x00\x01": "H",
    b"\x00\x02": "e",
    b"\x00\x03": "l",
    b"\x00\x04": "o",
    b"\x00\x05": " ",
    b"\x00\x06": "W",
    b"\x00\x07": "r",
    b"\x00\x08": "d",
}
_REVERSE = {text: code for code, text in _MAP.items()}
_CODEC = CidTextCodec(_MAP)
_CODECS = lambda name: _CODEC if name == "F1" else None  # noqa: E731

_METRIC = CompositeFontMetric(
    width_of=lambda cid: 500.0, code_to_text=_MAP, ascent=800.0, descent=-200.0
)
_METRICS = lambda name: _METRIC if name == "F1" else None  # noqa: E731


def _cids(text: str) -> str:
    """Hex CID string (without <>) spelling *text* in the test font."""
    return "".join(_REVERSE[ch].hex().upper() for ch in text)


# --- editor (engine level) ---------------------------------------------------


def test_redact_type0_removes_matched_codes() -> None:
    content = f"BT /F1 12 Tf <{_cids('Hello World')}> Tj ET".encode()
    out, count = redact_text_in_content(content, "World", codec_for_name=_CODECS)
    assert count == 1
    assert f"<{_cids('Hello ')}>".encode() in out
    assert _cids("World").encode() not in out


def test_replace_type0_encodes_replacement_via_reverse_cmap() -> None:
    content = f"BT /F1 12 Tf <{_cids('Hello World')}> Tj ET".encode()
    out, count = replace_text_in_content(
        content, "World", "led", codec_for_name=_CODECS
    )
    assert count == 1
    assert f"<{_cids('Hello led')}>".encode() in out


def test_replace_type0_unmappable_replacement_raises() -> None:
    content = f"BT /F1 12 Tf <{_cids('Hello')}> Tj ET".encode()
    with pytest.raises(PdfValidationException):
        replace_text_in_content(content, "Hello", "X", codec_for_name=_CODECS)


def test_type0_match_across_consecutive_tj_operators() -> None:
    content = (
        f"BT /F1 12 Tf <{_cids('Wor')}> Tj <{_cids('ld')}> Tj ET".encode()
    )
    out, count = redact_text_in_content(content, "World", codec_for_name=_CODECS)
    assert count == 1
    assert _cids("Wor").encode() not in out
    assert _cids("ld").encode() not in out


def test_type0_match_across_tj_array_elements() -> None:
    content = f"BT /F1 12 Tf [<{_cids('Wor')}> -50 <{_cids('ld')}>] TJ ET".encode()
    out, count = redact_text_in_content(content, "World", codec_for_name=_CODECS)
    assert count == 1
    assert _cids("Wor").encode() not in out


def test_mixed_fonts_resolve_codec_per_run() -> None:
    # /F1 shows CID codes, /F2 shows plain Latin-1 -- both runs must match.
    content = f"BT /F1 12 Tf <{_cids('He')}> Tj /F2 12 Tf (He) Tj ET".encode()
    out, count = redact_text_in_content(content, "He", codec_for_name=_CODECS)
    assert count == 2
    assert _cids("He").encode() not in out
    assert b"(He)" not in out


def test_type0_without_codec_keeps_legacy_matching() -> None:
    content = f"BT /F1 12 Tf <{_cids('Hello World')}> Tj ET".encode()
    out, count = redact_text_in_content(content, "World", codec_for_name=None)
    assert count == 0
    assert out == content


def test_partial_ligature_match_is_skipped() -> None:
    codec = CidTextCodec({b"\x00\x09": "ff", b"\x00\x01": "a"})
    content = b"BT /F1 12 Tf <00090001> Tj ET"  # decodes to "ffa"
    out, count = redact_text_in_content(
        content, "fa", codec_for_name=lambda name: codec
    )
    assert count == 0
    assert out == content


def test_whole_ligature_match_is_removed() -> None:
    codec = CidTextCodec({b"\x00\x09": "ff", b"\x00\x01": "a"})
    content = b"BT /F1 12 Tf <00090001> Tj ET"
    out, count = redact_text_in_content(
        content, "ffa", codec_for_name=lambda name: codec
    )
    assert count == 1
    assert b"<>" in out


# --- locator -----------------------------------------------------------------


def test_locate_type0_match_uses_cid_widths() -> None:
    content = f"BT /F1 10 Tf 100 200 Td <{_cids('Hello World')}> Tj ET".encode()
    quads = locate_matches(content, "World", _METRICS)
    assert len(quads) == 1
    (x0, y0), (x1, _y1), _tr, _tl = quads[0]
    # "World" starts after "Hello " (6 glyphs * 5 units) at x=100; 5 glyphs wide.
    assert round(x0, 1) == 130.0
    assert round(x1, 1) == 155.0
    assert round(y0, 1) == 198.0  # baseline 200 + descent (-200/1000*10)


def test_locate_type0_variable_widths() -> None:
    metric = CompositeFontMetric(
        width_of=lambda cid: 1000.0 if cid == 6 else 500.0,  # wide "W"
        code_to_text=_MAP,
    )
    content = f"BT /F1 10 Tf 0 0 Td <{_cids('Hello World')}> Tj ET".encode()
    quads = locate_matches(content, "World", lambda name: metric)
    assert len(quads) == 1
    (x0, _y0), (x1, _y1), _tr, _tl = quads[0]
    assert round(x0, 1) == 30.0
    assert round(x1, 1) == 60.0  # 10 for "W" + 4 * 5


def test_locate_type0_odd_length_string_is_untracked() -> None:
    quads = locate_matches(b"BT /F1 10 Tf 0 0 Td <000102> Tj ET", "H", _METRICS)
    assert quads == []


# --- integration: COS-backed document ---------------------------------------

_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
8 beginbfchar
<0001> <0048>
<0002> <0065>
<0003> <006C>
<0004> <006F>
<0005> <0020>
<0006> <0057>
<0007> <0072>
<0008> <0064>
endbfchar
endcmap
end
end
"""


def _type0_doc() -> Document:
    content = f"BT /F1 12 Tf 20 40 Td <{_cids('Hello World')}> Tj ET".encode()
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc

    to_unicode_ref = cos.register_object(PdfStream(content=_CMAP, mapping={}))
    cid_font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("CIDFontType2"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("DW"): PdfNumber(1000),
            PdfName("W"): PdfArray(
                [
                    PdfNumber(1),
                    PdfArray([PdfNumber(500) for _ in range(8)]),
                ]
            ),
        }
    )
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("Encoding"): PdfName("Identity-H"),
            PdfName("DescendantFonts"): PdfArray([cos.register_object(cid_font)]),
            PdfName("ToUnicode"): to_unicode_ref,
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)

    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_redact_overlay_draws_bar_for_type0_font() -> None:
    doc = _type0_doc()
    assert doc.pages[0].redact_text("World", overlay=True) == 1
    content = doc.pages[0].content
    assert _cids("World").encode() not in content
    assert b"h f" in content  # a filled overlay path was appended
    # "World" starts after "Hello " (6 glyphs * 6pt at size 12) at x=20.
    assert b"56.000" in content
    assert b"86.000" in content


def test_document_replace_text_rewrites_type0_codes() -> None:
    doc = _type0_doc()
    assert doc.replace_text("World", "led") == 1
    content = doc.pages[0].content
    assert f"<{_cids('Hello led')}>".encode() in content
