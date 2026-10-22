"""Effective DPI follows each image paint without decoding image samples."""

from __future__ import annotations

import io
import math
from dataclasses import FrozenInstanceError, replace
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    ImageResolutionIssue,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import image_resolution
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)


def _page(document, content=b"72 0 0 72 10 20 cm /Im Do", size=(300, 150)):
    page = document.pages.add()
    pdf = document._engine_pdf
    pdf._ensure_cos()
    resource = pdf._ensure_resource_subdict(page.index, "XObject")
    image = PdfStream(
        b"samples must never be decoded",
        {
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(size[0]),
            PdfName("Height"): PdfNumber(size[1]),
            PdfName("Filter"): PdfName("UnsupportedCodec"),
        },
    )
    reference = pdf._cos_doc.register_object(image)
    resource.mapping[PdfName("Im")] = reference
    pdf._set_page_content(page.index, content)
    return pdf._get_page_dict(page.index), resource, image


def _form(document, resources, content, matrix=None):
    values = {
        PdfName("Subtype"): PdfName("Form"),
        PdfName("Resources"): resources,
        PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 1, 1)]),
    }
    if matrix is not None:
        values[PdfName("Matrix")] = PdfArray([PdfNumber(v) for v in matrix])
    stream = PdfStream(content, values)
    return document._engine_pdf._cos_doc.register_object(stream), stream


def _save(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_public_report_thresholds_and_detached_immutable_results():
    with Document() as document:
        _page(document)
        assert document.find_low_resolution_images() == []
        assert document.find_low_resolution_images(150) == []
        (issue,) = document.find_low_resolution_images(151)
        assert isinstance(issue, ImageResolutionIssue)
        assert (issue.page_index, issue.placement_index, issue.resource_path) == (
            0,
            0,
            ("Im",),
        )
        assert (issue.pixel_width, issue.pixel_height) == (300, 150)
        assert (issue.dpi_x, issue.dpi_y, issue.effective_dpi) == (300, 150, 150)
        assert issue.bbox == (10, 20, 72, 72)
        assert issue.image_name == "Im" and not issue.is_inline
        with pytest.raises(FrozenInstanceError):
            issue.dpi_x = 1
    assert issue.image_name == "Im" and issue.effective_dpi == 150


@pytest.mark.parametrize(
    "matrix,expected",
    [
        ("0 72 -72 0 200 20", (128, 20, 72, 72)),
        ("43.2 57.6 -57.6 43.2 100 20", (42.4, 20, 100.8, 100.8)),
        ("-72 0 0 -72 200 200", (128, 128, 72, 72)),
        ("72 0 54 72 10 20", (10, 20, 126, 72)),
    ],
)
def test_rotation_reflection_and_shear_use_image_axes(matrix, expected):
    with Document() as document:
        _page(document, f"{matrix} cm /Im Do".encode(), (300, 150))
        (issue,) = document.find_low_resolution_images(151)
        assert issue.dpi_x == pytest.approx(300)
        assert issue.dpi_y == pytest.approx(120 if "54 72" in matrix else 150)
        assert issue.bbox == pytest.approx(expected)


def test_repeated_paints_graphics_stack_order_and_singular_transforms():
    with Document() as document:
        _page(
            document,
            b"q 72 0 0 72 0 0 cm /Im Do Q "
            b"q 0 0 0 0 0 0 cm /Im Do Q "
            b"q 144 0 0 36 5 6 cm /Im Do /Im Do Q "
            b"q 1 0 2 0 0 0 cm /Im Do Q",
            (150, 150),
        )
        issues = document.find_low_resolution_images()
        assert [issue.placement_index for issue in issues] == [2, 3]
        assert [(issue.dpi_x, issue.dpi_y) for issue in issues] == [(75, 300)] * 2
        assert [issue.bbox for issue in issues] == [(5, 6, 144, 36)] * 2


def test_nested_repeated_forms_compose_matrices_and_keep_local_resource_names():
    with Document() as document:
        _, resources, image = _page(
            document, b"q 0 2 -3 0 100 20 cm /Outer Do Q /Outer Do"
        )
        inner_resources = PdfDictionary(
            {
                PdfName("XObject"): PdfDictionary(
                    {
                        PdfName("Same"): document._engine_pdf._cos_doc.register_object(
                            image
                        )
                    }
                )
            }
        )
        inner, _ = _form(document, inner_resources, b"72 0 0 36 5 6 cm /Same Do")
        outer_resources = PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("Child"): inner})}
        )
        outer, _ = _form(document, outer_resources, b"/Child Do", (2, 0, 0, 1, 10, 20))
        resources.mapping[PdfName("Outer")] = outer
        issues = document.find_low_resolution_images(301)
        assert len(issues) == 2
        assert issues[0].resource_path == ("Outer", "Child", "Same")
        assert (issues[0].dpi_x, issues[0].dpi_y) == pytest.approx((75, 100))
        assert issues[0].bbox == pytest.approx((-86, 60, 108, 288))
        assert (issues[1].dpi_x, issues[1].dpi_y) == pytest.approx((150, 300))


