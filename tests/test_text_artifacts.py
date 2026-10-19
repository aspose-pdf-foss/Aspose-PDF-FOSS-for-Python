"""Optional Artifact filtering across plain, regional and batch text extraction."""

from __future__ import annotations

import io
import zlib
from contextlib import contextmanager

import pytest

from aspose_pdf import (
    Document,
    PageNumberStamp,
    PdfExtractor,
    PdfLoadLimits,
    PdfResourceLimitException,
    TextStamp,
)
from aspose_pdf.engine.content_stream_parser import ContentStreamParser
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.lowcode import ByteArrayDataSource, TextExtractor, TextExtractorOptions


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _stream(content, entries=b""):
    return (
        b"<< "
        + entries
        + b" /Length %d >>\nstream\n" % len(content)
        + content
        + b"\nendstream"
    )


def _pdf(objects):
    """Build minimal PDFs for nested scopes, forms and malformed content."""
    data = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number, value in sorted(objects.items()):
        offsets[number] = len(data)
        data += b"%d 0 obj\n" % number + value + b"\nendobj\n"
    start = len(data)
    size = max(objects) + 1
    data += b"xref\n0 %d\n" % size
    for number in range(size):
        data += (
            b"%010d 00000 n \n" % offsets[number]
            if number in offsets
            else b"0000000000 65535 f \n"
        )
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        size,
        start,
    )
    return bytes(data)


def _text(word, y=700):
    return f"BT /F1 10 Tf 72 {y} Td ({word}) Tj ET ".encode()


def _objects(content):
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Fm 6 0 R >> "
        b"/Properties << /Logical << /ActualText (Logical replacement) >> "
        b"/Artifact << /ActualText (Named property body) >> >> >> >>",
        3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: _stream(content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: _stream(_text("Form body", 650), b"/Subtype /Form /BBox [0 0 595 842]"),
    }


@contextmanager
def _content_document(content):
    with Document(_pdf(_objects(content))) as document:
        yield document


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_authored_artifacts_and_stamps_are_optional_and_cache_is_not_contaminated(
    source, tmp_path
):
    with Document() as original:
        page = original.pages.add()
        page.add_text("Body", 72, 700)
        page.add_text("Header", 72, 760, artifact="Pagination")
        page.add_text("Footer", 72, 50, artifact=True)
        page.add_text("Untagged header stays", 72, 780)
        original.add_stamp(TextStamp(value="DRAFT"))
        original.add_stamp(PageNumberStamp(value="Page {page}"))
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "source.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(
                path
            )
        try:
            default = document.extract_text()
            assert all(
                word in default
                for word in ("Body", "Header", "Footer", "DRAFT", "Page 1")
            )
            expected = "Body\nUntagged header stays"
            assert document.extract_text(skip_artifacts=True) == expected
            assert document.pages[0].extract_text(skip_artifacts=True) == expected
            assert (
                document.extract_text(pages=[0, 0], skip_artifacts=True)
                == expected + "\n" + expected
            )
            assert document.extract_text(pages=[], skip_artifacts=True) == ""
            assert document.extract_text() == default
            assert document.extract_text(skip_artifacts=False) == default
        finally:
            if document is not original:
                document.dispose()


@pytest.mark.parametrize(
    "opening",
    [
        b"/Artifact BMC ",
        b"/Artifact BDC ",
        b"/#41rtifact BMC ",
        b"/Artifact /Logical BDC ",
        b"/Artifact << /Type /Pagination /Subtype /Header /BBox [0 0 200 40] >> BDC ",
        b"/Artifact << /Nested << /Words (secret metadata) >> /Attached [/Top] >> BDC ",
    ],
)
@pytest.mark.parametrize("clipped", [False, True])
def test_bare_named_inline_and_escaped_artifact_tags_are_recognized(opening, clipped):
    with _content_document(
        _text("Before", 740)
        + opening
        + _text("Artifact", 720)
        + b"EMC "
        + _text("After", 700)
    ) as document:
        clip = (0, 0, 595, 842) if clipped else None
        assert document.extract_text(skip_artifacts=True, clip=clip) == "Before\nAfter"
        assert (
            document.pages[0].extract_text(skip_artifacts=True, clip=clip)
            == "Before\nAfter"
        )


@pytest.mark.parametrize("clipped", [False, True])
def test_nested_marked_scopes_resume_after_the_matching_emc(clipped):
    content = (
        b"/P BMC "
        + _text("Before", 740)
        + b"/Artifact BMC /Span BMC "
        + _text("Secret", 720)
        + b"/Artifact << /Type /Layout >> BDC "
        + _text("Nested", 710)
        + b"EMC EMC EMC "
        + _text("After", 700)
        + b"EMC"
    )
    with _content_document(content) as document:
        clip = (0, 0, 595, 842) if clipped else None
        assert document.extract_text(skip_artifacts=True, clip=clip) == "Before\nAfter"


