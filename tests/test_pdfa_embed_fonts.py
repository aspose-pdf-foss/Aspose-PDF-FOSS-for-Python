"""PDF/A conversion auto-embeds Standard-14 / symbol fonts without a directory.

``convert_to_pdfa`` previously embedded fonts only from a caller-supplied
``font_lookup_directory``; a non-embedded Standard-14 font (which PDF/A requires
embedded) was left reported. It now embeds the bundled metric-compatible
substitute and synthesizes ``/Widths`` from that face. Non-standard custom fonts
still resolve to nothing here and stay reported.

Two things veraPDF 1.30.2 then found, on the samples
``scripts/write_conformance_samples.py`` writes:

* the substitute is an sfnt, attached as ``/FontFile2``, but the dictionary
  still said ``/Subtype /Type1`` -- and a Type 1 font's program is ``/FontFile``
  or ``/FontFile3`` (ISO 32000-1 table 122), never ``/FontFile2``. veraPDF read
  that as "the font program is not embedded", correctly, while this library's
  own check looked for any ``FontFile*`` key and thought it was there. So every
  PDF/A file this produced failed the one rule the embedding exists to satisfy.
* the pass walked page resources only, so the ``/Helv`` a form field's
  appearance is drawn with -- which lives in the AcroForm's ``/DR`` -- was left
  unembedded, and unreported.

Both are fixed below, and ``convert_to_pdfua`` embeds too: PDF/UA requires it
just as PDF/A does (ISO 14289-1 7.21.4.1).
"""

from __future__ import annotations

from aspose_pdf.document import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfStream,
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


# --- what veraPDF found -----------------------------------------------------


def test_an_embedded_sfnt_makes_the_font_a_truetype_one():
    # A /FontFile2 belongs to a /TrueType font. Left as /Type1, the program is
    # not one that font can have, and a reader draws nothing from it.
    doc = Document()
    doc.load_from(_pdf_with_font("Helvetica"))
    doc.convert_to_pdfa("1b")
    engine, font, descriptor = _resolved_font(doc)
    assert engine._get_name(font.get(PdfName("Subtype"))) == "TrueType"
    assert PdfName("FontFile2") in descriptor.mapping
    assert PdfName("FirstChar") in font.mapping and PdfName("Widths") in font.mapping


def test_a_program_under_the_wrong_key_is_not_an_embedded_font():
    doc = Document()
    doc.load_from(_pdf_with_font("Helvetica"))
    engine, font, _descriptor = _resolved_font(doc)
    # A Type 1 font holding a TrueType program: what this used to write.
    descriptor = PdfDictionary(
        {
            PdfName("Type"): PdfName("FontDescriptor"),
            PdfName("FontName"): PdfName("Helvetica"),
            PdfName("FontFile2"): engine._cos_doc.register_object(
                PdfStream(b"\x00\x01\x00\x00", {})
            ),
        }
    )
    font.mapping[PdfName("FontDescriptor")] = descriptor
    issues, _warnings = engine.check_pdfa_compliance_detailed("1b")
    assert any("not a program a /Type1 font can have" in issue for issue in issues)


def test_a_font_only_a_form_field_uses_is_found():
    # The /Helv in the AcroForm's default resources renders the field, so
    # PDF/A and PDF/UA both require it embedded -- and walking pages misses it.
    document = Document()
    document.pages.add()
    document.form.add_text_field("nickname", 0, (60, 100, 260, 130), value="typed")
    engine = document._engine_pdf
    engine._ensure_cos()
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acroform = engine._resolve(root.mapping[PdfName("AcroForm")])
    acroform.mapping[PdfName("DR")] = PdfDictionary(
        {
            PdfName("Font"): PdfDictionary(
                {
                    PdfName("Helv"): PdfDictionary(
                        {
                            PdfName("Type"): PdfName("Font"),
                            PdfName("Subtype"): PdfName("Type1"),
                            PdfName("BaseFont"): PdfName("Helvetica"),
                        }
                    )
                }
            )
        }
    )
    where = dict(
        (engine._get_name(font.mapping.get(PdfName("BaseFont"))), place)
        for font, place in engine._rendering_font_dictionaries()
    )
    assert "the form's default resources" in where.values()

    issues, _warnings = engine.check_pdfa_compliance_detailed("1b")
    assert any("the form's default resources" in issue for issue in issues)


def test_pdfua_conversion_embeds_the_fonts_it_requires():
    # PDF/UA requires every rendering font embedded; the conversion used to
    # leave a document that failed its own check.
    document = Document()
    document.pages.add().add_text("Heading", 60, 700, font_size=20)
    document.convert_to_pdfua(title="Sample", auto_tag=True)
    engine, font, descriptor = _resolved_font(document)
    assert isinstance(descriptor, PdfDictionary)
    assert PdfName("FontFile2") in descriptor.mapping
    assert engine._get_name(font.get(PdfName("Subtype"))) == "TrueType"
    assert not any(
        "not embedded" in error for error in document.validate_pdfua().errors
    )


