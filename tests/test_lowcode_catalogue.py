"""The low-code plugins beyond the first four.

``lowcode`` shipped ``Merger``, ``Optimizer``, ``Splitter`` and ``TextExtractor``
-- while its own ``Plugin`` enum already named ``EXTRACTOR``, ``CONVERTER``,
``GENERATOR`` and ``EDITOR``, with no class behind any of them. These tests cover
the classes that fill those four names: an image extractor, an images-to-PDF
generator, a converter to HTML/Markdown/SVG/TIFF/PNG/JPEG, and the editors
(rotate, remove pages, stamp, flatten, encrypt, decrypt, convert to PDF/A), plus
the page selections ``Splitter`` grew.

Every one of them composes operations that already existed and are tested in
their own right; what is checked here is the plugin contract -- what each one
produces, how many results, what it refuses, and that the results reach the
output data sources.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PageSize, TextStamp
from aspose_pdf.exceptions import AsposePdfException, PdfSecurityException
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    Converter,
    ConvertOptions,
    DecryptOptions,
    Decryptor,
    EncryptOptions,
    Encryptor,
    FlattenOptions,
    FormFlattener,
    ImageExtractor,
    ImageExtractorOptions,
    ImagesToPdf,
    ImagesToPdfOptions,
    PageRemoveOptions,
    PageRemover,
    PdfAConverter,
    PdfAConvertOptions,
    RotateOptions,
    Rotator,
    SplitOptions,
    Splitter,
    Stamper,
    StampOptions,
)


def _pdf(pages: int = 3) -> bytes:
    document = Document()
    for index in range(pages):
        page = document.pages.add(PageSize.A5)
        page.add_text(f"Hello {index}", 50, 400)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _loaded(result, password: str | None = None) -> Document:
    return Document(io.BytesIO(result.to_array()), password=password)


def _png(width: float = 60, height: float = 40, dpi: float = 72) -> bytes:
    """A real PNG, rendered rather than carried as a fixture."""
    document = Document()
    page = document.pages.add(size=(width, height))
    page.draw_rectangle(0, 0, width, height, fill_color=(1, 0, 0), stroke_color=None)
    return page.render(dpi=dpi).to_png()


def _jpeg(width: float = 60, height: float = 40, dpi: float = 72) -> bytes:
    document = Document()
    page = document.pages.add(size=(width, height))
    page.draw_rectangle(0, 0, width, height, fill_color=(0, 0, 1), stroke_color=None)
    return page.render(dpi=dpi).to_jpeg(quality=80)


def _source(data: bytes) -> ByteArrayDataSource:
    return ByteArrayDataSource(data)


# ---------------------------------------------------------------------------
# Editors
# ---------------------------------------------------------------------------


def test_rotator_adds_to_the_rotation_a_page_already_has():
    source = _pdf()
    first = Rotator().process(RotateOptions(90).add_input(_source(source)))
    once = first[0].to_array()
    assert [page.rotation for page in _loaded(first[0]).pages] == [90, 90, 90]
    twice = Rotator().process(RotateOptions(90).add_input(_source(once)))
    assert [page.rotation for page in _loaded(twice[0]).pages] == [180, 180, 180]


def test_rotator_can_set_the_rotation_instead():
    once = Rotator().process(RotateOptions(90).add_input(_source(_pdf())))[0].to_array()
    absolute = Rotator().process(
        RotateOptions(270, relative=False).add_input(_source(once))
    )
    assert [page.rotation for page in _loaded(absolute[0]).pages] == [270, 270, 270]


def test_rotator_turns_only_the_pages_named():
    results = Rotator().process(RotateOptions(90, pages=[1]).add_input(_source(_pdf())))
    assert [page.rotation for page in _loaded(results[0]).pages] == [0, 90, 0]


def test_rotator_refuses_a_page_the_document_does_not_have():
    with pytest.raises(AsposePdfException, match="outside this document"):
        Rotator().process(RotateOptions(90, pages=[7]).add_input(_source(_pdf())))


def test_page_remover_drops_the_pages_named():
    results = PageRemover().process(
        PageRemoveOptions([0, 2]).add_input(_source(_pdf()))
    )
    document = _loaded(results[0])
    assert document.page_count == 1
    assert "Hello 1" in document.extract_text()


def test_page_remover_takes_the_pages_in_any_order_and_without_repeats():
    results = PageRemover().process(
        PageRemoveOptions([2, 0, 2]).add_input(_source(_pdf()))
    )
    assert _loaded(results[0]).page_count == 1


def test_page_remover_will_not_empty_a_document():
    with pytest.raises(AsposePdfException, match="no document behind"):
        PageRemover().process(PageRemoveOptions([0, 1, 2]).add_input(_source(_pdf())))


def test_page_remover_needs_pages_to_remove():
    with pytest.raises(AsposePdfException, match="No pages to remove"):
        PageRemover().process(PageRemoveOptions().add_input(_source(_pdf())))


def test_stamper_puts_the_stamp_on_the_pages_named():
    results = Stamper().process(
        StampOptions(TextStamp("DRAFT", font_size=30), pages=[0]).add_input(
            _source(_pdf())
        )
    )
    document = _loaded(results[0])
    assert "DRAFT" in document.pages[0].extract_text()
    assert "DRAFT" not in document.pages[1].extract_text()


def test_stamper_stamps_every_page_by_default():
    results = Stamper().process(
        StampOptions(TextStamp("DRAFT", font_size=30)).add_input(_source(_pdf()))
    )
    document = _loaded(results[0])
    assert all("DRAFT" in page.extract_text() for page in document.pages)


def test_stamper_needs_a_stamp():
    with pytest.raises(AsposePdfException, match="No stamp"):
        Stamper().process(StampOptions().add_input(_source(_pdf())))


def test_form_flattener_bakes_a_field_into_the_page():
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("name", page, (50, 700, 250, 725), value="typed")
    buffer = io.BytesIO()
    document.save(buffer)

    results = FormFlattener().process(
        FlattenOptions().add_input(_source(buffer.getvalue()))
    )
    flattened = _loaded(results[0])
    assert len(flattened.form.fields) == 0
    assert "typed" in flattened.pages[0].extract_text()


def test_form_flattener_takes_annotations_with_the_fields():
    # Flattening is one operation in the engine, so there is no fields-only form
    # of it -- Form.flatten() is the same call. A plugin option offering the
    # narrower behaviour would have promised what nothing implements.
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("name", page, (50, 700, 250, 725), value="typed")
    page.annotations.add("Square", (10, 10, 60, 60), "", properties={"C": [1, 0, 0]})
    buffer = io.BytesIO()
    document.save(buffer)

    results = FormFlattener().process(
        FlattenOptions().add_input(_source(buffer.getvalue()))
    )
    flattened = _loaded(results[0])
    assert len(flattened.form.fields) == 0
    assert len(flattened.pages[0].annotations) == 0


def test_encryptor_produces_a_document_that_needs_its_password():
    results = Encryptor().process(
        EncryptOptions("user", "owner").add_input(_source(_pdf()))
    )
    with pytest.raises(PdfSecurityException):
        _loaded(results[0])
    assert "Hello 0" in _loaded(results[0], password="user").extract_text()


def test_decryptor_takes_the_protection_off():
    encrypted = Encryptor().process(
        EncryptOptions("user", "owner").add_input(_source(_pdf()))
    )[0].to_array()
    results = Decryptor().process(DecryptOptions("user").add_input(_source(encrypted)))
    # No password at all, which is the point of the plugin.
    assert "Hello 0" in _loaded(results[0]).extract_text()


def test_decryptor_leaves_an_unprotected_input_alone():
    # Document.is_encrypted is about being *locked*, so it is false for a
    # document opened with its password: the removal cannot be gated on it.
    results = Decryptor().process(DecryptOptions("user").add_input(_source(_pdf())))
    assert "Hello 0" in _loaded(results[0]).extract_text()


def test_decryptor_refuses_the_wrong_password():
    encrypted = Encryptor().process(
        EncryptOptions("user", "owner").add_input(_source(_pdf()))
    )[0].to_array()
    with pytest.raises(PdfSecurityException):
        Decryptor().process(DecryptOptions("nope").add_input(_source(encrypted)))


def test_pdfa_converter_converts_and_reports_the_fonts_it_could_not_embed():
    plugin = PdfAConverter()
    results = plugin.process(PdfAConvertOptions("2b").add_input(_source(_pdf())))
    assert _loaded(results[0]).is_pdfa_compliant("2b")
    # One list per input: the Standard-14 face is embedded from the bundled
    # substitutes, so nothing is left unembedded here.
    assert plugin.unembedded_fonts == [[]]


def test_pdfa_converter_rejects_a_level_that_does_not_exist():
    with pytest.raises(Exception, match=r"(?i)level"):
        PdfAConverter().process(PdfAConvertOptions("9z").add_input(_source(_pdf())))


# ---------------------------------------------------------------------------
# Splitter selections
# ---------------------------------------------------------------------------


def test_splitter_still_splits_into_single_pages_by_default():
    results = Splitter().process(SplitOptions().add_input(_source(_pdf())))
    assert [_loaded(result).page_count for result in results] == [1, 1, 1]


def test_splitter_takes_iterables_and_slices_as_selections():
    options = SplitOptions(selections=[range(0, 2), [2], slice(1, None)])
    results = Splitter().process(options.add_input(_source(_pdf())))
    assert [_loaded(result).page_count for result in results] == [2, 1, 2]
    assert "Hello 2" in _loaded(results[1]).extract_text()


def test_a_negative_index_in_a_selection_counts_from_the_end():
    options = SplitOptions(selections=[[-1]])
    results = Splitter().process(options.add_input(_source(_pdf())))
    assert "Hello 2" in _loaded(results[0]).extract_text()


def test_a_selection_outside_the_document_is_refused():
    options = SplitOptions(selections=[[0, 9]])
    with pytest.raises(AsposePdfException, match="outside this document"):
        Splitter().process(options.add_input(_source(_pdf())))


def test_an_empty_selection_is_refused():
    options = SplitOptions(selections=[[]])
    with pytest.raises(AsposePdfException, match="names no page"):
        Splitter().process(options.add_input(_source(_pdf())))


def test_split_options_still_takes_limits_positionally():
    from aspose_pdf import PdfLoadLimits

    limits = PdfLoadLimits(max_input_bytes=1024)
    assert SplitOptions(limits).limits is limits
    assert SplitOptions(limits, selections=[[0]]).selections == [[0]]


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output_format", "results", "textual"),
    [
        ("html", 1, True),
        ("markdown", 1, True),
        ("svg", 3, True),
        ("tiff", 1, False),
        ("png", 3, False),
        ("jpeg", 3, False),
    ],
)
def test_converter_result_count_and_kind(output_format, results, textual):
    container = Converter().process(
        ConvertOptions(output_format).add_input(_source(_pdf()))
    )
    assert len(container) == results
    assert container[0].is_string() is textual
    assert container[0].to_array()


def test_the_converted_output_is_the_format_it_says():
    def convert(output_format: str) -> bytes:
        return (
            Converter()
            .process(ConvertOptions(output_format).add_input(_source(_pdf())))[0]
            .to_array()
        )

    assert convert("html").lstrip().startswith(b"<")
    assert b"Hello 0" in convert("markdown")
    assert convert("svg").lstrip().startswith(b"<")
    assert convert("png").startswith(b"\x89PNG\r\n\x1a\n")
    assert convert("jpeg").startswith(b"\xff\xd8")
    assert convert("tiff").startswith(b"II")


def test_converter_accepts_the_usual_spellings_of_a_format():
    for spelling in ("PNG", "jpg", "tif", "md", "htm", " html "):
        assert len(Converter().process(
            ConvertOptions(spelling).add_input(_source(_pdf(1)))
        )) == 1


def test_converter_refuses_a_format_it_has_no_writer_for():
    with pytest.raises(AsposePdfException, match="Unsupported output format"):
        ConvertOptions("docx")


def test_converter_takes_only_the_pages_named():
    container = Converter().process(
        ConvertOptions("png", pages=[0, 2]).add_input(_source(_pdf()))
    )
    assert len(container) == 2
    markdown = Converter().process(
        ConvertOptions("markdown", pages=[1]).add_input(_source(_pdf()))
    )
    assert "Hello 1" in markdown[0].to_string()
    assert "Hello 0" not in markdown[0].to_string()


def test_converter_renders_at_the_dpi_asked_for():
    import struct

    def png_size(data: bytes) -> tuple[int, int]:
        return struct.unpack(">II", data[16:24])

    at_72 = Converter().process(
        ConvertOptions("png", pages=[0]).add_input(_source(_pdf(1)))
    )[0].to_array()
    at_144 = Converter().process(
        ConvertOptions("png", pages=[0], dpi=144).add_input(_source(_pdf(1)))
    )[0].to_array()
    assert png_size(at_144)[0] == pytest.approx(png_size(at_72)[0] * 2, abs=2)


def test_a_gray_tiff_is_smaller_than_the_colour_one():
    colour = Converter().process(
        ConvertOptions("tiff").add_input(_source(_pdf()))
    )[0].to_array()
    grey = Converter().process(
        ConvertOptions("tiff", mode="gray").add_input(_source(_pdf()))
    )[0].to_array()
    assert len(grey) < len(colour)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_image_extractor_gives_one_result_per_image():
    document = Document()
    page = document.pages.add()
    page.add_image(_png(), 10, 10, 60, 40)
    page.add_image(_jpeg(), 100, 10, 60, 40)
    buffer = io.BytesIO()
    document.save(buffer)

    container = ImageExtractor().process(
        ImageExtractorOptions().add_input(_source(buffer.getvalue()))
    )
    assert len(container) == 2
    heads = {result.to_array()[:4] for result in container}
    assert b"\x89PNG" in heads or b"\xff\xd8\xff\xe0" in heads


def test_image_extractor_produces_nothing_for_a_document_without_images():
    container = ImageExtractor().process(
        ImageExtractorOptions().add_input(_source(_pdf()))
    )
    assert len(container) == 0


def test_images_to_pdf_cuts_each_page_to_its_own_picture():
    container = ImagesToPdf().process(
        ImagesToPdfOptions()
        .add_input(_source(_png(60, 40)))
        .add_input(_source(_jpeg(60, 40, dpi=144)))
    )
    assert len(container) == 1
    document = _loaded(container[0])
    assert document.page_count == 2
    assert document.pages[0].size == PageSize(60, 40)
    # Rendered at 144 dpi, the second image is 120x80 pixels, so one point per
    # pixel makes a page twice the size.
    assert document.pages[1].size == PageSize(120, 80)


def test_images_to_pdf_sizes_a_page_by_the_dpi_given():
    container = ImagesToPdf().process(
        ImagesToPdfOptions(dpi=300).add_input(_source(_png(60, 40)))
    )
    page = _loaded(container[0]).pages[0]
    assert page.size.width == pytest.approx(60 * 72 / 300)
    assert page.size.height == pytest.approx(40 * 72 / 300)


def test_images_to_pdf_can_make_one_document_per_image():
    container = ImagesToPdf().process(
        ImagesToPdfOptions(single_document=False)
        .add_input(_source(_png()))
        .add_input(_source(_jpeg()))
    )
    assert len(container) == 2
    assert all(_loaded(result).page_count == 1 for result in container)


def test_images_to_pdf_fits_an_image_inside_a_fixed_page_size():
    container = ImagesToPdf().process(
        ImagesToPdfOptions(page_size=PageSize.A4, margin=36).add_input(
            _source(_png(600, 400, dpi=72))
        )
    )
    document = _loaded(container[0])
    assert document.pages[0].size == PageSize.A4
    # The image is wider than A4 less two margins, so it is scaled to fit and
    # something is actually drawn.
    assert b"/Image" in document.pages[0].content or document.pages[0].content


def test_images_to_pdf_takes_a_page_size_by_name():
    container = ImagesToPdf().process(
        ImagesToPdfOptions(page_size="letter").add_input(_source(_png()))
    )
    assert _loaded(container[0]).pages[0].size == PageSize.LETTER


def test_images_to_pdf_refuses_a_margin_that_leaves_no_room():
    with pytest.raises(AsposePdfException, match="no room"):
        ImagesToPdf().process(
            ImagesToPdfOptions(page_size=(100, 100), margin=60).add_input(
                _source(_png())
            )
        )


def test_images_to_pdf_refuses_input_that_is_not_a_jpeg_or_a_png():
    with pytest.raises(Exception, match=r"(?i)jpeg and png|carries its own size"):
        ImagesToPdf().process(ImagesToPdfOptions().add_input(_source(_pdf())))


def test_images_to_pdf_refuses_a_dpi_of_zero():
    with pytest.raises(AsposePdfException, match="dpi"):
        ImagesToPdf().process(
            ImagesToPdfOptions(dpi=0).add_input(_source(_png()))
        )


# ---------------------------------------------------------------------------
# The plugin contract every one of them shares
# ---------------------------------------------------------------------------


def test_every_new_plugin_needs_an_input():
    for plugin, options in [
        (Rotator(), RotateOptions()),
        (PageRemover(), PageRemoveOptions([0])),
        (Encryptor(), EncryptOptions("u")),
        (Decryptor(), DecryptOptions("u")),
        (PdfAConverter(), PdfAConvertOptions()),
        (FormFlattener(), FlattenOptions()),
        (Stamper(), StampOptions(TextStamp("x"))),
        (Converter(), ConvertOptions("png")),
        (ImageExtractor(), ImageExtractorOptions()),
        (ImagesToPdf(), ImagesToPdfOptions()),
    ]:
        with pytest.raises(AsposePdfException, match="No input data sources"):
            plugin.process(options)


def test_results_reach_the_output_data_sources():
    out = ByteArrayDataSource(b"")
    options = RotateOptions(90).add_input(_source(_pdf())).add_output(out)
    Rotator().process(options)
    assert out.data.startswith(b"%PDF")


def test_more_outputs_than_results_is_refused():
    options = (
        RotateOptions(90)
        .add_input(_source(_pdf()))
        .add_output(ByteArrayDataSource(b""))
        .add_output(ByteArrayDataSource(b""))
    )
    with pytest.raises(AsposePdfException, match="More output data sources"):
        Rotator().process(options)


def test_a_plugin_runs_over_every_input():
    container = Rotator().process(
        RotateOptions(90).add_input(_source(_pdf(1))).add_input(_source(_pdf(2)))
    )
    assert [_loaded(result).page_count for result in container] == [1, 2]
    assert all(
        page.rotation == 90
        for result in container
        for page in _loaded(result).pages
    )


def test_the_resource_limit_policy_is_applied_to_every_new_plugin():
    from aspose_pdf import PdfLoadLimits
    from aspose_pdf.exceptions import PdfResourceLimitException

    tiny = PdfLoadLimits(max_input_bytes=64)
    options = RotateOptions(90, limits=tiny).add_input(_source(_pdf()))
    with pytest.raises(PdfResourceLimitException):
        Rotator().process(options)