def test_artifact_words_and_property_names_do_not_classify_ordinary_content():
    content = (
        _text("Literal /Artifact BMC", 740)
        + b"/Span << /Name /Artifact >> BDC "
        + _text("Body", 720)
        + b"EMC "
        + b"/Span /Artifact BDC "
        + _text("Glyphs", 700)
        + b"EMC"
    )
    with _content_document(content) as document:
        assert (
            document.extract_text(skip_artifacts=True)
            == "Literal /Artifact BMC\nBodyNamed property body"
        )


@pytest.mark.parametrize("skip_artifacts", [False, True])
def test_inherited_resources_supply_forms_and_logical_text_in_both_modes(skip_artifacts):
    content = b"/Fm Do /Span /Logical BDC " + _text("Glyphs", 600) + b"EMC"
    with _content_document(content) as document:
        for clip in (None, (0, 0, 595, 842)):
            result = document.extract_text(skip_artifacts=skip_artifacts, clip=clip)
            assert "Form body" in result
            assert "Logical replacement" in result
            assert "Glyphs" not in result


@pytest.mark.parametrize("clipped", [False, True])
def test_forms_inside_artifacts_and_artifacts_inside_nested_forms_are_filtered(clipped):
    objects = _objects(_text("Before", 780) + b"/Fm Do " + _text("After", 500))
    objects[6] = _stream(
        b"/Artifact BMC "
        + _text("Form header", 750)
        + b"EMC "
        + _text("Form body", 700)
        + b"/Nested Do",
        b"/Subtype /Form /BBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> /XObject << /Nested 7 0 R >> >>",
    )
    objects[7] = _stream(
        b"/Artifact << /Type /Pagination >> BDC "
        + _text("Nested footer", 650)
        + b"EMC "
        + _text("Nested body", 600),
        b"/Subtype /Form /BBox [0 0 595 842]",
    )
    clip = (0, 0, 595, 842) if clipped else None
    with Document(_pdf(objects)) as document:
        assert (
            document.extract_text(skip_artifacts=True, clip=clip)
            == "Before\nForm body\nNested body\nAfter"
        )
    objects[4] = _stream(
        _text("Before", 780) + b"/Artifact BMC /Fm Do EMC " + _text("After", 500)
    )
    with Document(_pdf(objects)) as document:
        assert document.extract_text(skip_artifacts=True, clip=clip) == "Before\nAfter"


@pytest.mark.parametrize("clipped", [False, True])
def test_actual_text_inside_an_artifact_does_not_return_at_emc(clipped):
    content = (
        _text("Before", 740)
        + b"/Artifact BMC /Span /Logical BDC "
        + _text("Secret", 720)
        + b"EMC EMC "
        + _text("After", 700)
    )
    with _content_document(content) as document:
        clip = (0, 0, 595, 842) if clipped else None
        assert document.extract_text(skip_artifacts=True, clip=clip) == "Before\nAfter"


@pytest.mark.parametrize("clipped", [False, True])
@pytest.mark.parametrize("form", [False, True])
def test_outer_actual_text_is_not_reintroduced_when_all_its_content_is_artifact(
    clipped, form
):
    objects = _objects(
        b"/Span /Logical BDC "
        + (b"/Fm Do" if form else b"/Artifact BMC " + _text("Secret") + b"EMC")
        + b" EMC"
    )
    objects[6] = _stream(
        b"/Artifact BMC " + _text("Secret") + b"EMC",
        b"/Subtype /Form /BBox [0 0 595 842]",
    )
    with Document(_pdf(objects)) as document:
        clip = (0, 0, 595, 842) if clipped else None
        assert document.extract_text(skip_artifacts=True, clip=clip) == ""


def test_mixed_actual_text_remains_whole_when_real_body_glyphs_are_selected():
    content = (
        b"/Span /Logical BDC /Artifact BMC "
        + _text("Secret", 740)
        + b"EMC "
        + _text("Body", 700)
        + b"EMC"
    )
    with _content_document(content) as document:
        assert document.extract_text(skip_artifacts=True) == "Logical replacement"
        assert (
            document.extract_text(skip_artifacts=True, clip=(0, 690, 595, 720))
            == "Logical replacement"
        )
        assert document.extract_text(skip_artifacts=True, clip=(0, 730, 595, 755)) == ""


