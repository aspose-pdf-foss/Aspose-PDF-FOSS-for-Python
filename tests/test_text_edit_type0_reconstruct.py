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

import pytest

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


def _embedded_cmap_doc(
    *,
    vertical: bool = False,
    shown: bytes = b"\x01\x02",
    encoding_cmap: bytes = _ENC_CMAP,
    to_unicode: bytes = _TU_1BYTE,
) -> Document:
    content = (
        b"BT /F1 12 Tf 20 60 Td <"
        + shown.hex().upper().encode("ascii")
        + b"> Tj ET"
    )
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc

    encoding_cmap = (
        encoding_cmap.replace(b"begincmap", b"begincmap\n/WMode 1 def", 1)
        if vertical
        else encoding_cmap
    )
    enc_ref = cos.register_object(PdfStream(content=encoding_cmap, mapping={}))
    tu_ref = cos.register_object(PdfStream(content=to_unicode, mapping={}))
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
    if vertical:
        cid_font.mapping[PdfName("DW2")] = PdfArray(
            [PdfNumber(880), PdfNumber(-1000)]
        )
        cid_font.mapping[PdfName("W2")] = PdfArray(
            [
                PdfNumber(1),
                PdfArray(
                    [
                        PdfNumber(-500),
                        PdfNumber(250),
                        PdfNumber(880),
                        PdfNumber(-700),
                        PdfNumber(250),
                        PdfNumber(880),
                    ]
                ),
            ]
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
    assert doc._engine_pdf.extract_text() == "Hi"
    assert doc.pages[0].redact_text("Hi", overlay=True) == 1
    content = doc.pages[0].content
    assert b"0102" not in content  # one-byte codes removed
    assert b"h f" in content  # bar drawn from the parsed code -> CID widths


def test_embedded_cmap_rejects_invalid_to_unicode_source_code() -> None:
    encoding_cmap = _ENC_CMAP.replace(
        b"2 begincidchar\n<01> 1\n<02> 2",
        b"1 begincidchar\n<01> 1",
    )
    to_unicode = _TU_1BYTE.replace(
        b"2 beginbfchar\n<01> <0048>\n<02> <0069>",
        b"1 beginbfchar\n<02> <0058>",
    )
    doc = _embedded_cmap_doc(
        shown=b"\x02",
        encoding_cmap=encoding_cmap,
        to_unicode=to_unicode,
    )
    before = doc.pages[0].content

    assert doc._engine_pdf.extract_text() == "�"
    assert doc.replace_text("X", "Y") == 0
    assert doc.pages[0].content == before


def test_embedded_cmap_program_wmode_uses_vertical_metrics() -> None:
    from aspose_pdf.engine.text_locate import locate_matches

    doc = _embedded_cmap_doc(vertical=True)
    pdf = doc._engine_pdf
    metric = pdf._build_simple_font_metrics(0)("F1")
    quads = locate_matches(
        pdf.page_contents[0],
        "Hi",
        pdf._build_simple_font_metrics(0),
    )

    assert metric is not None and metric.vertical
    assert metric.vertical_metrics_of(1) == (-500.0, 250.0, 880.0)
    assert len(quads) == 1
    (x0, y0), (x1, _y1), (_x2, y2), _ = quads[0]
    assert abs(y2 - y0) > abs(x1 - x0)


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


def test_vertical_tj_positive_adjustment_moves_following_glyph_down() -> None:
    from aspose_pdf.engine.text_locate import locate_matches

    doc = _identity_v_doc()
    pdf = doc._engine_pdf
    metric_for_name = pdf._build_simple_font_metrics(0)
    baseline = pdf.page_contents[0].replace(
        b"<00010002> Tj",
        b"[<0001> 0 <0002>] TJ",
    )
    adjusted = baseline.replace(b" 0 <0002>", b" 200 <0002>")

    baseline_quad = locate_matches(baseline, "Hi", metric_for_name)[0]
    adjusted_quad = locate_matches(adjusted, "Hi", metric_for_name)[0]
    baseline_y = [point[1] for point in baseline_quad]
    adjusted_y = [point[1] for point in adjusted_quad]

    assert min(adjusted_y) == pytest.approx(min(baseline_y) - 2.4)
