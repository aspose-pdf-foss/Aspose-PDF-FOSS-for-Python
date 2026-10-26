"""Rectangular exclusions remove positioned text without changing source PDFs."""

from __future__ import annotations

import io
from itertools import repeat

import pytest

from aspose_pdf import Document, PdfExtractor, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)

HEADER = (0, 740, 600, 800)
FOOTER = (0, 0, 600, 80)


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _report(document, body="Body"):
    page = document.pages.add()
    page.add_text("Header", 72, 760)
    page.add_text(body, 72, 700)
    page.add_text("Footer", 72, 50)
    return page


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_untagged_headers_and_footers_are_excluded_without_changing_pdf(source, tmp_path):
    with Document() as original:
        for body in ("First", "Second", "Third"):
            _report(original, body)
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "report.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(path)
        try:
            assert document.extract_text(exclude_regions=[HEADER, FOOTER]) == "First\nSecond\nThird"
            assert document.extract_text(
                pages=[2, 0, 2], exclude_regions=(box for box in [HEADER, FOOTER]),
            ) == "Third\nFirst\nThird"
            assert document.pages[1].extract_text(exclude_regions=[HEADER, FOOTER]) == "Second"
            assert document.extract_text().count("Header") == 3
            assert document.extract_text().count("Footer") == 3
            assert _bytes(document) == raw
        finally:
            if document is not original:
                document.dispose()


def test_exclusions_combine_with_clip_and_artifact_filtering():
    with Document() as document:
        page = _report(document)
        page.add_text("DRAFT", 72, 680, artifact=True)
        options = dict(exclude_regions=[HEADER], clip=(0, 650, 600, 800))
        assert page.extract_text(**options) == "Body\nDRAFT"
        assert document.extract_text(**options, skip_artifacts=True) == "Body"
        assert page.extract_text(clip=(0, 740, 600, 800), exclude_regions=[HEADER]) == ""


@pytest.mark.parametrize("exclude_regions", [None, [], ()])
def test_no_exclusions_preserve_default_text_and_cached_result(exclude_regions, monkeypatch):
    with Document() as document:
        _report(document)
        expected = document.extract_text()

        def forbidden(*args, **kwargs):
            pytest.fail("Empty exclusions must retain the ordinary full-text cache")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", forbidden)
        assert document.extract_text(exclude_regions=exclude_regions) == expected
        assert document.extract_text(exclude_regions=iter(())) == expected


def test_empty_filtered_pages_keep_separators_and_do_not_reset_engine_cursor():
    with Document() as document:
        _report(document, "First")
        _report(document, "Second")
        full = document.extract_text()
        engine = document._engine_pdf
        first = engine.get_next_page_text()
        assert "First" in first
        assert document.extract_text(exclude_regions=[(-100, -100, 1000, 1000)]) == "\n"
        assert document.extract_text(pages=[], exclude_regions=[HEADER]) == ""
        assert "Second" in engine.get_next_page_text()
        assert document.extract_text() == full


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_coordinates_are_unrotated_and_independent_of_crop_box(rotation):
    with Document() as document:
        page = _report(document)
        page.rotation = rotation
        page.crop_box = (200, 200, 500, 500)
        assert page.extract_text(exclude_regions=[HEADER, FOOTER]) == "Body"
        assert page.rotation == rotation
        assert page.crop_box == (200, 200, 500, 500)


def test_partial_string_and_shared_edges_preserve_code_units_and_word_gaps():
    with Document() as document:
        page = document.pages.add()
        page.add_text("ABCDEF", 72, 700, font_size=10, font_name="Courier")
        assert page.extract_text(exclude_regions=[(85, 690, 95, 720)]) == "AB EF"
        right = document.extract_words()[0].rect[2]
        assert page.extract_text(exclude_regions=[(right, 690, right + 12, 720)]) == "ABCDEF"
        assert page.extract_text(exclude_regions=[(83, 700, 85, 701)]) == "A DEF"
        assert page.extract_text(exclude_regions=[(72, 690, 77, 720)]) == "BCDEF"


@pytest.mark.parametrize("content", [
    b"BT /F1 10 Tf 72 700 Td (AB) Tj (CD) Tj (EF) Tj ET",
    b"BT /F1 10 Tf 72 700 Td [(AB) (CD) (EF)] TJ ET",
    b"BT /F1 10 Tf 72 700 Td <414243444546> Tj ET",
])
def test_excluded_middle_operands_do_not_merge_remaining_words(content):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        document._engine_pdf._set_page_content(0, content)
        assert page.extract_text(exclude_regions=[(85, 690, 95, 720)]) == "AB EF"


