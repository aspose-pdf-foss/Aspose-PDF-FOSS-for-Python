"""Supplying a font program for a font the document only names.

A PDF may *refer* to a font without carrying it -- ``/BaseFont /Arial`` and
nothing else -- and then its text draws differently wherever Arial is different
or absent. PDF/A, PDF/UA and PDF/X all forbid that, and no amount of rewriting
fixes it: the program is not in the file. ``Document.embed_fonts`` supplies it,
matching each missing font to a real face from the same sources the renderer
substitutes from and writing that face into the document.

The guarantees under test:

* The Standard 14, Symbol and ZapfDingbats need no font source at all -- the
  bundled metric-compatible faces cover them, so a bare call is worth making.
* A font nothing answers to is *reported*, never silently left: the message
  names the font, the page, and what the caller can do about it.
* The program goes under the key its kind uses and ``/Subtype`` is corrected to
  match (ISO 32000-1 table 122), because a TrueType program under a ``/Type1``
  font is not an embedded font at all.
* The document's own metrics survive: ``/Widths`` and ``/W`` are not replaced,
  so a supplied face changes which glyphs are drawn and never where they sit.
* A composite font gets a rebuilt ``/CIDToGIDMap``, since its CIDs are glyph
  indices into the program it no longer has (9.7.4.2), and its text still reads
  afterwards.

Every face used here is either bundled with the package or synthesised in the
test, so nothing depends on what the machine running the suite has installed.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, FontSubstitutionOptions, PageSize
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import PdfValidationException

PROGRAM_KEYS = ("FontFile", "FontFile2", "FontFile3")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def face() -> bytes:
    """A real, usable TrueType program: the bundled sans substitute."""
    return load_substitute_sfnt("sans-regular")


def _reopen(doc: Document) -> Document:
    """Save *doc* and hand back what a reader would see."""
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return Document(buffer.getvalue())


def _standard14_doc() -> Document:
    """A page whose only font is a non-embedded Helvetica."""
    doc = Document()
    page = doc.pages.add(PageSize.A4)
    page.add_text("Hello", 50, 700, font_size=14)
    return _reopen(doc)


def _named_font_doc(base_font: str = "Corporate Sans", subtype: str = "TrueType"):
    """A page whose only font is a non-embedded font with *base_font*'s name.

    It carries a descriptor with metrics and no program, which is how a
    non-embedded font actually appears in a file produced elsewhere -- and the
    state the conformance checks call "not embedded".
    """
    doc = Document()
    page = doc.pages.add(PageSize.A4)
    page.add_text("Hello", 50, 700, font_size=14)
    reopened = _reopen(doc)
    engine = reopened._engine_pdf
    for font, _where in engine._rendering_font_dictionaries():
        font.mapping[PdfName("BaseFont")] = PdfName(base_font)
        font.mapping[PdfName("Subtype")] = PdfName(subtype)
        font.mapping[PdfName("FontDescriptor")] = engine._cos_doc.register_object(
            PdfDictionary(
                {
                    PdfName("Type"): PdfName("FontDescriptor"),
                    PdfName("FontName"): PdfName(base_font),
                    PdfName("Flags"): PdfNumber(32),
                    PdfName("ItalicAngle"): PdfNumber(0),
                    PdfName("Ascent"): PdfNumber(700),
                    PdfName("Descent"): PdfNumber(-200),
                    PdfName("CapHeight"): PdfNumber(700),
                    PdfName("StemV"): PdfNumber(80),
                }
            )
        )
    return reopened


def _composite_doc(face: bytes, *, text: str = "Zażółć gęślą jaźń") -> Document:
    """A page with a properly embedded composite font."""
    doc = Document()
    page = doc.pages.add(PageSize.A4)
    page.add_text(text, 50, 700, font=face, font_size=14)
    return _reopen(doc)


def _descendant(engine, font: PdfDictionary) -> PdfDictionary:
    descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
    return engine._resolve(descendants.items[0])


def _only_font(doc: Document) -> tuple[PdfDictionary, PdfDictionary | None]:
    """The document's single font dictionary and its descriptor."""
    engine = doc._engine_pdf
    fonts = list(engine._rendering_font_dictionaries())
    assert len(fonts) == 1, f"expected one font, found {len(fonts)}"
    font = fonts[0][0]
    carrier, _kind = engine._font_program_carrier(font)
    descriptor = (
        engine._resolve(carrier.mapping.get(PdfName("FontDescriptor")))
        if carrier is not None
        else None
    )
    return font, descriptor