def test_logical_replacement_for_a_form_with_mixed_content_is_emitted_once():
    objects = _objects(b"/Span /Logical BDC /Fm Do EMC")
    objects[6] = _stream(
        b"/Artifact BMC " + _text("Secret", 740) + b"EMC " + _text("Body", 700),
        b"/Subtype /Form /BBox [0 0 595 842]",
    )
    with Document(_pdf(objects)) as document:
        for clip in (None, (0, 0, 595, 842)):
            assert (
                document.extract_text(skip_artifacts=True, clip=clip)
                == "Logical replacement"
            )


def test_skipped_text_still_updates_fonts_positions_and_leading():
    objects = _objects(
        b"BT /F1 10 Tf 72 700 Td /Artifact BMC /F2 10 Tf (X) Tj 20 TL T* EMC (A) Tj ET"
    )
    objects[2] = objects[2].replace(b"/F1 5 0 R", b"/F1 5 0 R /F2 7 0 R")
    objects[7] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 8 0 R >>"
    )
    objects[8] = _stream(
        b"begincmap 1 begincodespacerange <00> <FF> endcodespacerange "
        b"1 beginbfchar <41> <005a> endbfchar endcmap"
    )
    with Document(_pdf(objects)) as document:
        assert document.extract_text(skip_artifacts=True) == "Z"
        assert (
            document.extract_text(skip_artifacts=True, clip=(0, 670, 595, 695)) == "Z"
        )
        assert document.extract_text(skip_artifacts=True, clip=(0, 695, 595, 720)) == ""


@pytest.mark.parametrize("valid_artifact_bt", [False, True])
def test_recovery_of_broken_body_does_not_revive_artifact_text(valid_artifact_bt):
    body = _text("Body").replace(b"BT", b"B?", 1)
    artifact = _text("Secret", 740)
    if not valid_artifact_bt:
        artifact = artifact.replace(b"BT", b"B?", 1)
    with _content_document(body + b"/Artifact BMC " + artifact + b"EMC") as document:
        assert document.extract_text(skip_artifacts=True) == "Body"


def test_all_artifact_broken_content_remains_empty_under_recovery():
    with _content_document(
        b"/Artifact BMC B? /F1 12 Tf (Secret) Tj ET EMC"
    ) as document:
        assert document.extract_text(skip_artifacts=True) == ""
        assert "Secret" in document.extract_text()


def test_missing_emc_stays_suppressed_and_extra_emc_does_not_hide_body():
    with _content_document(
        b"EMC " + _text("Before") + b"/Artifact BMC " + _text("Secret", 720)
    ) as document:
        assert document.extract_text(skip_artifacts=True) == "Before"
    with _content_document(b"EMC " + _text("Body")) as document:
        assert document.extract_text(skip_artifacts=True) == "Body"


def test_optional_content_visibility_and_artifact_filter_are_independent():
    with Document() as document:
        page = document.pages.add()
        layer = document.layers.add("Hidden", visible=False)
        page.add_text("Body", 72, 700)
        with page.layer(layer):
            page.add_text("Layer secret", 72, 720)
        page.add_text("Artifact", 72, 740, artifact=True)
        for clip in (None, (0, 0, 595, 842)):
            assert page.extract_text(skip_artifacts=True, clip=clip) == "Body"
        layer.visible = True
        assert page.extract_text(skip_artifacts=True) == "Body\nLayer secret"


def test_hidden_actual_text_and_forms_do_not_bypass_the_filter():
    resources = {
        "Properties": {"Actual": {"ActualText": "Secret replacement"}},
        "XObject": {"Fm": {"Subtype": "Form", "content": _text("Form secret")}},
    }
    parser = ContentStreamParser(
        b"/OC /Hidden BDC /Span /Actual BDC "
        + _text("Hidden")
        + b"EMC /Fm Do EMC "
        + _text("Body"),
        resources,
        hidden_oc_names=frozenset({"Hidden"}),
        skip_artifacts=True,
    )
    assert parser.extract_text() == "Body"