def test_embedded_unicode_text_has_no_partial_cids_or_replacement_characters():
    with Document() as document:
        page = document.pages.add()
        page.add_text("café Ångström", 72, 700, font_size=12, font=load_substitute_sfnt("sans-regular"))
        with Document(_bytes(document)) as reopened:
            text = reopened.extract_text(exclude_regions=[(72, 690, 80, 715)])
            assert text and text != "café Ångström"
            assert "\ufffd" not in text and "\x00" not in text
            assert "Ångström" in text


def test_actual_text_is_omitted_whole_when_any_glyph_is_excluded():
    with Document() as document:
        page = document.pages.add()
        page.add_text("ABCDEF", 72, 700, font_size=10, font_name="Courier")
        engine = document._engine_pdf
        name = engine._register_actual_text_property(0, "Logical replacement")
        engine._set_page_content(0, f"/Span /{name} BDC ".encode() + page.content + b" EMC")
        page.add_text("Body", 72, 600)
        assert page.extract_text(exclude_regions=[(85, 690, 95, 720)]) == "Body"
        assert page.extract_text(exclude_regions=[FOOTER]) == "Logical replacement\nBody"


def test_hidden_optional_content_stays_hidden():
    with Document() as document:
        page = _report(document)
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.add_text("Hidden", 72, 650)
        assert page.extract_text(exclude_regions=[HEADER, FOOTER]) == "Body"
        layer.visible = True
        assert page.extract_text(exclude_regions=[HEADER, FOOTER]) == "Body\nHidden"


def test_facade_selection_cursor_and_failed_validation_remain_atomic():
    with Document() as document:
        _report(document, "First")
        _report(document, "Second")
        raw = _bytes(document)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw)
        extractor.extract_text(page_numbers=[2, 1, 2], exclude_regions=iter([HEADER, FOOTER]))
        assert extractor.get_text() == "Second\nFirst\nSecond"
        assert extractor.get_next_page_text() == "Second"
        with pytest.raises(PdfValidationException, match="exclude_regions"):
            extractor.extract_text(exclude_regions=[HEADER, (0, 0, 0, 1)])
        assert extractor.get_text() == "Second\nFirst\nSecond"
        assert extractor.get_next_page_text() == "First"


def test_lowcode_snapshots_generators_and_mutable_rectangles_for_multiple_inputs():
    with Document() as document:
        _report(document)
        raw = _bytes(document)
    regions = [list(HEADER), list(FOOTER)]
    options = TextExtractorOptions(exclude_regions=(region for region in regions))
    regions[0][:] = [0, 0, 1, 1]
    regions.clear()
    assert options.exclude_regions == (HEADER, FOOTER)
    for _ in range(2):
        options.add_input(ByteArrayDataSource(raw))
    processor = TextExtractor()
    for _ in range(2):
        assert [result.to_string() for result in processor.process(options)] == ["Body", "Body"]
    options.exclude_regions = [FOOTER]
    with pytest.raises(PdfValidationException):
        options.exclude_regions = [(0, 0, 0, 1)]
    assert options.exclude_regions == (FOOTER,)
    options.exclude_regions = None
    assert options.exclude_regions == ()


@pytest.mark.parametrize("regions", [
    4, True, "1234", b"1234", {"box": HEADER}, [0, 0, 1, 1],
    [(0, 0, 1)], [(0, 0, 1, 1, 2)], [(0, 0, 0, 1)], [(1, 0, 0, 1)],
    [(0, 0, float("nan"), 1)], [(0, 0, 1, float("inf"))], [(False, 0, 1, 1)],
    [HEADER, None], [HEADER, (None, 0, 1, 1)],
])
def test_invalid_exclusions_are_rejected_even_with_no_pages_and_before_decoding(regions, monkeypatch):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="exclude_regions"):
            document.extract_text(pages=[], exclude_regions=regions)
        page = _report(document)
        monkeypatch.setattr(document._engine_pdf, "get_page_content", lambda *a: pytest.fail("Validate first"))
        for target in (page, document):
            with pytest.raises(PdfValidationException, match="exclude_regions"):
                target.extract_text(exclude_regions=regions)
    with PdfExtractor() as extractor:
        with pytest.raises(PdfValidationException, match="exclude_regions"):
            extractor.extract_text(exclude_regions=regions)
    with pytest.raises(PdfValidationException, match="exclude_regions"):
        TextExtractorOptions(exclude_regions=regions)