def _program_keys(doc: Document, descriptor: PdfDictionary | None) -> list[str]:
    """The program keys the descriptor carries; none at all when it has no descriptor.

    A Standard 14 font legitimately has no ``/FontDescriptor`` (table 111), and
    neither has a font that only names itself -- which is exactly the state
    these tests start from.
    """
    if descriptor is None:
        return []
    return [key for key in PROGRAM_KEYS if PdfName(key) in descriptor.mapping]


def _strip_program(doc: Document) -> None:
    """Remove the embedded program, leaving a font the document only names."""
    _font, descriptor = _only_font(doc)
    assert descriptor is not None
    for key in PROGRAM_KEYS:
        descriptor.mapping.pop(PdfName(key), None)


# ---------------------------------------------------------------------------
# The bundled faces, with nothing configured
# ---------------------------------------------------------------------------


def test_a_standard14_font_is_supplied_with_no_font_source_named():
    with _standard14_doc() as doc:
        assert doc.embed_fonts() == []
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == ["FontFile2"]


def test_the_supplied_standard14_font_satisfies_the_pdfa_font_rule():
    with _standard14_doc() as doc:
        # A Standard 14 font carries no descriptor at all (table 111), which is
        # what the check says about it before a program is supplied.
        assert [e for e in doc.validate_pdfa("2b").errors if "Font" in e]
        doc.embed_fonts()
        assert not [e for e in doc.validate_pdfa("2b").errors if "Font" in e]


def test_symbol_and_zapfdingbats_are_bundled_too():
    doc = Document()
    page = doc.pages.add(PageSize.A4)
    page.add_text("abc", 50, 700, font_name="Symbol")
    page.add_text("abc", 50, 650, font_name="ZapfDingbats")
    with _reopen(doc) as reopened:
        assert reopened.embed_fonts() == []
        engine = reopened._engine_pdf
        for font, _where in engine._rendering_font_dictionaries():
            descriptor = engine._resolve(font.mapping.get(PdfName("FontDescriptor")))
            assert PdfName("FontFile2") in descriptor.mapping


def test_a_font_no_source_answers_to_is_reported_not_silently_left():
    with _named_font_doc() as doc:
        report = doc.embed_fonts()
        assert len(report) == 1
        message = report[0]
        assert "Corporate Sans" in message
        assert "page 1" in message
        assert "FontSubstitutionOptions" in message
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == []


# ---------------------------------------------------------------------------
# Naming where the fonts are
# ---------------------------------------------------------------------------


def test_a_program_handed_over_directly_supplies_the_font(face):
    options = FontSubstitutionOptions(fonts={"Corporate Sans": face})
    with _named_font_doc() as doc:
        assert doc.embed_fonts(sources=options) == []
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == ["FontFile2"]


def test_a_directory_supplies_the_font(tmp_path, face):
    (tmp_path / "Corporate Sans.ttf").write_bytes(face)
    with _named_font_doc() as doc:
        assert doc.embed_fonts(directory=tmp_path) == []
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == ["FontFile2"]


def test_a_directory_is_searched_by_what_the_face_is_not_what_it_is_called(
    tmp_path, face
):
    """A face is found through its own ``name`` table, whatever the file is called."""
    from aspose_pdf.engine.font_resolver import FontResolver

    (tmp_path / "nothing-like-the-name.ttf").write_bytes(face)
    resolver = FontResolver(
        directories=(tmp_path,), programs=(), use_system_fonts=False
    )
    # The bundled substitutes carry an empty ``name`` table -- they are built
    # for embedding, not identification -- so this one indexes under its file
    # stem. Either way the lookup is the face's, never a bare file-name match.
    assert resolver.by_name("nothing-like-the-name") is not None
    assert resolver.by_name("Corporate Sans") is None


def test_the_documents_own_font_substitution_is_used_when_nothing_is_named(face):
    with _named_font_doc() as doc:
        doc.font_substitution = FontSubstitutionOptions(
            fonts={"Corporate Sans": face}
        )
        assert doc.embed_fonts() == []
        _font, descriptor = _only_font(doc)
        assert PdfName("FontFile2") in descriptor.mapping


def test_a_file_that_is_not_a_font_is_refused(tmp_path):
    (tmp_path / "Corporate Sans.ttf").write_bytes(b"not a font at all")
    with _named_font_doc() as doc:
        report = doc.embed_fonts(directory=tmp_path)
        assert len(report) == 1
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == []


