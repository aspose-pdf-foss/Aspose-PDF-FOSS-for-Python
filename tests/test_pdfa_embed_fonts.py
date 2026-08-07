"""PDF/A conversion auto-embeds Standard-14 / symbol fonts without a directory.

``convert_to_pdfa`` previously embedded fonts only from a caller-supplied
``font_lookup_directory``; a non-embedded Standard-14 font (which PDF/A requires
embedded) was left reported. It now embeds the bundled metric-compatible
substitute and synthesizes ``/Widths`` from that face. Non-standard custom fonts
still resolve to nothing here and stay reported.
"""

from __future__ import annotations

from aspose_pdf.document import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def _pdf_with_font(base_font: str, *, subtype: str = "Type1") -> bytes:
    """One page showing a non-embedded simple font (no descriptor, no widths)."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 120)]
    pdf.page_contents = [b"BT /F1 24 Tf 20 60 Td (ABC) Tj ET"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    font_ref = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName(subtype),
                PdfName("BaseFont"): PdfName(base_font),
            }
        )
    )
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): font_ref})}
    )
    return pdf.to_bytes()


def _resolved_font(doc: Document) -> tuple:
    engine = doc._engine_pdf
    page = engine._get_page_dict(0)
    res = engine._resolve(page.get(PdfName("Resources")))
    fonts = engine._resolve(res.get(PdfName("Font")))
    font = engine._resolve(fonts.get(PdfName("F1")))
    descriptor = engine._resolve(font.get(PdfName("FontDescriptor")))
    return engine, font, descriptor


def test_standard14_font_is_embedded_with_synthesized_widths():
    doc = Document()
    doc.load_from(_pdf_with_font("Helvetica"))
    doc.convert_to_pdfa("1b")

    engine, font, descriptor = _resolved_font(doc)
    assert isinstance(descriptor, PdfDictionary)
    assert PdfName("FontFile2") in descriptor.mapping  # now embedded
    stream = engine._resolve(descriptor.mapping.get(PdfName("FontFile2")))
    assert stream.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")

    widths = engine._resolve(font.get(PdfName("Widths")))
    first = int(engine._get_number(font.get(PdfName("FirstChar"))))
    assert isinstance(widths, PdfArray) and first <= 65
    # The Helvetica 'A' advance the metric-compatible substitute reproduces.
    assert int(engine._get_number(widths.items[65 - first])) == 667


def test_symbol_font_is_embedded():
    doc = Document()
    doc.load_from(_pdf_with_font("Symbol"))
    doc.convert_to_pdfa("1b")
    _engine, _font, descriptor = _resolved_font(doc)
    assert isinstance(descriptor, PdfDictionary)
    assert PdfName("FontFile2") in descriptor.mapping


def test_non_standard_font_stays_reported():
    doc = Document()
    doc.load_from(_pdf_with_font("AcmeCorp-Sans"))
    issues = doc.convert_to_pdfa("1b")

    _engine, _font, descriptor = _resolved_font(doc)
    # A substitute would change appearance, so a custom font is not auto-embedded.
    if isinstance(descriptor, PdfDictionary):
        assert PdfName("FontFile2") not in descriptor.mapping
    assert any("AcmeCorp-Sans" in issue for issue in issues)


def test_embedded_standard14_renders_the_same_glyphs():
    before = Document()
    before.load_from(_pdf_with_font("Helvetica"))
    before_raster = before.pages[0].render(antialias=False)

    after = Document()
    after.load_from(_pdf_with_font("Helvetica"))
    after.convert_to_pdfa("1b")
    after_raster = after.pages[0].render(antialias=False)

    # The embedded face is the same substitute at the same widths, so the page
    # renders identically (real glyphs, unchanged).
    w, h = 200, 120

    def pixels(raster):
        return [raster.get_pixel(x, y) for y in range(h) for x in range(w)]

    def ink(raster):
        return sum(
            1
            for px in pixels(raster)
            if px[0] < 128 and px[1] < 128 and px[2] < 128
        )

    assert ink(before_raster) > 0
    assert pixels(before_raster) == pixels(after_raster)