def test_inherited_resources_form_fallback_user_unit_page_rotate_and_unused_resources():
    with Document() as document:
        page, resources, _ = _page(document, b"72 0 0 72 0 0 cm /F Do", (150, 150))
        reference, form = _form(document, None, b"/Im Do")
        form.mapping.pop(PdfName("Resources"))
        resources.mapping[PdfName("F")] = reference
        parent = document._engine_pdf._resolve(page.mapping[PdfName("Parent")])
        parent.mapping[PdfName("Resources")] = page.mapping.pop(PdfName("Resources"))
        page.mapping[PdfName("UserUnit")] = PdfNumber(2)
        page.mapping[PdfName("Rotate")] = PdfNumber(90)
        resources.mapping[PdfName("Unused")] = PdfString(b"malformed but never invoked")
        (issue,) = document.find_low_resolution_images()
        assert (issue.dpi_x, issue.dpi_y) == (75, 75)
        assert issue.bbox == (0, 0, 72, 72)
        assert issue.resource_path == ("F", "Im")


def test_inline_images_and_operator_lookalikes_do_not_corrupt_following_placements():
    samples = b" q /Im Do Q ( "
    with Document() as document:
        _page(
            document,
            b"72 0 0 72 0 0 cm "
            b"BI /W 14 /H 1 /BPC 8 /CS /G ID " + samples + b" EI "
            b"(/Missing Do 99 0 0 99 0 0 cm) Tj "
            b"<2f4d697373696e6720446f> Tj "
            b"[/Missing (q) 400] TJ "
            b"/Tag << /ActualText (/Missing Do) /Nested [99 88] >> BDC EMC "
            b"% /Missing Do\n /Im Do",
            (10, 20),
        )
        issues = document.find_low_resolution_images()
        assert len(issues) == 2
        assert issues[0].is_inline and issues[0].image_name is None
        assert issues[0].resource_path == ()
        assert (issues[0].pixel_width, issues[0].pixel_height) == (14, 1)
        assert (issues[0].dpi_x, issues[0].dpi_y) == (14, 1)
        assert issues[1].placement_index == 1 and issues[1].image_name == "Im"


def test_inline_in_form_and_encoded_resource_names():
    with Document() as document:
        _, resources, image = _page(
            document, b"72 0 0 72 0 0 cm /F#20A Do /#2FIm Do", (1, 1)
        )
        reference, _ = _form(
            document, PdfDictionary({}), b"BI /W 1 /H 1 /BPC 8 /CS /G ID x EI"
        )
        resources.mapping[PdfName("F A")] = reference
        resources.mapping[PdfName("//Im")] = (
            document._engine_pdf._cos_doc.register_object(image)
        )
        first, second = document.find_low_resolution_images()
        assert first.is_inline and first.resource_path == ("F A",)
        assert second.image_name == "/Im" and second.resource_path == ("/Im",)


def test_selection_order_repeats_empty_and_full_validation_before_content(monkeypatch):
    with Document() as document:
        for _ in range(3):
            _page(document, size=(1, 1))
        assert [
            i.page_index
            for i in document.find_low_resolution_images(pages=iter([2, 0, 2]))
        ] == [2, 0, 2]
        assert document.find_low_resolution_images(pages=[]) == []
        monkeypatch.setattr(
            image_resolution,
            "_placements",
            lambda *a: pytest.fail("selection must validate first"),
        )
        for selection in ([0, 3], [0, -1], [False], [0.0], "0", 1):
            with pytest.raises(PdfValidationException):
                document.find_low_resolution_images(pages=selection)