def test_facade_and_lowcode_combine_filtering_page_selection_clip_and_repeated_runs():
    with Document() as document:
        for word in ("First", "Second"):
            page = document.pages.add()
            page.add_text(word, 72, 700)
            page.add_text("Header", 72, 740, artifact=True)
        raw = _bytes(document)
    clip = (0, 680, 595, 760)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw)
        extractor.extract_text(page_numbers=[2, 1, 2], clip=clip, skip_artifacts=True)
        assert extractor.get_text() == "Second\nFirst\nSecond"
        assert extractor.get_next_page_text() == "Second"
        extractor.extract_text(page_numbers=[1])
        assert extractor.get_text() == "First\nHeader"
    options = TextExtractorOptions(
        PdfLoadLimits(), pages=(i for i in [1, 0]), clip=clip, skip_artifacts=True
    )
    options.add_input(ByteArrayDataSource(raw))
    options.add_input(ByteArrayDataSource(raw))
    for _ in range(2):
        assert [result.to_string() for result in TextExtractor().process(options)] == [
            "Second\nFirst"
        ] * 2
    options.skip_artifacts = False
    assert "Header" in TextExtractor().process(options)[0].to_string()


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}, object()])
def test_skip_artifacts_requires_bool_even_on_empty_or_unbound_inputs(value):
    with Document() as document:
        for target in (document, document.pages.add()):
            with pytest.raises(PdfValidationException, match="skip_artifacts"):
                target.extract_text(skip_artifacts=value)
    with PdfExtractor() as extractor:
        with pytest.raises(PdfValidationException, match="skip_artifacts"):
            extractor.extract_text(page_numbers=[], skip_artifacts=value)
    with pytest.raises(PdfValidationException, match="skip_artifacts"):
        TextExtractorOptions(skip_artifacts=value)
    with pytest.raises(PdfValidationException, match="skip_artifacts"):
        ContentStreamParser(b"", {}, skip_artifacts=value)


def test_invalid_flag_does_not_replace_facade_results_or_valid_options():
    with Document() as document:
        document.pages.add().add_text("Body", 72, 700)
        raw = _bytes(document)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw)
        extractor.extract_text()
        with pytest.raises(PdfValidationException):
            extractor.extract_text(skip_artifacts=1)
        assert extractor.get_next_page_text() == "Body"
    options = TextExtractorOptions(skip_artifacts=True)
    with pytest.raises(PdfValidationException):
        options.skip_artifacts = 1
    assert options.skip_artifacts is True


def test_filtered_extraction_does_not_change_pdf_bytes():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Body", 72, 700)
        page.add_text("Header", 72, 740, artifact=True)
        before = _bytes(document)
        document.extract_text(skip_artifacts=True)
        page.extract_text(skip_artifacts=True, clip=(0, 0, 595, 842))
        assert _bytes(document) == before


def test_skipping_artifacts_does_not_bypass_marked_depth_or_token_limits():
    with Document(limits=PdfLoadLimits(max_nesting_depth=3)) as document:
        page = document.pages.add()
        engine = document._engine_pdf
        engine._set_page_content(
            0, b"/Artifact BMC " * 4 + _text("Secret") + b" EMC" * 4
        )
        for clip in (None, (0, 0, 595, 842)):
            with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
                page.extract_text(skip_artifacts=True, clip=clip)
    content = b"/Artifact BMC " + _text("Secret") * 10 + b"EMC"
    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        ContentStreamParser(
            content,
            {},
            limits=PdfLoadLimits(max_content_tokens=10),
            skip_artifacts=True,
        ).extract_text()


def test_filtered_streaming_selection_does_not_decode_other_pages(tmp_path):
    objects = _objects(
        _text("Body") + b"/Artifact BMC " + _text("Header", 740) + b"EMC"
    )
    objects[2] = objects[2].replace(b"[3 0 R] /Count 1", b"[3 0 R 7 0 R] /Count 2")
    objects[7] = b"<< /Type /Page /Parent 2 0 R /Contents 8 0 R >>"
    objects[8] = _stream(zlib.compress(b" " * 20_000), b"/Filter /FlateDecode")
    path = tmp_path / "lazy.pdf"
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:
        assert document.extract_text(pages=[0], skip_artifacts=True) == "Body"
        assert (
            document.extract_text(pages=[0], skip_artifacts=True, clip=(0, 0, 595, 842))
            == "Body"
        )
        assert document._engine_pdf.page_contents == []
        with pytest.raises(PdfResourceLimitException):
            document.extract_text(pages=[1], skip_artifacts=True)


@pytest.mark.parametrize("streaming", [False, True])
def test_filtered_unicode_and_encrypted_pdf_round_trip(tmp_path, streaming):
    path = tmp_path / "encrypted.pdf"
    objects = _objects(
        b"/P /Logical BDC "
        + _text("Glyphs")
        + b"EMC /Artifact BMC "
        + _text("Secret", 740)
        + b"EMC"
    )
    unicode_text = b"<feff" + "Привет".encode("utf-16-be").hex().encode() + b">"
    objects[2] = objects[2].replace(b"(Logical replacement)", unicode_text)
    with Document(_pdf(objects)) as document:
        document.encrypt(user_password="secret", owner_password="owner")
        document.save(path)
    with (Document.open_streaming if streaming else Document)(
        path, password="secret"
    ) as document:
        assert document.extract_text(skip_artifacts=True) == "Привет"
        assert (
            document.extract_text(skip_artifacts=True, clip=(0, 0, 595, 842))
            == "Привет"
        )
        assert "Secret" in document.extract_text()
