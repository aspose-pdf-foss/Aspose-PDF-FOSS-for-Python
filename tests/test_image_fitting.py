"""Images fit a frame without distortion or clipping neighboring content."""

from __future__ import annotations

import io
import sys

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.engine.jpeg_encoder import encode as encode_jpeg
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.images import ImagePlacementAbsorber

COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
BACKGROUND = (240, 240, 240)


def _stripes(vertical=False, scale=1):
    width, height = ((2, 4) if vertical else (4, 2))
    width, height = width * scale, height * scale
    pixels = b"".join(
        bytes(COLORS[(y if vertical else x) // scale])
        for y in range(height) for x in range(width)
    )
    return write_png(width, height, "RGB", pixels)


def _expected(vertical, fit):
    if fit == "cover":
        rectangles = (
            [(8, 12, 8, 4), (8, 8, 8, 4)] if vertical
            else [(8, 8, 4, 8), (12, 8, 4, 8)]
        )
        colors = COLORS[1:3]
    elif vertical:
        rectangles = [(10 if fit == "contain" else 8, 14 - 2 * i,
                       4 if fit == "contain" else 8, 2) for i in range(4)]
        colors = COLORS
    else:
        rectangles = [(8 + 2 * i, 10 if fit == "contain" else 8,
                       2, 4 if fit == "contain" else 8) for i in range(4)]
        colors = COLORS
    pixels = bytearray(bytes(BACKGROUND) * 24 * 24)
    for (left, bottom, width, height), color in zip(rectangles, colors):
        for y in range(bottom, bottom + height):
            for x in range(left, left + width):
                offset = ((23 - y) * 24 + x) * 3
                pixels[offset:offset + 3] = bytes(color)
    return bytes(pixels)


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("scale", [1, 4])
@pytest.mark.parametrize("fit", ["stretch", "contain", "cover"])
@pytest.mark.parametrize("source_kind", ["bytes", "bytearray", "path"])
def test_fitted_images_keep_proportions_and_crop_at_the_frame_after_roundtrip(
    vertical, scale, fit, source_kind, tmp_path,
):
    data = _stripes(vertical, scale)
    image_path = tmp_path / "stripes.png"
    image_path.write_bytes(data)
    image = {"bytes": data, "bytearray": bytearray(data), "path": image_path}[source_kind]
    with Document() as document:
        page = document.pages.add(size=(24, 24))
        name = page.add_image(image, 8, 8, 8, 8, fit=fit)
        assert isinstance(name, str) and name
        assert page.render(background=BACKGROUND, antialias=False).pixels == _expected(
            vertical, fit,
        )
        with io.BytesIO() as output:
            document.save(output)
            with Document(output.getvalue()) as reopened:
                assert reopened.pages[1].render(
                    background=BACKGROUND, antialias=False,
                ).pixels == _expected(vertical, fit)
        target = tmp_path / "fitted.pdf"
        document.save(target)
        with Document(target) as reopened:
            assert reopened.pages[1].render(
                background=BACKGROUND, antialias=False,
            ).pixels == _expected(vertical, fit)


@pytest.mark.parametrize(
    "fit,matrix,dpi",
    [
        ("contain", (8, 0, 0, 4, 8, 10), 36),
        ("cover", (16, 0, 0, 8, 4, 8), 18),
    ],
)
def test_placement_metadata_tracks_the_full_image_transform(fit, matrix, dpi):
    with Document() as document, io.BytesIO() as output:
        page = document.pages.add(size=(24, 24))
        page.add_image(_stripes(), 8, 8, 8, 8, fit=fit, name="Photo")
        document.save(output)
        with Document(output.getvalue()) as reopened:
            for current in (document, reopened):
                absorber = ImagePlacementAbsorber()
                absorber.visit(current.pages[1])
                [image] = absorber.image_placements
                assert image.matrix == pytest.approx(matrix)
                assert image.resolution == pytest.approx((dpi, dpi))
                assert (image.width, image.height) == (4, 2)
                assert (image.rectangle.x, image.rectangle.y,
                        image.rectangle.width, image.rectangle.height) == (
                            *matrix[4:], matrix[0], matrix[3],
                        )


@pytest.mark.parametrize(
    "fit,width,height,matrix",
    [
        ("contain", 12, 4, (8, 0, 0, 4, 10, 8)),
        ("cover", 12, 4, (12, 0, 0, 6, 8, 7)),
        ("contain", 8, 4, (8, 0, 0, 4, 8, 8)),
        ("cover", 8, 4, (8, 0, 0, 4, 8, 8)),
        ("contain", 2, 2, (2, 0, 0, 1, 8, 8.5)),
        ("cover", 2, 2, (4, 0, 0, 2, 7, 8)),
    ],
)
def test_non_square_equal_aspect_and_fractional_fit_geometry(fit, width, height, matrix):
    with Document() as document:
        page = document.pages.add()
        page.add_image(_stripes(), 8, 8, width, height, fit=fit)
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert absorber.image_placements[0].matrix == pytest.approx(matrix)


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_fit_is_applied_inside_the_current_graphics_state_without_leaking(fit):
    with Document() as document:
        page = document.pages.add(size=(48, 48))
        with page.graphics(transform=(2, 0, 0, 2, 0, 0)):
            page.add_image(_stripes(), 8, 8, 8, 8, fit=fit)
        page.draw_rectangle(1, 1, 2, 2, fill_color=(1, 0, 1), stroke_color=None)
        raster = page.render(antialias=False)
        assert raster.get_pixel(1, 46) == (255, 0, 255)
        assert raster.get_pixel(15, 24) == (255, 255, 255)
        assert raster.get_pixel(32, 24) == (255, 255, 255)
        assert raster.get_pixel(20, 24) == (0, 255, 0)


def test_cover_clipping_does_not_include_an_unpainted_path_from_existing_content():
    with Document() as document:
        page = document.pages.add(((0, 0, 24, 24), b"0 0 24 24 re"))
        page.add_image(_stripes(), 8, 8, 8, 8, fit="cover")
        assert page.render(background=BACKGROUND, antialias=False).pixels == _expected(
            False, "cover",
        )


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_raw_samples_and_png_transparency_work_without_pillow(fit, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with Document() as document:
        raw_page, png_page = document.pages.add(size=(24, 24)), document.pages.add(size=(24, 24))
        raw_page.add_image(bytes(COLORS[0]) * 8, 8, 8, 8, 8,
                           pixel_width=4, pixel_height=2, fit=fit)
        png = write_png(4, 2, "RGBA", bytes((255, 0, 0, 128)) * 8)
        png_page.add_image(png, 8, 8, 8, 8, fit=fit)
        raw = raw_page.render(antialias=False)
        alpha = png_page.render(background=(0, 0, 255), antialias=False)
        assert raw.get_pixel(12, 12) == (255, 0, 0)
        assert alpha.get_pixel(12, 12) == (128, 0, 127)
        assert alpha.get_pixel(7, 12) == (0, 0, 255)


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_jpeg_fitting_keeps_original_compressed_bytes_and_pixel_dimensions(
    fit, tmp_path, monkeypatch,
):
    monkeypatch.setitem(sys.modules, "PIL", None)
    jpeg = encode_jpeg(8, 4, 3, bytes(COLORS[0]) * 32)
    with Document() as document, io.BytesIO() as output:
        page = document.pages.add(size=(24, 24))
        page.add_image(jpeg, 8, 8, 8, 8, fit=fit)
        document.save(output)
        with Document(output.getvalue()) as reopened:
            absorber = ImagePlacementAbsorber()
            absorber.visit(reopened.pages[1])
            [image] = absorber.image_placements
            assert (image.width, image.height) == (8, 4)
            target = image.save(tmp_path / "original.jpg")
            assert target.read_bytes() == jpeg


@pytest.mark.parametrize("fit", ["contain", "cover"])
@pytest.mark.parametrize("incremental", [False, True])
def test_fitted_images_can_be_added_to_streaming_documents_with_alternate_text(
    fit, incremental, tmp_path,
):
    target = tmp_path / "original.pdf"
    with Document() as document:
        document.pages.add(size=(24, 24))
        document.save(target)
    with Document.open_streaming(target) as document, io.BytesIO() as output:
        document.pages[1].add_image(_stripes(), 8, 8, 8, 8, fit=fit, alt="Color stripes")
        document.save(output, incremental=incremental)
        with Document(output.getvalue()) as reopened:
            assert reopened.pages[1].render(
                background=BACKGROUND, antialias=False,
            ).pixels == _expected(False, fit)
            assert b"/Figure" in reopened.pages[1].content
            assert reopened.tagged_content.root_elements[0].alt_text == "Color stripes"


@pytest.mark.parametrize("fit", ["contain", "cover"])
@pytest.mark.parametrize(
    "options",
    [
        {"width": None}, {"height": None}, {"width": 0}, {"height": -1},
        {"width": True}, {"height": object()}, {"x": False}, {"y": None},
        {"width": float("nan")}, {"height": float("inf")},
        {"x": float("inf")}, {"y": float("nan")}, {"width": 10**1000},
        {"width": 1e-10},
    ],
)
def test_invalid_fit_boxes_fail_without_changing_the_document(fit, options):
    with Document() as document, io.BytesIO() as before, io.BytesIO() as after:
        page = document.pages.add(size=(24, 24))
        document.save(before)
        arguments = dict(x=8, y=8, width=8, height=8, fit=fit)
        arguments.update(options)
        with pytest.raises(PdfValidationException):
            page.add_image(_stripes(), **arguments)
        document.save(after)
        assert before.getvalue() == after.getvalue()


@pytest.mark.parametrize("fit", [None, True, 1, [], "fit", "CONTAIN", ""])
def test_invalid_fit_modes_are_rejected(fit):
    with Document() as document:
        page = document.pages.add()
        with pytest.raises(PdfValidationException, match="fit"):
            page.add_image(_stripes(), 8, 8, 8, 8, fit=fit)


@pytest.mark.parametrize(
    "fit,width,height", [("cover", 1e308, 1e308), ("contain", 1e-6, 8)],
)
def test_unrepresentable_fitted_geometry_is_rejected_before_registering_resources(
    fit, width, height,
):
    with Document() as document, io.BytesIO() as before, io.BytesIO() as after:
        page = document.pages.add()
        document.save(before)
        with pytest.raises(PdfValidationException, match="Fitted image"):
            page.add_image(_stripes(), 0, 0, width, height, fit=fit)
        document.save(after)
        assert before.getvalue() == after.getvalue()


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_fitting_preserves_authored_image_resource_limits(fit):
    with Document(limits=PdfLoadLimits(max_image_pixels=7)) as document:
        page = document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
            page.add_image(_stripes(), 8, 8, 8, 8, fit=fit)
        assert not page.content


def test_default_stretch_and_omitted_dimensions_keep_existing_behavior():
    with Document() as document:
        default, explicit = document.pages.add(size=(24, 24)), document.pages.add(size=(24, 24))
        default.add_image(_stripes(), 8, 8, width=8)
        explicit.add_image(_stripes(), 8, 8, width=8, fit="stretch")
        assert default.render(antialias=False).pixels == explicit.render(antialias=False).pixels
        absorber = ImagePlacementAbsorber()
        absorber.visit(default)
        assert absorber.image_placements[0].matrix == (8, 0, 0, 2, 8, 8)


# Expected contain origins, cover origins and visible stripe ranges in an 8x8 frame.
_ANCHOR_CASES = (
    ("top_left", 8, 12, 8, 0, 0, 0),
    ("top", 10, 12, 4, 0, 1, 0),
    ("top_right", 12, 12, 0, 0, 2, 0),
    ("left", 8, 10, 8, 4, 0, 1),
    ("center", 10, 10, 4, 4, 1, 1),
    ("right", 12, 10, 0, 4, 2, 1),
    ("bottom_left", 8, 8, 8, 8, 0, 2),
    ("bottom", 10, 8, 4, 8, 1, 2),
    ("bottom_right", 12, 8, 0, 8, 2, 2),
)


def _anchored_expected(vertical, fit, case):
    _anchor, contain_x, contain_y, _cover_x, _cover_y, horizontal, upright = case
    if fit == "contain":
        colors = COLORS
        rectangles = (
            [(contain_x, 14 - 2 * i, 4, 2) for i in range(4)] if vertical
            else [(8 + 2 * i, contain_y, 2, 4) for i in range(4)]
        )
    else:
        start = upright if vertical else horizontal
        colors = COLORS[start:start + 2]
        rectangles = (
            [(8, 12, 8, 4), (8, 8, 8, 4)] if vertical
            else [(8, 8, 4, 8), (12, 8, 4, 8)]
        )
    pixels = bytearray(bytes(BACKGROUND) * 24 * 24)
    for (left, bottom, width, height), color in zip(rectangles, colors):
        for y in range(bottom, bottom + height):
            for x in range(left, left + width):
                offset = ((23 - y) * 24 + x) * 3
                pixels[offset:offset + 3] = bytes(color)
    return bytes(pixels)


@pytest.mark.parametrize("case", _ANCHOR_CASES, ids=[case[0] for case in _ANCHOR_CASES])
@pytest.mark.parametrize("fit", ["contain", "cover"])
@pytest.mark.parametrize("vertical", [False, True])
def test_all_anchors_preserve_expected_pixels_and_full_placement_metadata(
    case, fit, vertical, tmp_path,
):
    anchor, contain_x, contain_y, cover_x, cover_y, _horizontal, _upright = case
    expected = _anchored_expected(vertical, fit, case)
    if fit == "contain":
        matrix = (4, 0, 0, 8, contain_x, 8) if vertical else (8, 0, 0, 4, 8, contain_y)
    else:
        matrix = (8, 0, 0, 16, 8, cover_y) if vertical else (16, 0, 0, 8, cover_x, 8)
    with Document() as document:
        page = document.pages.add(size=(24, 24))
        page.add_image(_stripes(vertical), 8, 8, 8, 8, fit=fit, anchor=anchor)
        assert page.render(background=BACKGROUND, antialias=False).pixels == expected
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert absorber.image_placements[0].matrix == pytest.approx(matrix)
        assert (page.media_box, page.crop_box, page.rotation) == ((0, 0, 24, 24), (0, 0, 24, 24), 0)
        path = tmp_path / "anchored.pdf"
        document.save(path)
        for source in (path, path.read_bytes()):
            with Document(source) as reopened:
                assert reopened.pages[1].render(background=BACKGROUND, antialias=False).pixels == expected
                absorber = ImagePlacementAbsorber()
                absorber.visit(reopened.pages[1])
                assert absorber.image_placements[0].matrix == pytest.approx(matrix)


@pytest.mark.parametrize("fit", ["stretch", "contain", "cover"])
def test_explicit_center_keeps_the_default_content_and_appearance(fit):
    with Document() as document:
        first, second = document.pages.add(size=(24, 24)), document.pages.add(size=(24, 24))
        first.add_image(_stripes(), 8, 8, 8, 8, fit=fit)
        second.add_image(_stripes(), 8, 8, 8, 8, fit=fit, anchor="center")
        assert first.content == second.content
        assert first.render(antialias=False).pixels == second.render(antialias=False).pixels


@pytest.mark.parametrize("case", _ANCHOR_CASES, ids=[case[0] for case in _ANCHOR_CASES])
def test_stretch_ignores_valid_anchors_and_keeps_omitted_dimension_defaults(case):
    with Document() as document:
        page = document.pages.add(size=(24, 24))
        page.add_image(_stripes(), 8, 8, width=8, anchor=case[0])
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert absorber.image_placements[0].matrix == (8, 0, 0, 2, 8, 8)


@pytest.mark.parametrize("anchor", [None, True, 1, [], {}, (0, 1), "", "top-left", "TOP_LEFT", "center "])
@pytest.mark.parametrize("fit", ["stretch", "contain", "cover"])
def test_invalid_anchors_precede_image_file_reads_and_preserve_document(anchor, fit, tmp_path):
    with Document() as document, io.BytesIO() as before, io.BytesIO() as after:
        page = document.pages.add()
        document.save(before)
        with pytest.raises(PdfValidationException, match="anchor"):
            page.add_image(tmp_path / "missing.png", 8, 8, 8, 8, fit=fit, anchor=anchor)
        document.save(after)
        assert before.getvalue() == after.getvalue()


def test_engine_rejects_invalid_anchor_before_decoding_image_data(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid anchors must not decode images")

    monkeypatch.setattr("aspose_pdf.engine.simple_pdf.prepare_image", unexpected)
    with Document() as document:
        document.pages.add()
        with pytest.raises(PdfValidationException, match="anchor"):
            document._engine_pdf.add_image_to_page(0, b"broken", 8, 8, 8, 8, anchor="unknown")


def test_anchor_specific_overflow_is_rejected_before_resource_registration():
    with Document() as document, io.BytesIO() as before, io.BytesIO() as after:
        page = document.pages.add()
        document.save(before)
        with pytest.raises(PdfValidationException, match="Fitted image geometry"):
            page.add_image(_stripes(), 0, 1.4e308, 1e308, 1e308, fit="contain", anchor="top")
        document.save(after)
        assert before.getvalue() == after.getvalue()


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_anchored_raw_samples_transparency_and_original_jpeg_need_no_pillow(fit, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with Document() as document:
        raw, alpha, photo = [document.pages.add(size=(24, 24)) for _ in range(3)]
        raw.add_image(bytes(COLORS[0]) * 8, 8, 8, 8, 8, fit=fit, anchor="top_right", pixel_width=4, pixel_height=2)
        png = write_png(4, 2, "RGBA", bytes((255, 0, 0, 128)) * 8)
        alpha.add_image(png, 8, 8, 8, 8, fit=fit, anchor="top_right")
        assert raw.render(antialias=False).get_pixel(12, 9) == (255, 0, 0)
        rendered = alpha.render(background=(0, 0, 255), antialias=False)
        assert rendered.get_pixel(12, 9) == (128, 0, 127)
        assert rendered.get_pixel(7, 9) == (0, 0, 255)
        jpeg = encode_jpeg(8, 4, 3, bytes(COLORS[0]) * 32)
        input_path = tmp_path / "input.jpg"
        input_path.write_bytes(jpeg)
        photo.add_image(input_path, 8, 8, 8, 8, fit=fit, anchor="bottom_right")
        with io.BytesIO() as stream:
            document.save(stream)
            with Document(stream.getvalue()) as reopened:
                absorber = ImagePlacementAbsorber()
                absorber.visit(reopened.pages[3])
                image = absorber.image_placements[0]
                assert (image.width, image.height) == (8, 4)
                assert image.save(tmp_path / "original.jpg").read_bytes() == jpeg


@pytest.mark.parametrize("fit", ["contain", "cover"])
@pytest.mark.parametrize("incremental", [False, True])
def test_anchored_images_support_streaming_incremental_saves_and_alternate_text(fit, incremental, tmp_path):
    source = tmp_path / "source.pdf"
    with Document() as document:
        document.pages.add(size=(24, 24))
        document.save(source)
    original = source.read_bytes()
    with Document.open_streaming(source) as document, io.BytesIO() as output:
        document.pages[1].add_image(_stripes(), 8, 8, 8, 8, fit=fit, anchor="bottom_right", alt="Color stripes")
        document.save(output, incremental=incremental)
        with Document(output.getvalue()) as reopened:
            assert reopened.pages[1].render(background=BACKGROUND, antialias=False).pixels == _anchored_expected(False, fit, _ANCHOR_CASES[-1])
            assert reopened.tagged_content.root_elements[0].alt_text == "Color stripes"
    assert source.read_bytes() == original


@pytest.mark.parametrize("fit", ["contain", "cover"])
def test_anchors_follow_graphics_transforms_without_leaking_clipping(fit):
    with Document() as document:
        page = document.pages.add(size=(48, 48))
        with page.graphics(transform=(2, 0, 0, 2, 0, 0)):
            page.add_image(_stripes(), 8, 8, 8, 8, fit=fit, anchor="top_right")
        page.draw_rectangle(1, 1, 2, 2, fill_color=(1, 0, 1), stroke_color=None)
        raster = page.render(antialias=False)
        assert raster.get_pixel(1, 46) == (255, 0, 255)
        assert raster.get_pixel(15, 20) == (255, 255, 255)
        assert raster.get_pixel(32, 20) == (255, 255, 255)
        assert raster.get_pixel(18, 20) == ((255, 0, 0) if fit == "contain" else (0, 0, 255))
        assert raster.get_pixel(30, 20) == (255, 255, 0)
        assert raster.get_pixel(18, 30) == ((255, 255, 255) if fit == "contain" else (0, 0, 255))