# ---------------------------------------------------------------------------
# What is written, and what is left alone
# ---------------------------------------------------------------------------


def test_a_type1_font_is_corrected_to_truetype_rather_than_given_a_type1_key(face):
    """Table 122: a TrueType program under /Type1 is not an embedded font."""
    options = FontSubstitutionOptions(fonts={"Corporate Sans": face})
    with _named_font_doc(subtype="Type1") as doc:
        assert doc.embed_fonts(sources=options) == []
        font, descriptor = _only_font(doc)
        assert doc._engine_pdf._get_name(font.mapping.get(PdfName("Subtype"))) == (
            "TrueType"
        )
        assert _program_keys(doc, descriptor) == ["FontFile2"]


def test_the_documents_own_widths_survive_embedding(face):
    options = FontSubstitutionOptions(fonts={"Corporate Sans": face})
    with _named_font_doc() as doc:
        font, _descriptor = _only_font(doc)
        before = font.mapping.get(PdfName("Widths"))
        before_values = (
            [item.value for item in doc._engine_pdf._resolve(before).items]
            if before is not None
            else None
        )
        doc.embed_fonts(sources=options)
        after = doc._engine_pdf._resolve(font.mapping.get(PdfName("Widths")))
        if before_values is None:
            assert after is None or after.items
        else:
            assert [item.value for item in after.items] == before_values


def test_an_already_embedded_font_is_left_exactly_as_it_is(face):
    with _composite_doc(face) as doc:
        _font, descriptor = _only_font(doc)
        program = doc._engine_pdf._resolve(descriptor.mapping[PdfName("FontFile2")])
        before = bytes(program.content)
        assert doc.embed_fonts(sources=FontSubstitutionOptions.system()) == []
        _font2, descriptor2 = _only_font(doc)
        after = doc._engine_pdf._resolve(descriptor2.mapping[PdfName("FontFile2")])
        assert bytes(after.content) == before


def test_a_type3_font_is_not_touched():
    """A Type 3 font carries its glyphs as content streams; there is nothing to embed."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"BT /F1 12 Tf 10 100 Td (a) Tj ET"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    glyph = cos.register_object(
        PdfStream(b"0 0 0 0 0 0 d0", {PdfName("Length"): PdfNumber(14)})
    )
    font = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type3"),
                PdfName("FontBBox"): PdfArray([PdfNumber(0)] * 4),
                PdfName("FontMatrix"): PdfArray(
                    [PdfNumber(v) for v in (0.001, 0, 0, 0.001, 0, 0)]
                ),
                PdfName("CharProcs"): PdfDictionary({PdfName("a"): glyph}),
                PdfName("Encoding"): PdfName("WinAnsiEncoding"),
                PdfName("FirstChar"): PdfNumber(97),
                PdfName("LastChar"): PdfNumber(97),
                PdfName("Widths"): PdfArray([PdfNumber(500)]),
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): font})}
    )
    assert pdf.embed_missing_fonts() == []


# ---------------------------------------------------------------------------
# Composite fonts
# ---------------------------------------------------------------------------


def test_a_composite_font_is_supplied_and_its_text_still_reads(face):
    text = "Zażółć gęślą jaźń"
    with _composite_doc(face, text=text) as doc:
        _strip_program(doc)
        assert any("not embedded" in e for e in doc.validate_pdfa("2b").errors)

        options = FontSubstitutionOptions(fonts={"Font": face})
        assert doc.embed_fonts(sources=options) == []

        _font, descriptor = _only_font(doc)
        assert PdfName("FontFile2") in descriptor.mapping
        assert not [e for e in doc.validate_pdfa("2b").errors if "embedded" in e]
        assert text in doc.extract_text()


def test_a_supplied_composite_font_gets_its_cid_to_gid_map_rebuilt(face):
    """A CIDFontType2's CIDs are glyph indices (9.7.4.2), so the map has to change."""
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        descendant = _descendant(engine, font)
        _strip_program(doc)
        descendant.mapping.pop(PdfName("CIDToGIDMap"), None)

        options = FontSubstitutionOptions(fonts={"Font": face})
        assert doc.embed_fonts(sources=options) == []

        mapping = engine._resolve(descendant.mapping.get(PdfName("CIDToGIDMap")))
        assert isinstance(mapping, PdfStream)
        assert len(mapping.content) % 2 == 0
        assert any(mapping.content), "every CID mapped to glyph 0"