@pytest.mark.parametrize(
    "value", [0, -1, True, "150", None, math.nan, math.inf, -math.inf, 10**400]
)
def test_threshold_validation_even_for_empty_selection(value):
    with Document() as document:
        with pytest.raises(PdfValidationException):
            document.find_low_resolution_images(value, pages=[])


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, math.inf, 10**400, "10"])
def test_invalid_image_geometry_is_not_silently_skipped(bad):
    with Document() as document:
        _, _, image = _page(document)
        image.mapping[PdfName("Width")] = (
            PdfString(bad) if isinstance(bad, str) else PdfNumber(bad)
        )
        with pytest.raises(PdfValidationException):
            document.find_low_resolution_images()


@pytest.mark.parametrize(
    "content",
    [
        b"/Missing Do",
        b"1 0 0 cm /Im Do",
        b"nan 0 0 1 0 0 cm /Im Do",
        b"Q /Im Do",
        b"q /Im Do",
        b"[/Im Do",
        b"/Im /Im Do",
        b"BI /W ID x EI",
    ],
)
def test_malformed_invoked_content_raises(content):
    with Document() as document:
        _page(document, content)
        with pytest.raises((PdfParseException, PdfValidationException)):
            document.find_low_resolution_images()


def test_form_cycles_fail_but_unused_cycles_do_not():
    with Document() as document:
        _, resources, _ = _page(document)
        resource_dict = PdfDictionary({PdfName("XObject"): resources})
        reference, _ = _form(document, resource_dict, b"/F Do")
        resources.mapping[PdfName("F")] = reference
        assert document.find_low_resolution_images() == []
        document._engine_pdf._set_page_content(0, b"/F Do")
        with pytest.raises(PdfParseException, match="cycle"):
            document.find_low_resolution_images()


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_roundtrip_lazy_selected_reads_no_pixel_decode_or_source_mutation(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        for _ in range(3):
            source.pages.add().add_image(
                RasterizedPage(1, 1, b"\xff\x00\x00").to_png(),
                10,
                20,
                width=72,
                height=72,
            )
        source.info["Title"] = "Keep title"
        source.add_attachment("keep.txt", b"Keep attachment")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        before if kind == "bytes" else path, password="secret" if encrypted else None
    ) as document:
        pdf = document._engine_pdf
        protection = document.is_encrypted
        cursor = (pdf._image_names, pdf._image_cursor)
        cache = (set(dict.keys(pdf.images)), set(getattr(pdf.images, "_loaders", {})))
        content_cache = list(pdf.page_contents)
        original = image_resolution.CosExtractor._decode_stream
        calls = []
        selected = pdf._get_page_dict(2)
        selected_content = pdf._resolve(selected.mapping[PdfName("Contents")])

        def decode(extractor, stream, *args, **options):
            assert stream is selected_content, (
                "Only selected page content may be decoded"
            )
            calls.append(stream)
            return original(extractor, stream, *args, **options)

        monkeypatch.setattr(image_resolution.CosExtractor, "_decode_stream", decode)
        monkeypatch.setattr(
            pdf, "_hydrate_image_info", lambda: pytest.fail("must stay lazy")
        )
        (issue,) = document.find_low_resolution_images(pages=[2])
        assert issue.page_index == 2 and issue.effective_dpi == 1
        assert (pdf._image_names, pdf._image_cursor) == cursor
        assert (
            set(dict.keys(pdf.images)),
            set(getattr(pdf.images, "_loaders", {})),
        ) == cache
        assert pdf.page_contents == content_cache
        assert bool(calls) == (kind == "streaming")
        assert document.info["Title"] == "Keep title"
        assert document.attachments == {"keep.txt": b"Keep attachment"}
        assert document.is_encrypted is protection
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_image_pixels", 10),
        ("max_content_stream_bytes", 4),
        ("max_content_tokens", 4),
        ("max_container_items", 1),
    ],
)
def test_inspection_resource_limits(field, value):
    with Document() as document:
        _page(document, b"q 72 0 0 72 0 0 cm /Im Do /Im Do Q")
        document._load_limits = replace(document.load_limits, **{field: value})
        with pytest.raises(PdfResourceLimitException):
            document.find_low_resolution_images()