def test_exclusion_count_is_bounded_including_unending_generators(monkeypatch):
    limits = PdfLoadLimits(max_container_items=20)
    with Document(limits=limits) as document:
        page = document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "get_page_content", lambda *a: pytest.fail("Validate first"))
        for target in (document, page):
            with pytest.raises(PdfResourceLimitException, match="text exclusion rectangles"):
                target.extract_text(exclude_regions=repeat(HEADER))
    with pytest.raises(PdfResourceLimitException, match="text exclusion rectangles"):
        TextExtractorOptions(limits, exclude_regions=repeat(HEADER))


def test_streaming_selection_only_decodes_selected_pages(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    with Document() as document:
        for body in ("First", "Second", "Third"):
            _report(document, body)
        document.save(path)
    with Document.open_streaming(path) as document:
        engine = document._engine_pdf
        read = engine.get_page_content
        visited = []

        def selected(index):
            assert index == 1
            visited.append(index)
            return read(index)

        monkeypatch.setattr(engine, "get_page_content", selected)
        assert document.extract_text(pages=[1, 1], exclude_regions=[HEADER, FOOTER]) == "Second\nSecond"
        assert visited == [1, 1] and engine.page_contents == []
        assert engine._extracted_text is None


def test_disposed_document_and_page_reject_extraction():
    document = Document()
    page = document.pages.add()
    document.dispose()
    for target in (document, page):
        with pytest.raises(AsposePdfException, match="disposed"):
            target.extract_text(exclude_regions=[HEADER])


def _form(document, content, *, matrix=(1, 0, 0, 1, 0, 0), resources=None):
    engine = document._engine_pdf
    page_resources = engine._resolve_resources_cos(engine._get_page_dict(0))
    if resources is None:
        resources = PdfDictionary({
            name: value for name, value in page_resources.mapping.items()
            if name in (PdfName("Font"), PdfName("Properties"))
        })
    return engine._cos_doc.register_object(PdfStream(content, {
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(value) for value in (0, 0, 500, 800)]),
        PdfName("Matrix"): PdfArray([PdfNumber(value) for value in matrix]),
        PdfName("Resources"): resources,
    }))


@pytest.mark.parametrize("round_trip", [False, True])
def test_nested_repeated_forms_use_global_coordinates_and_local_font_scope(round_trip):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        font = PdfDictionary({
            PdfName("Subtype"): PdfName("Type1"), PdfName("BaseFont"): PdfName("Courier"),
            PdfName("FirstChar"): PdfNumber(0),
            PdfName("Widths"): PdfArray([PdfNumber(600)] * 256),
        })
        local = PdfDictionary({PdfName("Font"): PdfDictionary({PdfName("Local"): font})})
        inner = _form(document, b"BT /Local 10 Tf 0 0 Td (ABCDEF) Tj ET", resources=local)
        resources = PdfDictionary({PdfName("XObject"): PdfDictionary({PdfName("Inner"): inner})})
        outer = _form(document, b"/Inner Do", matrix=(2, 0, 0, 2, 72, 700), resources=resources)
        engine = document._engine_pdf
        engine._resolve_resources_cos(engine._get_page_dict(0)).mapping[PdfName("XObject")] = PdfDictionary({
            PdfName("Outer"): outer,
        })
        engine._set_page_content(0, b"/Outer Do q 1 0 0 1 100 0 cm /Outer Do Q")
        options = dict(exclude_regions=[(97, 690, 119, 725)])
        assert page.extract_text(**options) == "AB EF\nABCDEF"
        if round_trip:
            with Document(_bytes(document)) as reopened:
                assert reopened.extract_text(**options) == "AB EF\nABCDEF"


@pytest.mark.parametrize("selected_form", [False, True])
def test_form_exclusions_suppress_an_enclosing_actual_text_even_if_form_has_no_remaining_text(selected_form):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Visible", 72, 600)
        visible = page.content
        engine = document._engine_pdf
        name = engine._register_actual_text_property(0, "Do not leak logical text")
        content = b"BT /F1 12 Tf 72 760 Td (Excluded) Tj ET"
        if selected_form:
            content += b" BT /F1 12 Tf 72 700 Td (Included) Tj ET"
        inner = _form(document, content)
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("XObject")] = PdfDictionary({PdfName("Inner"): inner})
        engine._set_page_content(0, f"/Span /{name} BDC ".encode() + b"/Inner Do EMC " + visible)
        assert page.extract_text(exclude_regions=[HEADER]) == "Visible"


