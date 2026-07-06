"""Type0 editing/overlay without a ToUnicode CMap, and non-Identity-H overlays.

Two gaps are covered here:

* **#1** -- subset CIDFontType2 fonts (InDesign/LaTeX) often omit ToUnicode.
  ``replace_text``/``redact_text`` reconstruct code -> text by inverting the
  embedded TrueType Unicode ``cmap`` (code == CID == GID under Identity), so
  matching works with no ToUnicode at all.
* **#2** -- redaction-overlay bars now render for Identity-H without ToUnicode
  (fed by the reconstructed map), for embedded Encoding CMaps (parsed for
  code -> CID), and for Identity-V (a stacked vertical column bar).
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
from aspose_pdf.engine.font_subset import read_unicode_cmap
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.std_font_data import load_substitute_sfnt

# A real embedded TrueType (DejaVu Sans) with a Unicode cmap; under an Identity
# CIDToGIDMap the show-string code is the glyph id, so code == CID == GID.
_FONT = load_substitute_sfnt("sans-regular")
_UNI = read_unicode_cmap(_FONT)  # {codepoint: gid}


def _code(ch: str) -> bytes:
    return _UNI[ord(ch)].to_bytes(2, "big")


def _hexbytes(text: str) -> bytes:
    return b"".join(_code(c) for c in text)


def _reconstruct_doc() -> Document:
    """Identity-H CIDFontType2, embedded /FontFile2, NO /ToUnicode."""
    codes = _hexbytes("Hi")
    content = b"BT /F1 12 Tf 20 40 Td <" + codes.hex().upper().encode() + b"> Tj ET"
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc

    font_stream = PdfStream(content=_FONT, mapping={PdfName("Length1"): PdfNumber(len(_FONT))})
    descriptor = PdfDictionary(
        {
            PdfName("Type"): PdfName("FontDescriptor"),
            PdfName("FontName"): PdfName("TestCID"),
            PdfName("Ascent"): PdfNumber(800),
            PdfName("Descent"): PdfNumber(-200),
            PdfName("FontFile2"): cos.register_object(font_stream),
        }
    )
    cid_font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("CIDFontType2"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("CIDToGIDMap"): PdfName("Identity"),
            PdfName("DW"): PdfNumber(600),
            PdfName("FontDescriptor"): cos.register_object(descriptor),
        }
    )
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("Encoding"): PdfName("Identity-H"),
            PdfName("DescendantFonts"): PdfArray([cos.register_object(cid_font)]),
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)

    doc = Document()
    doc._engine_pdf = pdf
    return doc


# --- #1: reconstruction (no ToUnicode) --------------------------------------


def test_reconstruct_replace_without_tounicode() -> None:
    doc = _reconstruct_doc()
    assert doc.replace_text("Hi", "Ho") == 1
    content = doc.pages[0].content
    # "Ho" re-encodes through the reverse reconstructed map (H unchanged, i->o).
    assert _hexbytes("Ho").hex().upper().encode() in content
    assert _hexbytes("Hi").hex().upper().encode() not in content


def test_reconstruct_redact_without_tounicode() -> None:
    doc = _reconstruct_doc()
    assert doc.pages[0].redact_text("Hi") == 1
    content = doc.pages[0].content
    assert _hexbytes("Hi").hex().upper().encode() not in content


# --- #2a: Identity-H overlay fed by the reconstructed map --------------------


def test_reconstruct_overlay_draws_bar() -> None:
    doc = _reconstruct_doc()
    assert doc.pages[0].redact_text("Hi", overlay=True) == 1
    content = doc.pages[0].content
    assert b"h f" in content  # a redaction bar (filled path) was appended


# --- #2b: embedded Encoding CMap (non-identity) overlay ----------------------

# One-byte codes 0x01/0x02 -> CIDs 1/2 (embedded Encoding CMap), matched via a
# one-byte ToUnicode; overlay width comes from /W keyed by the mapped CID.
_ENC_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
2 begincidchar
<01> 1
<02> 2
endcidchar
endcmap
end
end
"""

_TU_1BYTE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<01> <0048>
<02> <0069>
endbfchar
endcmap
end
end
"""


def _embedded_cmap_doc() -> Document:
    content = b"BT /F1 12 Tf 20 40 Td <0102> Tj ET"  # codes 0x01, 0x02
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc

    enc_ref = cos.register_object(PdfStream(content=_ENC_CMAP, mapping={}))
    tu_ref = cos.register_object(PdfStream(content=_TU_1BYTE, mapping={}))
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
            PdfName("Encoding"): enc_ref,
            PdfName("DescendantFonts"): PdfArray([cos.register_object(cid_font)]),
            PdfName("ToUnicode"): tu_ref,
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)

    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_embedded_cmap_overlay_draws_bar() -> None:
    doc = _embedded_cmap_doc()
    assert doc.pages[0].redact_text("Hi", overlay=True) == 1
    content = doc.pages[0].content
    assert b"0102" not in content  # one-byte codes removed
    assert b"h f" in content  # bar drawn from the parsed code -> CID widths


# --- #2c: Identity-V vertical overlay ---------------------------------------

_TU_2BYTE = b"""/CIDInit /ProcSet findresource begin
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


def _identity_v_doc() -> Document:
    content = b"BT /F1 12 Tf 20 60 Td <00010002> Tj ET"
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc

    tu_ref = cos.register_object(PdfStream(content=_TU_2BYTE, mapping={}))
    cid_font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("CIDFontType2"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("DW"): PdfNumber(1000),
        }
    )
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("TestCID"),
            PdfName("Encoding"): PdfName("Identity-V"),
            PdfName("DescendantFonts"): PdfArray([cos.register_object(cid_font)]),
            PdfName("ToUnicode"): tu_ref,
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)

    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_identity_v_overlay_is_vertical_column() -> None:
    from aspose_pdf.engine.text_locate import locate_matches

    doc = _identity_v_doc()
    pdf = doc._engine_pdf
    content = pdf.page_contents[0]
    quads = locate_matches(content, "Hi", pdf._build_simple_font_metrics(0))
    assert quads, "vertical run should produce an overlay quad"
    # A stacked column bar: taller than it is wide (one-em column, two-glyph run).
    (x0, y0), (x1, _y1), (_x2, y2), _ = quads[0]
    width = abs(x1 - x0)
    height = abs(y2 - y0)
    assert height > width