def test_unbounded_selection_and_repeated_form_expansion_are_bounded():
    with Document(limits=PdfLoadLimits(max_container_items=100)) as document:
        _page(document)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.find_low_resolution_images(pages=repeat(0))
    with Document() as document:
        _, resources, _ = _page(document, b"/F Do " * 10)
        reference, _ = _form(
            document, PdfDictionary({PdfName("XObject"): resources}), b"/Im Do " * 10
        )
        resources.mapping[PdfName("F")] = reference
        document._load_limits = replace(document.load_limits, max_content_tokens=100)
        with pytest.raises(PdfResourceLimitException, match="total tokens"):
            document.find_low_resolution_images()


def test_empty_documents_and_disposed_documents():
    with Document() as document:
        assert document.find_low_resolution_images() == []
    with pytest.raises(AsposePdfException):
        document.find_low_resolution_images()


def test_decimal_user_unit_and_rotated_axis_equality_is_not_a_false_positive():
    with Document() as document:
        page, _, _ = _page(document, b"43.2 57.6 -57.6 43.2 0 0 cm /Im Do", (180, 180))
        page.mapping[PdfName("UserUnit")] = PdfNumber(1.2)
        assert document.find_low_resolution_images(150) == []
        (issue,) = document.find_low_resolution_images(150.000001)
        assert (issue.dpi_x, issue.dpi_y) == (150, 150)


@pytest.mark.parametrize("value", [0, -1, True, math.nan, math.inf, 10**400, "2"])
def test_invalid_user_unit(value):
    with Document() as document:
        page, _, _ = _page(document)
        page.mapping[PdfName("UserUnit")] = (
            PdfString(value) if isinstance(value, str) else PdfNumber(value)
        )
        with pytest.raises(PdfValidationException):
            document.find_low_resolution_images()


@pytest.mark.parametrize(
    "matrix",
    [
        PdfString(b"invalid"),
        PdfArray([PdfNumber(1)]),
        PdfArray([PdfNumber(math.inf)] * 6),
    ],
)
def test_invalid_form_matrix(matrix):
    with Document() as document:
        _, resources, _ = _page(document, b"/F Do")
        ref, form = _form(
            document, PdfDictionary({PdfName("XObject"): resources}), b"/Im Do"
        )
        form.mapping[PdfName("Matrix")] = matrix
        resources.mapping[PdfName("F")] = ref
        with pytest.raises(PdfValidationException):
            document.find_low_resolution_images()


@pytest.mark.parametrize("streaming", [False, True])
def test_compressed_encrypted_form_content_and_page_stream_arrays(tmp_path, streaming):
    import zlib

    path = tmp_path / "forms.pdf"
    with Document() as source:
        page, resources, _ = _page(source, b"72 0 0 72 10 20 cm /F Do", (10, 20))
        reference, form = _form(
            source,
            PdfDictionary({PdfName("XObject"): resources}),
            zlib.compress(b"/Im Do"),
            (2, 0, 0, 1, 0, 0),
        )
        form.mapping[PdfName("Filter")] = PdfName("FlateDecode")
        resources.mapping[PdfName("F")] = reference
        pdf = source._engine_pdf
        page.mapping[PdfName("Contents")] = PdfArray(
            [
                pdf._cos_doc.register_object(PdfStream(b"q 72 0 0 72 10 20 cm")),
                pdf._cos_doc.register_object(PdfStream(b"/F Do Q")),
            ]
        )
        pdf.page_contents[0] = b"q 72 0 0 72 10 20 cm\n/F Do Q"
        source.encrypt("secret", "owner")
        source.save(path)
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret") as document:
        (issue,) = document.find_low_resolution_images()
        assert issue.resource_path == ("F", "Im")
        assert (issue.dpi_x, issue.dpi_y) == (5, 20)
        assert issue.bbox == (10, 20, 144, 72)