def test_nested_actual_text_is_indivisible_but_hidden_and_skipped_glyphs_do_not_suppress_it():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Body", 72, 700)
        body = page.content
        engine = document._engine_pdf
        outer = engine._register_actual_text_property(0, "Outer replacement")
        inner = engine._register_actual_text_property(0, "Inner replacement")
        engine._set_page_content(0, (
            f"/Span /{outer} BDC /P BMC /Span /{inner} BDC ".encode()
            + b"BT /F1 12 Tf 72 760 Td (Hidden) Tj ET EMC EMC "
            + body + b" EMC"
        ))
        assert page.extract_text(exclude_regions=[HEADER]) == ""
        engine._set_page_content(0, (
            f"/Span /{outer} BDC /Artifact BMC ".encode()
            + b"BT /F1 12 Tf 72 760 Td (Artifact) Tj ET EMC "
            + body + b" EMC"
        ))
        assert page.extract_text(exclude_regions=[HEADER], skip_artifacts=True) == "Outer replacement"


@pytest.mark.parametrize("spacing,expected", [
    (b"(AB CD EF)", "AB  EF"),
    (b"[(AB ) (CD) ( EF)]", "AB  EF"),
    (b"[(AB) -500 (CD) -500 (EF)]", "AB EF"),
])
def test_exclusion_gaps_preserve_retained_spaces_and_tj_separators(spacing, expected):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        operator = b"TJ" if spacing.startswith(b"[") else b"Tj"
        document._engine_pdf._set_page_content(0, b"BT /F1 10 Tf 72 700 Td " + spacing + b" " + operator + b" ET")
        # Spaces or a TJ adjustment put the middle letters near x=91..103.
        assert page.extract_text(exclude_regions=[(92, 690, 101, 720)]) == expected


def test_exclusion_comparison_budget_is_shared_with_invoked_forms():
    with Document(limits=PdfLoadLimits(max_content_tokens=110)) as document:
        page = document.pages.add()
        page.add_text("A", 72, 700)
        text = page.content
        engine = document._engine_pdf
        form = _form(document, text)
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("XObject")] = PdfDictionary({PdfName("Form"): form})
        engine._set_page_content(0, b"/Form Do " * 6)
        regions = [(0, 0, 1, 1)] * 20
        with pytest.raises(PdfResourceLimitException, match="text exclusion comparisons"):
            page.extract_text(exclude_regions=regions)


def test_unmeasurable_text_never_recovers_raw_strings_with_nonempty_exclusions():
    with Document() as document:
        page = document.pages.add()
        document._engine_pdf._set_page_content(0, b"BT /Missing 12 Tf 72 700 Td (Unplaced) Tj ET")
        assert page.extract_text(exclude_regions=[FOOTER]) == ""


def test_forms_inside_artifacts_are_not_decoded(monkeypatch):
    with Document() as document:
        page = _report(document)
        engine = document._engine_pdf
        form = _form(document, b"not compressed")
        stream = engine._resolve(form)
        stream.mapping[PdfName("Filter")] = PdfName("FlateDecode")
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("XObject")] = PdfDictionary({PdfName("Form"): form})
        engine._set_page_content(0, page.content + b" /Artifact BMC /Form Do EMC")
        monkeypatch.setattr(engine, "_decode_cos_stream", lambda *a: pytest.fail("Do not decode artifacts"))
        assert page.extract_text(exclude_regions=[HEADER, FOOTER], skip_artifacts=True) == "Body"


def test_exclusion_keeps_ligatures_indivisible():
    with Document() as document:
        page = document.pages.add()
        page.add_text("ABC", 72, 700, font_size=10, font_name="Courier")
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        font = engine._resolve(resources.mapping[PdfName("Font")]).mapping[PdfName("F1")]
        font.mapping[PdfName("ToUnicode")] = PdfStream(
            b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
            b"3 beginbfchar <41> <0041> <42> <006600660069> <43> <0043> endbfchar endcmap", {},
        )
        assert page.extract_text(exclude_regions=[FOOTER]) == "AffiC"
        assert page.extract_text(exclude_regions=[(79, 690, 83, 720)]) == "A C"


@pytest.mark.parametrize("content,region,expected", [
    (b"BT /F1 10 Tf 0 1 -1 0 200 100 Tm (ABC) Tj ET", (190, 107, 205, 111), "A C"),
    (b"q 2 0 0 2 100 100 cm BT /F1 10 Tf 10 20 Td (ABC) Tj ET Q", (133, 135, 143, 158), "A C"),
    (b"BT /F1 10 Tf 12 TL 72 700 Td (AB) Tj (CD) ' 0 0 (EF) \" ET", (70, 686, 90, 697), "AB\nEF"),
])
def test_transformed_text_and_quote_operators_follow_glyph_geometry(content, region, expected):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        document._engine_pdf._set_page_content(0, content)
        assert page.extract_text(exclude_regions=[region]) == expected


