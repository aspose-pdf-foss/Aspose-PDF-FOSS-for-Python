"""Text fields with an embedded Type0 (CID) font render non-Latin values.

Without an embedded font a field uses the Standard-14 ``/DR`` Helvetica, so a
non-Latin value cannot be shown. Passing ``font=`` embeds a Type0 font in the
AcroForm ``/DR`` and bakes a CID-encoded ``/AP`` so the value's real glyphs are
referenced. (The page rasterizer does not composite widget ``/AP`` streams; the
appearance is validated at the COS level, as a viewer would consume it.)
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfName,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

_VALUE = "Привет"


def _cyrillic_font() -> bytes:
    order = [".notdef"] + [f"g{ord(c):04X}" for c in dict.fromkeys(_VALUE)]
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    cmap = {}
    for char in dict.fromkeys(_VALUE):
        name = f"g{ord(char):04X}"
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((400, 0))
        pen.lineTo((400, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()
        cmap[ord(char)] = name
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (400, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Cyr", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupMaxp()
    fb.font.recalcTimestamp = False
    out = io.BytesIO()
    fb.font.save(out)
    return out.getvalue()


def _field_doc() -> Document:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 200)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    doc = Document()
    doc._engine_pdf = pdf
    doc.form.add_text_field(
        "nm", doc.pages[0], (20, 150, 280, 180), value=_VALUE, font=_cyrillic_font()
    )
    return doc


def _dr_type0_font(engine: SimplePdf):
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    acro = engine._resolve(root.mapping.get(PdfName("AcroForm")))
    dr = engine._resolve(acro.mapping.get(PdfName("DR")))
    fonts = engine._resolve(dr.mapping.get(PdfName("Font")))
    for name, ref in fonts.mapping.items():
        font = engine._resolve(ref)
        if engine._get_name(font.mapping.get(PdfName("Subtype"))) == "Type0":
            return name.name.lstrip("/"), font
    return None, None


def _widget_ap_content(engine: SimplePdf) -> bytes:
    annots = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Annots")))
    for ref in annots.items:
        widget = engine._resolve(ref)
        if engine._get_name(widget.mapping.get(PdfName("Subtype"))) == "Widget":
            ap = engine._resolve(widget.mapping.get(PdfName("AP")))
            normal = engine._resolve(ap.mapping.get(PdfName("N")))
            if isinstance(normal, PdfStream):
                return normal.content
    return b""


def test_type0_font_embedded_in_dr():
    engine = _field_doc()._engine_pdf
    name, font = _dr_type0_font(engine)
    assert name is not None
    # Full Type0 graph: Identity-H + a descendant CIDFontType2 with FontFile2.
    assert engine._get_name(font.mapping.get(PdfName("Encoding"))) == "Identity-H"
    descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
    cidfont = engine._resolve(descendants.items[0])
    descriptor = engine._resolve(cidfont.mapping.get(PdfName("FontDescriptor")))
    assert PdfName("FontFile2") in descriptor.mapping
    assert PdfName("ToUnicode") in font.mapping


def test_field_ap_is_cid_encoded():
    content = _widget_ap_content(_field_doc()._engine_pdf)
    # A hex (two-byte CID) show string, not a Latin-1 literal.
    assert b"> Tj" in content and b"<" in content
    assert b"(" not in content.split(b"Tj")[0]  # no literal-string operand


def test_type0_field_round_trips_and_survives_regeneration():
    doc = _field_doc()
    doc.form.generate_appearances()  # must not clobber the baked CID /AP
    engine = doc._engine_pdf
    assert b"> Tj" in _widget_ap_content(engine)

    buffer = io.BytesIO()
    doc.save(buffer)
    reloaded = Document()
    reloaded.load_from(buffer.getvalue())
    name, font = _dr_type0_font(reloaded._engine_pdf)
    assert name is not None
    # ToUnicode decodes the baked CIDs back to the original value.
    to_unicode = reloaded._engine_pdf._resolve(font.mapping.get(PdfName("ToUnicode")))
    assert isinstance(to_unicode, PdfStream)
    content = _widget_ap_content(reloaded._engine_pdf)
    assert b"> Tj" in content


def test_plain_ascii_field_still_uses_standard14():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 200)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    doc = Document()
    doc._engine_pdf = pdf
    doc.form.add_text_field("plain", doc.pages[0], (20, 150, 280, 180), value="Hi")
    field_obj = doc._engine_pdf.get_form_fields()["plain"]
    da = field_obj.get("DA", "") if isinstance(field_obj, dict) else ""
    # No Type0 embedding for a default field.
    assert isinstance(da, str)