@pytest.mark.parametrize(
    "content",
    [
        b"BI /W 1 /H 1 /BPC 8 /CS /G ID x",
        b"BI /W 1 /H 1 /BPC 8 /CS /G ID x /Im Do",
        b"1" + b"0" * 400 + b" 0 0 72 0 0 cm /Im Do",
    ],
)
def test_truncated_inline_images_and_extreme_transforms_raise_clear_errors(content):
    with Document() as document:
        _page(document, content)
        with pytest.raises((PdfParseException, PdfValidationException)):
            document.find_low_resolution_images()


def test_source_bytes_remain_identical_and_reports_follow_saved_edits():
    with Document() as document:
        document.pages.add().add_image(
            RasterizedPage(1, 1, b"\xff\x00\x00").to_png(), 10, 20, width=72, height=72
        )
        before = _save(document)
        original = document.find_low_resolution_images()
        assert _save(document) == before
        with Document(before) as reopened:
            assert reopened.find_low_resolution_images() == original


def test_form_depth_and_total_decoded_bytes_are_limited():
    with Document() as document:
        _, resources, _ = _page(document, b"/Outer Do")
        inner_resources = PdfDictionary({PdfName("XObject"): resources})
        inner, _ = _form(document, inner_resources, b"/Im Do")
        outer, _ = _form(
            document,
            PdfDictionary(
                {PdfName("XObject"): PdfDictionary({PdfName("Inner"): inner})}
            ),
            b"/Inner Do",
        )
        resources.mapping[PdfName("Outer")] = outer
        document._load_limits = replace(document.load_limits, max_nesting_depth=2)
        with pytest.raises(PdfResourceLimitException, match="Form nesting"):
            document.find_low_resolution_images()
        document._load_limits = replace(
            document.load_limits, max_nesting_depth=100, max_total_decoded_bytes=10
        )
        with pytest.raises(PdfResourceLimitException, match="total decoded"):
            document.find_low_resolution_images()


def test_deep_forms_are_followed_without_a_fixed_eight_level_cutoff():
    with Document() as document:
        _, resources, _ = _page(document, b"72 0 0 72 0 0 cm /F Do", (10, 20))
        child_resources = PdfDictionary({PdfName("XObject"): resources})
        content = b"/Im Do"
        for _ in range(12):
            reference, _ = _form(document, child_resources, content)
            child_resources = PdfDictionary(
                {PdfName("XObject"): PdfDictionary({PdfName("F"): reference})}
            )
            content = b"/F Do"
        resources.mapping[PdfName("F")] = reference
        (issue,) = document.find_low_resolution_images()
        assert issue.resource_path == (*(["F"] * 12), "Im")
        assert issue.effective_dpi == 10


def test_pdf_null_user_unit_is_the_default_and_array_operands_are_bounded():
    with Document() as document:
        page, _, _ = _page(document, size=(10, 20))
        page.mapping[PdfName("UserUnit")] = PdfNull()
        assert document.find_low_resolution_images()[0].effective_dpi == 10
        document._engine_pdf._set_page_content(0, b"[] [] [] q Q")
        document._load_limits = replace(document.load_limits, max_container_items=2)
        with pytest.raises(PdfResourceLimitException, match="operands"):
            document.find_low_resolution_images()


def test_nested_form_missing_resources_uses_page_resources_instead_of_outer_form():
    with Document() as document:
        _, resources, _ = _page(document, b"72 0 0 72 0 0 cm /Outer Do", (150, 150))
        pdf = document._engine_pdf
        inner, form = _form(document, None, b"/Im Do")
        form.mapping.pop(PdfName("Resources"))
        other_image = PdfStream(
            b"not decoded",
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
            },
        )
        outer_resources = PdfDictionary(
            {
                PdfName("XObject"): PdfDictionary(
                    {
                        PdfName("Inner"): inner,
                        PdfName("Im"): pdf._cos_doc.register_object(other_image),
                    }
                )
            }
        )
        outer, _ = _form(document, outer_resources, b"/Inner Do")
        resources.mapping[PdfName("Outer")] = outer
        (issue,) = document.find_low_resolution_images(151)
        assert issue.resource_path == ("Outer", "Inner", "Im")
        assert (issue.pixel_width, issue.pixel_height) == (150, 150)
        assert (issue.dpi_x, issue.dpi_y) == (150, 150)