def test_a_composite_fonts_own_advances_are_not_replaced(face):
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        descendant = _descendant(engine, font)
        before = engine._resolve(descendant.mapping.get(PdfName("W")))
        before_dump = None if before is None else repr(before.items)
        _strip_program(doc)
        doc.embed_fonts(sources=FontSubstitutionOptions(fonts={"Font": face}))
        after = engine._resolve(descendant.mapping.get(PdfName("W")))
        assert (None if after is None else repr(after.items)) == before_dump


def test_a_composite_font_with_no_to_unicode_map_is_reported(face):
    with _composite_doc(face) as doc:
        font, _descriptor = _only_font(doc)
        _strip_program(doc)
        font.mapping.pop(PdfName("ToUnicode"), None)
        report = doc.embed_fonts(
            sources=FontSubstitutionOptions(fonts={"Font": face})
        )
        assert len(report) == 1
        assert "ToUnicode" in report[0]


def test_a_cidfonttype0_is_reported_rather_than_mismapped(face):
    """Its CIDs index a CID-keyed CFF's charset; no remapping turns that into glyphs."""
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        descendant = _descendant(engine, font)
        _strip_program(doc)
        descendant.mapping[PdfName("Subtype")] = PdfName("CIDFontType0")
        report = doc.embed_fonts(
            sources=FontSubstitutionOptions(fonts={"Font": face})
        )
        assert len(report) == 1
        assert "CIDFontType0" in report[0]


def test_a_composite_font_with_no_descendant_is_reported(face):
    with _composite_doc(face) as doc:
        font, _descriptor = _only_font(doc)
        font.mapping[PdfName("DescendantFonts")] = PdfArray([])
        report = doc.embed_fonts()
        assert len(report) == 1
        assert "descendant" in report[0]


def test_a_composite_font_falls_back_to_its_character_collection(face):
    """Nothing answers to the name, but the font says which collection it is for.

    A composite font declares its ``/CIDSystemInfo`` (9.7.3), and a face known
    to serve that collection is a better answer than no font at all. ``SimSun``
    is one of the families preferred for Adobe-GB1.
    """
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        descendant = _descendant(engine, font)
        _strip_program(doc)
        font.mapping[PdfName("BaseFont")] = PdfName("NoSuchFaceAnywhere")
        descendant.mapping[PdfName("BaseFont")] = PdfName("NoSuchFaceAnywhere")
        descendant.mapping[PdfName("CIDSystemInfo")] = PdfDictionary(
            {
                PdfName("Registry"): PdfString(b"Adobe"),
                PdfName("Ordering"): PdfString(b"GB1"),
                PdfName("Supplement"): PdfNumber(2),
            }
        )
        report = doc.embed_fonts(
            sources=FontSubstitutionOptions(fonts={"SimSun": face})
        )
        assert report == []
        _font2, descriptor = _only_font(doc)
        assert PdfName("FontFile2") in descriptor.mapping


# ---------------------------------------------------------------------------
# The conformance checks read a composite font's program where it lives
# ---------------------------------------------------------------------------


def test_an_embedded_composite_font_passes_the_pdfa_font_rule(face):
    """Table 121 gives a Type 0 font no descriptor; 9.7.4 puts it on the descendant."""
    with _composite_doc(face) as doc:
        assert doc.convert_to_pdfa("2b") == []
        assert doc.validate_pdfa("2b").errors == []


def test_an_embedded_composite_font_passes_the_pdfx_font_rule(face):
    with _composite_doc(face) as doc:
        assert [e for e in doc.convert_to_pdfx("PDF/X-4") if "ont" in e] == []


def test_a_composite_font_with_no_program_reads_as_not_embedded(face):
    """Not as "missing FontDescriptor", which said nothing about what was wrong."""
    with _composite_doc(face) as doc:
        _strip_program(doc)
        font_errors = [e for e in doc.validate_pdfa("2b").errors if "Font" in e]
        assert len(font_errors) == 1
        assert "not embedded" in font_errors[0]
        assert "FontDescriptor" not in font_errors[0]


def test_a_composite_font_that_really_has_no_descriptor_still_says_so(face):
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        _descendant(engine, font).mapping.pop(PdfName("FontDescriptor"))
        errors = doc.validate_pdfa("2b").errors
        assert any("missing FontDescriptor" in e for e in errors)


