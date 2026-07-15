"""Type0 editing beyond Identity-H.

Matching a composite font's show strings only needs the font's ToUnicode CMap,
which maps character codes straight to Unicode -- so editing works for named or
embedded CMaps, one-byte codespaces and Identity-V too, not just Identity-H.
Redaction-overlay geometry needs code -> CID: Identity-H/V and embedded Encoding
CMaps draw a bar, as do the exact bundled predefined CMaps with compatible
CIDSystemInfo. A non-bundled named CMap can still use ToUnicode for exact text
editing, but without code-to-CID data it draws no overlay bar.
"""

from __future__ import annotations

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
)

# --- CidTextCodec: code lengths ---------------------------------------------


def test_codec_uniform_one_byte_codes() -> None:
    codec = CidTextCodec({b"\x41": "A", b"\x42": "B", b"\x43": "C"})
    units = codec.decode_units(b"\x41\x42\x43")
    assert [u[2] for u in units] == ["A", "B", "C"]
    assert [(u[0], u[1]) for u in units] == [(0, 1), (1, 1), (2, 1)]


def test_codec_uniform_one_byte_splice() -> None:
    codec = CidTextCodec({b"A": "A", b"B": "B", b"C": "C"})
    content = b"BT /F1 12 Tf (ABC) Tj ET"  # literal bytes are the codes
    out, count = redact_text_in_content(
        content, "B", codec_for_name=lambda name: codec
    )
    assert count == 1
    # Untouched one-byte codes stay byte-identical around the removed one.
    assert b"(AC)" in out


def test_codec_mixed_length_greedy_matches_longest() -> None:
    codec = CidTextCodec({b"\x20": " ", b"\x81\x40": "A", b"\x81\x41": "B"})
    units = codec.decode_units(b"\x81\x40\x20\x81\x41")
    assert [u[2] for u in units] == ["A", " ", "B"]
    assert [(u[0], u[1]) for u in units] == [(0, 2), (2, 1), (3, 2)]


def test_codec_mixed_length_unknown_prefix_is_unmapped() -> None:
    codec = CidTextCodec({b"\x20": " ", b"\x81\x40": "A"})
    units = codec.decode_units(b"\x99\x81\x40")  # 0x99 unknown -> min-len unit
    assert units[0][1] == 1 and units[0][2] == "�"
    assert units[1][2] == "A"


def test_codec_two_byte_trailing_odd_byte() -> None:
    codec = CidTextCodec({b"\x00\x41": "A"})
    units = codec.decode_units(b"\x00\x41\x00")  # dangling byte
    assert units[0][2] == "A"
    assert units[1] == (2, 1, "�")


# --- integration: non-identity encodings ------------------------------------

# Two-byte ToUnicode for "Hi" regardless of the (declared) encoding name.
_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
2 beginbfchar
<0001> <0048>
<0002> <0069>
endbfchar
endcmap
end
end
"""


def _type0_doc(encoding: str) -> Document:
    # Show string spells "Hi" with codes 0x0001, 0x0002.
    content = b"BT /F1 12 Tf 20 40 Td <00010002> Tj ET"
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
                [PdfNumber(1), PdfArray([PdfNumber(500), PdfNumber(500)])]
            ),
        }
    )
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("Encoding"): PdfName(encoding),
            PdfName("DescendantFonts"): PdfArray([cos.register_object(cid_font)]),
            PdfName("ToUnicode"): to_unicode_ref,
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)

    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_replace_non_identity_named_cmap() -> None:
    doc = _type0_doc("UniGB-UCS2-H")
    assert doc.replace_text("Hi", "ii") == 1
    content = doc.pages[0].content
    # "ii" encodes back through the reverse ToUnicode map (both are code 0x0002).
    assert b"<00020002>" in content


def test_redact_identity_v_removes_text() -> None:
    doc = _type0_doc("Identity-V")
    assert doc.pages[0].redact_text("Hi") == 1
    content = doc.pages[0].content
    assert b"00010002" not in content


def test_named_cmap_without_cidsysteminfo_draws_no_overlay_bar() -> None:
    # The missing CIDSystemInfo makes the named CMap unsafe for geometry, while
    # ToUnicode still permits exact text removal.
    doc = _type0_doc("UniGB-UCS2-H")
    assert doc.pages[0].redact_text("Hi", overlay=True) == 1
    content = doc.pages[0].content
    assert b"00010002" not in content  # text removed
    assert b" f\n" not in content and b"h f" not in content  # no fill path