def _bare_font(base: str = "Helvetica") -> PdfDictionary:
    """A simple font with no descriptor and so no embedded program."""
    return PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type1"),
            PdfName("BaseFont"): PdfName(base),
        }
    )


def _document_with_fonts_everywhere() -> Document:
    """One page whose fonts hide in each of the places a font can hide."""
    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    engine._ensure_cos()
    page = engine._get_page_dict(0)
    resources = engine._resolve(page.mapping.get(PdfName("Resources")))
    if not isinstance(resources, PdfDictionary):
        resources = PdfDictionary({})
        page.mapping[PdfName("Resources")] = resources

    resources.mapping[PdfName("Font")] = PdfDictionary(
        {PdfName("F1"): engine._cos_doc.register_object(_bare_font("Helvetica"))}
    )
    # Inside a form XObject the page draws.
    form = PdfStream(
        b"BT /F2 8 Tf (x) Tj ET",
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): PdfArray([]),
            PdfName("Resources"): PdfDictionary(
                {
                    PdfName("Font"): PdfDictionary(
                        {PdfName("F2"): engine._cos_doc.register_object(_bare_font("Courier"))}
                    )
                }
            ),
        },
    )
    resources.mapping[PdfName("XObject")] = PdfDictionary(
        {PdfName("Fm0"): engine._cos_doc.register_object(form)}
    )
    # In an annotation's appearance stream, and nowhere else.
    appearance = PdfStream(
        b"BT /F3 8 Tf (y) Tj ET",
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): PdfArray([]),
            PdfName("Resources"): PdfDictionary(
                {
                    PdfName("Font"): PdfDictionary(
                        {PdfName("F3"): engine._cos_doc.register_object(_bare_font("Times-Roman"))}
                    )
                }
            ),
        },
    )
    page.mapping[PdfName("Annots")] = PdfArray(
        [
            engine._cos_doc.register_object(
                PdfDictionary(
                    {
                        PdfName("Type"): PdfName("Annot"),
                        PdfName("Subtype"): PdfName("Widget"),
                        PdfName("Rect"): PdfArray([]),
                        PdfName("AP"): PdfDictionary(
                            {
                                PdfName("N"): engine._cos_doc.register_object(appearance)
                            }
                        ),
                    }
                )
            )
        ]
    )
    return document


def test_every_place_a_font_can_hide_is_looked_in():
    document = _document_with_fonts_everywhere()
    engine = document._engine_pdf
    found = {
        engine._get_name(font.mapping.get(PdfName("BaseFont"))): where
        for font, where in engine._rendering_font_dictionaries()
    }
    assert found["Helvetica"] == "page 1"
    assert found["Courier"] == "page 1"  # through the form XObject the page draws
    assert found["Times-Roman"] == "an annotation appearance on page 1"

    # Each one is reported, and says where it was found, so a caller knows
    # which font to deal with. Without a descriptor that is the complaint
    # PDF/A makes first; either way the font is no longer invisible.
    issues, _warnings = engine.check_pdfa_compliance_detailed("1b")
    assert any("Helvetica" in issue and "page 1" in issue for issue in issues)
    assert any("Courier" in issue and "page 1" in issue for issue in issues)
    assert any(
        "Times-Roman" in issue and "annotation appearance" in issue
        for issue in issues
    )


def test_every_font_is_embedded_not_only_the_first():
    document = _document_with_fonts_everywhere()
    document.convert_to_pdfa("1b")
    engine = document._engine_pdf
    for font, where in engine._rendering_font_dictionaries():
        descriptor = engine._resolve(font.mapping.get(PdfName("FontDescriptor")))
        base = engine._get_name(font.mapping.get(PdfName("BaseFont")))
        assert isinstance(descriptor, PdfDictionary), f"{base} in {where}"
        assert PdfName("FontFile2") in descriptor.mapping, f"{base} in {where}"
    assert not any(
        "not embedded" in issue
        for issue in engine.check_pdfa_compliance_detailed("1b")[0]
    )


def test_a_font_two_pages_share_is_reported_once():
    document = Document()
    document.pages.add()
    document.pages.add()
    engine = document._engine_pdf
    engine._ensure_cos()
    shared = engine._cos_doc.register_object(_bare_font("Helvetica"))
    for index in (0, 1):
        page = engine._get_page_dict(index)
        page.mapping[PdfName("Resources")] = PdfDictionary(
            {PdfName("Font"): PdfDictionary({PdfName("F1"): shared})}
        )
    assert len(engine._rendering_font_dictionaries()) == 1
    issues, _warnings = engine.check_pdfa_compliance_detailed("1b")
    assert sum("Helvetica" in issue for issue in issues) == 1