def test_a_program_under_the_wrong_key_is_not_an_embedded_font(face):
    """Table 126: a CIDFontType2 does not carry a /FontFile."""
    with _composite_doc(face) as doc:
        _font, descriptor = _only_font(doc)
        program = descriptor.mapping.pop(PdfName("FontFile2"))
        descriptor.mapping[PdfName("FontFile")] = program
        errors = doc.validate_pdfa("2b").errors
        assert any("is /CIDFontType2 but carries" in e for e in errors)


# ---------------------------------------------------------------------------
# The conversions do this pass themselves
# ---------------------------------------------------------------------------


def test_convert_to_pdfa_supplies_a_named_font_from_the_documents_sources(face):
    with _named_font_doc() as doc:
        doc.font_substitution = FontSubstitutionOptions(
            fonts={"Corporate Sans": face}
        )
        remaining = doc.convert_to_pdfa("2b")
        assert not [e for e in remaining if "embedded" in e]


def test_convert_to_pdfua_supplies_a_named_font_from_the_documents_sources(face):
    """PDF/UA requires every font embedded too (ISO 14289-1 7.21.4.1)."""
    with _named_font_doc() as doc:
        doc.font_substitution = FontSubstitutionOptions(
            fonts={"Corporate Sans": face}
        )
        remaining = doc.convert_to_pdfua()
        assert not [e for e in remaining if "embedded" in e]


def test_convert_to_pdfx_supplies_a_named_font_from_a_directory(tmp_path, face):
    (tmp_path / "Corporate Sans.ttf").write_bytes(face)
    with _named_font_doc() as doc:
        remaining = doc.convert_to_pdfx(
            "PDF/X-4", font_lookup_directory=tmp_path
        )
        assert not [e for e in remaining if "embedded" in e]


def test_convert_to_pdfa_still_reports_what_it_could_not_supply():
    with _named_font_doc() as doc:
        remaining = doc.convert_to_pdfa("2b")
        assert any("Corporate Sans" in e and "not embedded" in e for e in remaining)


def test_only_the_named_sources_are_used_when_the_bundled_pass_is_declined():
    """The engine's ``bundled=False``, for a caller who wants nothing supplied for free."""
    with _standard14_doc() as doc:
        report = doc._engine_pdf.embed_missing_fonts(bundled=False)
        assert len(report) == 1
        assert "Helvetica" in report[0]
        _font, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == []


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_sources_must_be_font_substitution_options():
    with _standard14_doc() as doc, pytest.raises(PdfValidationException):
        doc.embed_fonts(sources="/fonts")


def test_naming_the_location_twice_is_refused(face):
    options = FontSubstitutionOptions(fonts={"Corporate Sans": face})
    with _standard14_doc() as doc, pytest.raises(PdfValidationException):
        doc.embed_fonts(sources=options, directory="/fonts")


def test_a_missing_directory_is_simply_empty(tmp_path):
    with _named_font_doc() as doc:
        report = doc.embed_fonts(directory=tmp_path / "nowhere")
        assert len(report) == 1


def test_embed_fonts_on_a_disposed_document_raises():
    doc = _standard14_doc()
    doc.close()
    with pytest.raises(Exception, match=r"(?i)dispos"):
        doc.embed_fonts()


def test_a_document_with_no_fonts_reports_nothing():
    doc = Document()
    doc.pages.add(PageSize.A4)
    with _reopen(doc) as reopened:
        assert reopened.embed_fonts() == []


def test_a_composite_font_under_a_predefined_cmap_is_reported(face):
    """Only under an Identity CMap is a character code the same number as a CID."""
    with _composite_doc(face) as doc:
        font, _descriptor = _only_font(doc)
        _strip_program(doc)
        font.mapping[PdfName("Encoding")] = PdfName("UniGB-UCS2-H")
        report = doc.embed_fonts(
            sources=FontSubstitutionOptions(fonts={"Font": face})
        )
        assert len(report) == 1
        assert "UniGB-UCS2-H" in report[0]
        _font2, descriptor = _only_font(doc)
        assert _program_keys(doc, descriptor) == []


def test_a_composite_font_under_an_embedded_cmap_is_reported(face):
    with _composite_doc(face) as doc:
        engine = doc._engine_pdf
        font, _descriptor = _only_font(doc)
        _strip_program(doc)
        font.mapping[PdfName("Encoding")] = engine._cos_doc.register_object(
            PdfStream(b"%!PS", {PdfName("Length"): PdfNumber(4)})
        )
        report = doc.embed_fonts(
            sources=FontSubstitutionOptions(fonts={"Font": face})
        )
        assert len(report) == 1
        assert "embedded CMap" in report[0]
