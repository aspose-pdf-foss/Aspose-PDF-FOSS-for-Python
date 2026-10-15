"""Region extraction selects positioned glyphs without changing the document."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    PdfExtractor,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.lowcode import ByteArrayDataSource, TextExtractor, TextExtractorOptions


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _content_document(content):
    document = Document()
    document.pages.add().add_text("seed", 72, 700, font_size=10, font_name="Courier")
    resources = document._engine_pdf._resolve_resources_cos(
        document._engine_pdf._get_page_dict(0)
    )
    font = document._engine_pdf._resolve(resources.mapping[PdfName("Font")]).mapping[
        PdfName("F1")
    ]
    font.mapping[PdfName("FirstChar")] = PdfNumber(0)
    font.mapping[PdfName("Widths")] = PdfArray([PdfNumber(600)] * 256)
    document._engine_pdf._set_page_content(0, content)
    return document


@pytest.mark.parametrize("round_trip", [False, True])
def test_selects_part_of_a_string_and_preserves_full_text(round_trip, tmp_path):
    with Document() as document:
        page = document.pages.add()
        page.add_text("ABCDEF", 72, 700, font_size=10, font_name="Courier")
        page.add_text("Other line", 72, 600)
        if round_trip:
            source = tmp_path / "source.pdf"
            document.save(source)
            with Document(source) as reopened:
                assert (
                    reopened.pages[0].extract_text(clip=(78.1, 690, 96, 710)) == "BCD"
                )
        original = page.content
        assert page.extract_text(clip=(78.1, 690, 96, 710)) == "BCD"
        assert page.extract_text(clip=(83, 700, 85, 701)) == "BC"
        assert page.extract_text(clip=(108.1, 690, 120, 710)) == ""
        assert page.extract_text(clip=(0, 0, 1, 1)) == ""
        assert page.extract_text() == "ABCDEF\nOther line"
        assert page.extract_text(clip=None) == page.extract_text()
        assert page.content == original


def test_document_region_does_not_poison_full_text_cache_or_page_cursor():
    with Document() as document:
        for word in ("First", "Second", "Third"):
            page = document.pages.add()
            page.add_text(word, 72, 700)
            page.add_text("Footer", 72, 100)
        expected = "First\nFooter\nSecond\nFooter\nThird\nFooter"
        assert document.extract_text() == expected
        assert document.extract_text(clip=(0, 680, 300, 730)) == "First\nSecond\nThird"
        assert document.extract_text(clip=(0, 200, 1, 201)) == "\n\n"
        assert document.extract_text() == expected
        assert document.pages[1].extract_text(clip=(0, 0, 300, 150)) == "Footer"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_coordinates_stay_in_unrotated_pdf_space(rotation):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Selected", 72, 700)
        page.rotation = rotation
        page.crop_box = (200, 200, 500, 500)
        assert page.extract_text(clip=(70, 690, 200, 720)) == "Selected"
        assert page.rotation == rotation
        assert page.crop_box == (200, 200, 500, 500)


@pytest.mark.parametrize(
    ("content", "clip", "expected"),
    [
        (b"BT /F1 10 Tf 72 700 Td [(AB) -500 (CD)] TJ ET", (89, 690, 101, 710), "CD"),
        (
            b"BT /F1 10 Tf 72 700 Td [(AB) -500 (CD)] TJ ET",
            (70, 690, 110, 710),
            "AB CD",
        ),
        (
            b"BT /F1 10 Tf 12 TL 72 700 Td (AB) Tj (CD) ' 0 0 (EF) \" ET",
            (70, 670, 90, 691),
            "CD\nEF",
        ),
        (b"BT /F1 10 Tf 72 700 Td <41424344> Tj ET", (78, 690, 90, 710), "BC"),
        (b"BT /F1 10 Tf 72 700 Td (A\\(B\\)C) Tj ET", (78, 690, 96, 710), "(B)"),
        (
            b"q 2 0 0 2 100 100 cm BT /F1 10 Tf 10 20 Td (AB) Tj ET Q",
            (132, 135, 144, 158),
            "B",
        ),
        (b"BT /F1 10 Tf 0 1 -1 0 200 100 Tm (AB) Tj ET", (190, 106, 205, 112), "B"),
        (
            b"BT /F1 10 Tf 50 Tz 2 Tc 5 Ts 72 700 Td (ABC) Tj ET",
            (76, 705, 80, 710),
            "B",
        ),
        (
            b"BT /F1 10 Tf 72 700 Td (AB) Tj 100 0 Td (Excluded) Tj -100 -20 Td (CD) Tj ET",
            (70, 675, 90, 710),
            "AB\nCD",
        ),
        (b"BT /F1 10 Tf 72 700 Td (  ) Tj ET", (70, 690, 90, 710), "  "),
    ],
)
def test_positioning_and_encoded_operands(content, clip, expected):
    with _content_document(content) as document:
        assert document.pages[0].extract_text(clip=clip) == expected


def test_embedded_unicode_code_units_are_selected_whole():
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    with Document() as document:
        page = document.pages.add()
        page.add_text(
            "café Ångström",
            72,
            700,
            font_size=12,
            font=load_substitute_sfnt("sans-regular"),
        )
        with Document(io.BytesIO(_bytes(document))) as reopened:
            page = reopened.pages[0]
            assert page.extract_text(clip=(0, 680, 300, 730)) == "café Ångström"
            selected = page.extract_text(clip=(72, 690, 80, 715))
            assert selected and "café Ångström".startswith(selected)
            assert len(selected) < len("café Ångström")
            assert "\ufffd" not in selected


def test_actual_text_is_included_only_when_its_underlying_glyphs_are_selected():
    with _content_document(
        b"/Span /Logical BDC BT /F1 10 Tf 72 700 Td (ABC) Tj ET EMC "
        b"/Span /Excluded BDC BT /F1 10 Tf 72 600 Td (Outside) Tj ET EMC"
    ) as document:
        page = document.pages[0]
        resources = document._engine_pdf._resolve_resources_cos(
            document._engine_pdf._get_page_dict(0)
        )
        resources.mapping[PdfName("Properties")] = PdfDictionary(
            {
                PdfName("Logical"): PdfDictionary(
                    {PdfName("ActualText"): PdfString(b"Logical text")}
                ),
                PdfName("Excluded"): PdfDictionary(
                    {PdfName("ActualText"): PdfString(b"Excluded replacement")}
                ),
            }
        )
        assert page.extract_text(clip=(78, 690, 84, 710)) == "Logical text"
        assert page.extract_text(clip=(0, 0, 1, 1)) == ""
        assert page.extract_text(clip=(70, 690, 200, 720)) == "Logical text"


def test_simple_font_to_unicode_ligatures_are_not_split():
    with _content_document(b"BT /F1 10 Tf 72 700 Td (ABC) Tj ET") as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        font = engine._resolve(resources.mapping[PdfName("Font")]).mapping[
            PdfName("F1")
        ]
        font.mapping[PdfName("ToUnicode")] = PdfStream(
            b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
            b"3 beginbfchar <41> <0041> <42> <006600660069> <43> <0043> endbfchar endcmap",
            {},
        )
        assert document.pages[0].extract_text(clip=(79, 690, 83, 710)) == "ffi"


def test_decoding_uses_the_font_restored_by_graphics_state():
    with _content_document(
        b"BT /F1 10 Tf 72 700 Td q /F2 10 Tf (A) Tj Q (A) Tj ET"
    ) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        fonts = engine._resolve(resources.mapping[PdfName("Font")])
        original = fonts.mapping[PdfName("F1")]
        other = PdfDictionary(dict(original.mapping))
        fonts.mapping[PdfName("F2")] = other
        for font, text in ((original, "First"), (other, "Second")):
            font.mapping[PdfName("ToUnicode")] = PdfStream(
                b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
                b"1 beginbfchar <41> <"
                + text.encode("utf-16-be").hex().encode()
                + b"> endbfchar endcmap",
                {},
            )
        assert document.pages[0].extract_text(clip=(0, 680, 300, 730)) == "SecondFirst"
        assert document.pages[0].extract_text(clip=(79, 690, 83, 710)) == "First"


def test_hidden_layers_are_omitted_even_when_every_glyph_overlaps():
    with Document() as document:
        page = document.pages.add()
        draft = document.layers.add("Draft", visible=False)
        with page.layer(draft):
            page.add_text("Hidden", 72, 700)
        assert page.extract_text(clip=(0, 0, 500, 800)) == ""
        page.add_text("Visible", 72, 600)
        assert page.extract_text(clip=(0, 0, 500, 800)) == "Visible"
        document.layers["Draft"].visible = True
        assert page.extract_text(clip=(0, 680, 500, 730)) == "Hidden"


def _add_form(document, name, content, *, matrix=None, resources=None):
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    page_resources = engine._resolve_resources_cos(page)
    if resources is None:
        resources = PdfDictionary(
            {PdfName("Font"): page_resources.mapping[PdfName("Font")]}
        )
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(n) for n in (0, 0, 500, 800)]),
        PdfName("Resources"): resources,
    }
    if matrix:
        mapping[PdfName("Matrix")] = PdfArray([PdfNumber(n) for n in matrix])
    form = PdfStream(content, mapping)
    reference = engine._cos_doc.register_object(form)
    xobjects = engine._resolve(page_resources.mapping.get(PdfName("XObject")))
    if not isinstance(xobjects, PdfDictionary):
        xobjects = PdfDictionary({})
        page_resources.mapping[PdfName("XObject")] = xobjects
    xobjects.mapping[PdfName(name)] = reference
    return form, reference


def test_forms_use_each_placement_matrix_and_their_own_font_scope():
    with _content_document(b"q /Fm Do Q q 1 0 0 1 100 0 cm /Fm Do Q") as document:
        font = PdfDictionary(
            {
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Courier"),
            }
        )
        font.mapping[PdfName("FirstChar")] = PdfNumber(0)
        font.mapping[PdfName("Widths")] = PdfArray([PdfNumber(600)] * 256)
        resources = PdfDictionary(
            {PdfName("Font"): PdfDictionary({PdfName("Local"): font})}
        )
        _add_form(
            document,
            "Fm",
            b"BT /Local 10 Tf 0 0 Td (AB) Tj ET",
            matrix=(2, 0, 0, 2, 72, 700),
            resources=resources,
        )
        with Document(io.BytesIO(_bytes(document))) as reopened:
            page = reopened.pages[0]
            assert page.extract_text(clip=(84, 690, 96, 720)) == "B"
            assert page.extract_text(clip=(184, 690, 196, 720)) == "B"
            assert page.extract_text(clip=(70, 690, 200, 720)) == "AB\nAB"


def test_nested_and_recursive_forms_terminate_without_losing_text():
    with _content_document(b"/Outer Do") as document:
        inner, inner_ref = _add_form(
            document, "Inner", b"BT /F1 10 Tf 0 0 Td (ABC) Tj ET /Again Do"
        )
        inner.mapping[PdfName("Resources")].mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Again"): inner_ref}
        )
        outer, _ = _add_form(document, "Outer", b"q 1 0 0 1 72 700 cm /Child Do Q")
        outer.mapping[PdfName("Resources")].mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Child"): inner_ref}
        )
        assert document.pages[0].extract_text(clip=(78, 690, 84, 710)) == "B"


def test_forms_resolve_hidden_properties_in_their_own_resources():
    with _content_document(b"/Outer Do /HiddenForm Do") as document:
        draft = document.layers.add("Draft", visible=False)
        hidden = PdfIndirectReference(draft.object_number, 0)
        outer, _ = _add_form(
            document,
            "Outer",
            b"/OC /LocalLayer BDC BT /F1 10 Tf 72 700 Td (Hidden) Tj ET EMC "
            b"BT /F1 10 Tf 72 600 Td (Visible) Tj ET",
        )
        outer.mapping[PdfName("Resources")].mapping[PdfName("Properties")] = (
            PdfDictionary({PdfName("LocalLayer"): hidden})
        )
        hidden_form, _ = _add_form(
            document, "HiddenForm", b"BT /F1 10 Tf 72 700 Td (Secret) Tj ET"
        )
        hidden_form.mapping[PdfName("OC")] = hidden
        assert document.pages[0].extract_text(clip=(0, 0, 500, 800)) == "Visible"
        document.layers["Draft"].visible = True
        assert (
            document.pages[0].extract_text(clip=(0, 680, 500, 730)) == "Hidden\nSecret"
        )


def test_regions_allow_negative_coordinates_and_normalize_lists():
    with _content_document(b"BT /F1 10 Tf -20 -20 Td (AB) Tj ET") as document:
        assert document.pages[0].extract_text(clip=[-20, -25, -14, -10]) == "A"
        assert document.extract_text(clip=[-20, -25, -14, -10]) == "A"


def test_inline_image_samples_cannot_change_text_geometry():
    samples = b"BT /F1 10 Tf 1 0 0 1 1000 0 cm (Fake) Tj ET "
    image = (
        b"BI /W "
        + str(len(samples)).encode()
        + b" /H 1 /BPC 8 /CS /G ID "
        + samples
        + b" EI "
    )
    with _content_document(image + b"BT /F1 10 Tf 72 700 Td (ABC) Tj ET") as document:
        assert document.pages[0].extract_text(clip=(70, 690, 100, 720)) == "ABC"


@pytest.mark.parametrize(
    "content",
    [
        b"BT /Unknown 10 Tf 72 700 Td (Unplaced) Tj ET",
        b"(Loose string) % No text operator",
        b"BT /F1 10 Tf 72 700 Td <FEFF0041> Tj ET",
    ],
)
def test_unplaceable_text_never_uses_a_raw_recovery_fallback(content):
    with _content_document(content) as document:
        assert document.pages[0].extract_text(clip=(0, 0, 1000, 1000)) == ""


def test_facade_and_lowcode_expose_the_same_region_option():
    with Document() as document:
        for text in ("One", "Two"):
            page = document.pages.add()
            page.add_text(text, 72, 700)
            page.add_text("Footer", 72, 100)
        data = _bytes(document)
    clip = (0, 680, 300, 730)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(data)
        extractor.extract_text(clip=clip)
        assert extractor.get_text() == "One\nTwo"
        assert extractor.get_next_page_text() == "One"
        assert extractor.get_next_page_text() == "Two"
        extractor.extract_text()
        assert extractor.get_text() == "One\nFooter\nTwo\nFooter"
    options = TextExtractorOptions(PdfLoadLimits(), clip=clip)
    options.add_input(ByteArrayDataSource(data))
    assert TextExtractor().process(options)[0].to_string() == "One\nTwo"


@pytest.mark.parametrize(
    "clip",
    [
        (),
        (1, 2, 3),
        (1, 2, 3, 4, 5),
        "1234",
        b"1234",
        4,
        (0, 0, 0, 1),
        (1, 0, 0, 1),
        (0, 2, 1, 1),
        (0, 0, float("nan"), 1),
        (0, 0, 1, float("inf")),
        (False, 0, 1, 1),
        (None, 0, 1, 1),
        (object(), 0, 1, 1),
    ],
)
def test_invalid_rectangles_are_rejected_consistently(clip):
    with Document() as document:
        page = document.pages.add()
        for target in (page, document):
            with pytest.raises(PdfValidationException, match="clip"):
                target.extract_text(clip=clip)
    with PdfExtractor() as extractor:
        with pytest.raises(PdfValidationException, match="clip"):
            extractor.extract_text(clip=clip)
    with pytest.raises(PdfValidationException, match="clip"):
        TextExtractorOptions(clip=clip)


def test_empty_and_disposed_documents():
    document = Document()
    assert document.extract_text(clip=(0, 0, 1, 1)) == ""
    page = document.pages.add()
    assert page.extract_text(clip=(0, 0, 1, 1)) == ""
    document.dispose()
    for target in (document, page):
        with pytest.raises(AsposePdfException, match="disposed"):
            target.extract_text(clip=(0, 0, 1, 1))


def test_region_extraction_propagates_resource_limits():
    with Document(limits=PdfLoadLimits(max_container_items=30)) as document:
        document.pages.add().add_text("A" * 40, 72, 700)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.extract_text(clip=(0, 0, 500, 800))


def test_marked_content_depth_is_bounded():
    with Document(limits=PdfLoadLimits(max_nesting_depth=20)) as document:
        document.pages.add().add_text("Text", 72, 700)
        engine = document._engine_pdf
        engine._set_page_content(
            0, b"/Span BMC " * 21 + engine.get_page_content(0) + b" EMC" * 21
        )
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.extract_text(clip=(0, 0, 500, 800))


def test_streaming_page_selection_reads_only_the_requested_page(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    with Document() as document:
        for text in ("First", "Second", "Third"):
            document.pages.add().add_text(text, 72, 700)
        document.save(source)
    with Document.open_streaming(source) as document:
        engine = document._engine_pdf
        original = engine.get_page_content
        accessed = []

        def selected_only(index):
            assert index == 1
            accessed.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", selected_only)
        assert document.pages[1].extract_text(clip=(0, 680, 300, 730)) == "Second"
        assert accessed == [1]
        assert engine.page_contents == []


def test_uninvoked_forms_and_images_are_not_decoded(monkeypatch):
    with _content_document(b"BT /F1 10 Tf 72 700 Td (ABC) Tj ET /Photo Do") as document:
        form, _ = _add_form(
            document, "Unused", b"not compressed", resources=PdfDictionary({})
        )
        form.mapping[PdfName("Filter")] = PdfName("FlateDecode")
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        photo = PdfStream(b"not image data", {PdfName("Subtype"): PdfName("Image")})
        resources.mapping[PdfName("XObject")].mapping[PdfName("Photo")] = photo

        def forbidden(*args, **kwargs):
            pytest.fail("Region extraction decoded an unrelated form or image")

        monkeypatch.setattr(engine, "_decode_cos_stream", forbidden)
        assert document.pages[0].extract_text(clip=(70, 690, 100, 720)) == "ABC"