def test_streaming_encrypted_pdf_retains_source_and_only_reads_selected_pages(tmp_path, monkeypatch):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        for body in ("First", "Second", "Third"):
            _report(document, body)
        document.encrypt("user", "owner")
        document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(path, password="user") as document:
        engine = document._engine_pdf
        read = engine.get_page_content

        def selected(index):
            assert index == 1
            return read(index)

        monkeypatch.setattr(engine, "get_page_content", selected)
        assert document.extract_text(pages=[1, 1], exclude_regions=[HEADER, FOOTER]) == "Second\nSecond"
        assert document.is_encrypted and engine.page_contents == []
        assert engine._extracted_text is None
    assert path.read_bytes() == original


def test_facade_preserves_previous_results_and_cursor_after_exclusion_resource_failure():
    with Document() as document:
        document.pages.add().add_text("A", 72, 700)
        document.pages.add().add_text("BBBBBB", 72, 700)
        raw = _bytes(document)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw, limits=PdfLoadLimits(max_content_tokens=500))
        extractor.extract_text()
        assert extractor.get_next_page_text() == "A"
        with pytest.raises(PdfResourceLimitException, match="text exclusion comparisons"):
            extractor.extract_text(exclude_regions=[(0, 0, 1, 1)] * 100)
        assert extractor.get_text() == "A\nBBBBBB"
        assert extractor.get_next_page_text() == "BBBBBB"


def test_lowcode_does_not_emit_partial_output_when_a_later_input_exceeds_exclusion_work():
    options = TextExtractorOptions(
        PdfLoadLimits(max_content_tokens=500), exclude_regions=[(0, 0, 1, 1)] * 100,
    )
    for text in ("A", "BBBBBB"):
        with Document() as document:
            document.pages.add().add_text(text, 72, 700)
            options.add_input(ByteArrayDataSource(_bytes(document)))
    with io.BytesIO(b"unchanged") as output:
        options.add_output(StreamDataSource(output))
        with pytest.raises(PdfResourceLimitException, match="text exclusion comparisons"):
            TextExtractor().process(options)
        assert output.getvalue() == b"unchanged" and not output.closed


def test_lowcode_revalidates_exclusion_count_if_resource_policy_changes():
    options = TextExtractorOptions(exclude_regions=[FOOTER] * 5)
    options.limits = PdfLoadLimits(max_container_items=4)
    options.add_input(ByteArrayDataSource(b"must not read this input"))
    with pytest.raises(PdfResourceLimitException, match="text exclusion rectangles"):
        TextExtractor().process(options)


def test_uninvoked_forms_and_image_streams_are_not_decoded(monkeypatch):
    with Document() as document:
        page = _report(document)
        engine = document._engine_pdf
        unused = _form(document, b"not compressed")
        engine._resolve(unused).mapping[PdfName("Filter")] = PdfName("FlateDecode")
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("XObject")] = PdfDictionary({
            PdfName("Unused"): unused,
            PdfName("Photo"): PdfStream(b"not image data", {PdfName("Subtype"): PdfName("Image")}),
        })
        engine._set_page_content(0, page.content + b" /Photo Do")
        monkeypatch.setattr(engine, "_decode_cos_stream", lambda *a: pytest.fail("Do not decode unrelated streams"))
        assert page.extract_text(exclude_regions=[HEADER, FOOTER]) == "Body"


@pytest.mark.parametrize("content", [
    b"BT /F1 10 Tf 72 700 Td (AB) Tj () Tj (CD) Tj ET",
    b"BT /F1 10 Tf 72 700 Td [(AB) <> (CD)] TJ ET",
])
def test_empty_show_operands_do_not_create_artificial_word_boundaries(content):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        document._engine_pdf._set_page_content(0, content)
        assert page.extract_text(exclude_regions=[FOOTER]) == "ABCD"


@pytest.mark.parametrize("regions", [None, []])
def test_legacy_clip_spacing_is_preserved_when_exclusions_are_empty(regions):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700, font_size=10, font_name="Courier")
        document._engine_pdf._set_page_content(0, b"BT /F1 10 Tf 72 700 Td [(AB) -500 ( EF)] TJ ET")
        assert page.extract_text(clip=(0, 0, 600, 800), exclude_regions=regions) == "AB  EF"
