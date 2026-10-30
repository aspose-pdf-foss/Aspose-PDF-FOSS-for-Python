"""Existing page content and annotation geometry move together in page space."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.simple_pdf import CosExtractor
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfSecurityException,
    PdfValidationException,
)
from aspose_pdf.images import ImagePlacementAbsorber


def _draw(page, dx=0, dy=0):
    page.draw_rectangle(10 + dx, 10 + dy, 5, 6,
                        stroke_color=None, fill_color=(0, 0, 1))
    page.add_image(b"\xff\x00\x00", 25 + dx, 10 + dy, 6, 4,
                   pixel_width=1, pixel_height=1)
    page.add_text("Body", 10 + dx, 30 + dy, font_size=6)


def _pdf(rotation=0, algorithm=None):
    with Document() as document, io.BytesIO() as output:
        for _ in range(3):
            page = document.pages.add(size=(60, 50))
            _draw(page)
            page.rotation = rotation
        if algorithm is not None:
            document.encrypt("secret", algorithm=algorithm)
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("dx,dy", [(5, 7), (-4, -3)])
@pytest.mark.parametrize("incremental", [False, True])
def test_translation_matches_authored_positions_and_preserves_other_pages_and_boxes(
    source_kind, rotation, dx, dy, incremental, tmp_path,
):
    raw = _pdf(rotation)
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    loaded = (
        Document.open_streaming(source) if source_kind == "streaming"
        else Document(source if source_kind == "file" else raw)
    )
    with loaded as document, Document() as expected:
        page = document.pages[1]
        page.trim_box = (1, 2, 59, 48)
        boxes = page.media_box, page.crop_box, page.trim_box, page.bleed_box, page.art_box
        expected_page = expected.pages.add(size=(60, 50))
        _draw(expected_page, dx, dy)
        expected_page.rotation = rotation
        wanted = expected_page.render(antialias=False).pixels
        original = document.pages[0].render(antialias=False).pixels
        text = document.extract_text()
        assert page.translate_content(dx, dy) is page
        assert page.render(antialias=False).pixels == wanted
        assert document.pages[0].render(antialias=False).pixels == original
        assert (page.media_box, page.crop_box, page.trim_box, page.bleed_box, page.art_box) == boxes
        assert page.rotation == rotation
        assert document.extract_text() == text
        with io.BytesIO() as output:
            document.save(output, incremental=incremental)
            with Document(output.getvalue()) as reopened:
                assert reopened.pages[1].render(antialias=False).pixels == wanted
                assert reopened.pages[0].render(antialias=False).pixels == original
        target = tmp_path / "translated.pdf"
        document.save(target)
        with Document(target) as reopened:
            assert reopened.pages[1].render(antialias=False).pixels == wanted


def test_annotation_points_widgets_and_insets_move_correctly_after_roundtrip():
    with Document() as document, io.BytesIO() as output:
        page = document.pages.add(size=(100, 100))
        line = page.annotations.add_line((10, 20), (30, 40), rect=(8, 18, 32, 42))
        polygon = page.annotations.add_polygon([(10, 10), (30, 10), (20, 30)])
        ink = page.annotations.add_ink([[(10, 10), (20, 20)], [(30, 30), (40, 40)]])
        square = page.annotations.add_square((10, 10, 30, 30), properties={"RD": [1, 2, 3, 4]})
        free_text = page.annotations.add_free_text(
            (10, 50, 40, 70), "Note", properties={"CL": [1, 2, 3, 4, 5, 6]},
        )
        document.form.add_text_field("answer", page, (10, 75, 50, 90), value="Yes")
        page.annotations.add_highlight((10, 20, 30, 40))
        page.translate_content(5, -7)
        line, polygon, ink, square, free_text = [page.annotations[index] for index in range(5)]
        assert line.rect == (13, 11, 37, 35)
        assert line.start == (15, 13) and line.end == (35, 33)
        assert polygon.vertices == [(15, 3), (35, 3), (25, 23)]
        assert ink.ink_list == [[(15, 3), (25, 13)], [(35, 23), (45, 33)]]
        assert square.get_property("RD") == [1, 2, 3, 4]
        assert free_text.get_property("CL") == [6, -5, 8, -3, 10, -1]
        assert document.form["answer"].rect == (15, 68, 55, 83)
        assert page.annotations[-1].get_property("QuadPoints") == [15, 33, 35, 33, 15, 13, 35, 13]
        document.save(output)
        with Document(output.getvalue()) as reopened:
            assert reopened.pages[0].annotations[0].rect == (13, 11, 37, 35)
            assert reopened.form["answer"].rect == (15, 68, 55, 83)


def test_image_placement_metadata_and_repeated_translations_follow_the_drawing():
    with Document(_pdf()) as document, io.BytesIO() as output:
        page = document.pages[1]
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert absorber.image_placements[0].matrix == (6, 0, 0, 4, 25, 10)
        page.translate_content(5, -3).translate_content(-2.5, 4.25)
        absorber.visit(page)
        assert absorber.image_placements[0].matrix == (6, 0, 0, 4, 27.5, 11.25)
        rectangle = absorber.image_placements[0].rectangle
        assert (rectangle.x, rectangle.y, rectangle.width, rectangle.height) == (27.5, 11.25, 6, 4)
        document.save(output)
        with Document(output.getvalue()) as reopened:
            absorber.visit(reopened.pages[1])
            assert absorber.image_placements[0].matrix == (6, 0, 0, 4, 27.5, 11.25)


def test_generated_annotation_appearance_moves_on_a_page_without_content():
    with Document() as document:
        page = document.pages.add(size=(40, 40))
        annotation = page.annotations.add_square(
            (10, 10, 16, 18), color=(0, 1, 0), interior_color=(0, 1, 0),
        )
        assert annotation.generate_appearance()

        def colored_pixels():
            raster = page.render(antialias=False)
            return {(x, y) for y in range(40) for x in range(40)
                    if raster.get_pixel(x, y) != (255, 255, 255)}

        before = colored_pixels()
        assert before
        page.translate_content(5, -3)
        assert colored_pixels() == {(x + 5, y + 3) for x, y in before}


@pytest.mark.parametrize("operation", ["translate", "scale", "resize"])
def test_streaming_transforms_decode_only_the_selected_page_and_stay_lazy(
    operation, tmp_path, monkeypatch,
):
    source = tmp_path / "source.pdf"
    source.write_bytes(_pdf())
    with Document.open_streaming(source) as document:
        original = CosExtractor.get_page_content
        decoded = []

        def guarded(extractor, page_index):
            assert page_index == 1, "Translation must not decode other pages"
            decoded.append(page_index)
            return original(extractor, page_index)

        monkeypatch.setattr(CosExtractor, "get_page_content", guarded)
        page = document.pages[1]
        if operation == "translate":
            page.translate_content(3, 4)
        elif operation == "scale":
            page.scale_content(0.5, about="origin")
        else:
            page.resize((30, 25))
        assert decoded == [1]
        assert document._engine_pdf._lazy
        assert document._engine_pdf.page_contents == []
        matrix = b"3 4 cm" if operation == "translate" else b"0.5 0 0 0.5"
        assert matrix in page.content
        with io.BytesIO() as output:
            document.save(output, incremental=True)
        assert document._engine_pdf._lazy


def test_a_streaming_translation_survives_later_edits_extraction_and_iteration(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(_pdf())
    with Document.open_streaming(source) as document, Document() as expected:
        page = document.pages[1]
        original_first = document.pages[0].content
        page.translate_content(5, 7)
        page.add_text("Later", 2, 43, font_size=5)
        expected_page = expected.pages.add(size=(60, 50))
        _draw(expected_page, 5, 7)
        expected_page.add_text("Later", 2, 43, font_size=5)
        assert page.render(antialias=False).pixels == expected_page.render(antialias=False).pixels
        streams = list(document.iter_page_content_streams())
        assert streams[0] == original_first
        assert streams[1] == page.content
        with document.extract_pages([1]) as extracted:
            assert extracted.pages[0].render(antialias=False).pixels == expected_page.render(antialias=False).pixels


def test_shared_content_streams_are_not_changed_on_other_pages():
    with Document(_pdf()) as document, io.BytesIO() as output:
        engine = document._engine_pdf
        first, second = engine._get_page_dict(0), engine._get_page_dict(1)
        second.mapping[PdfName("Contents")] = first.mapping[PdfName("Contents")]
        document.save(output)
        with Document(output.getvalue()) as reopened:
            before = reopened.pages[0].content
            reopened.pages[1].translate_content(5, 7)
            assert reopened.pages[0].content == before
            assert reopened.pages[1].content != before


@pytest.mark.parametrize("algorithm", ["AES-256", "AES-128", "RC4"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("incremental", [False, True])
def test_encrypted_content_can_be_translated_and_saved_without_losing_protection(
    algorithm, streaming, incremental, tmp_path,
):
    raw = _pdf(algorithm=algorithm)
    source = tmp_path / "encrypted.pdf"
    source.write_bytes(raw)
    loaded = (
        Document.open_streaming(source, password="secret") if streaming
        else Document(raw, password="secret")
    )
    with loaded as document, Document() as expected, io.BytesIO() as output:
        page = document.pages[1]
        page.translate_content(5, 7)
        expected_page = expected.pages.add(size=(60, 50))
        _draw(expected_page, 5, 7)
        wanted = expected_page.render(antialias=False).pixels
        assert page.render(antialias=False).pixels == wanted
        document.save(output, incremental=incremental)
        with pytest.raises(PdfSecurityException):
            Document(output.getvalue())
        with Document(output.getvalue(), password="secret") as reopened:
            assert reopened.pages[1].render(antialias=False).pixels == wanted
            assert reopened.pages[0].extract_text() == "Body"


@pytest.mark.parametrize("algorithm", [None, "AES-128"])
def test_identity_translation_reads_no_content_and_preserves_original_bytes(
    algorithm, tmp_path, monkeypatch,
):
    raw = _pdf(algorithm=algorithm)
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    with Document.open_streaming(source, password="secret" if algorithm else None) as document:
        def forbidden(*args, **kwargs):
            pytest.fail("Identity translation must not read content")

        monkeypatch.setattr(CosExtractor, "get_page_content", forbidden)
        document.pages[1].translate_content(0, 0)
        with io.BytesIO() as output:
            document.save(output, incremental=True)
            assert output.getvalue() == raw


@pytest.mark.parametrize("axis", ["dx", "dy"])
@pytest.mark.parametrize("bad", [None, True, "invalid", object(), float("nan"), float("inf"), 10**1000])
def test_invalid_translation_fails_without_changing_content_or_annotations(axis, bad):
    with Document() as document:
        page = document.pages.add()
        _draw(page)
        page.annotations.add_square((10, 10, 20, 20))
        before = page.content
        with pytest.raises(PdfValidationException):
            page.translate_content(**{axis: bad, "dy" if axis == "dx" else "dx": 0})
        assert page.content == before and page.annotations[0].rect == (10, 10, 20, 20)


def test_unwrappable_content_is_rejected_before_any_geometry_changes():
    with Document() as document:
        page = document.pages.add(((0, 0, 30, 30), b"1 0 0 1 3 4 cm Q 0 0 2 2 re f"))
        page.annotations.add_square((10, 10, 20, 20))
        before = page.content
        with pytest.raises(PdfValidationException, match="cannot be translated"):
            page.translate_content(5, 7)
        assert page.content == before and page.annotations[0].rect == (10, 10, 20, 20)


def test_transformed_content_stays_within_resource_limits(tmp_path):
    target = tmp_path / "source.pdf"
    with Document() as document:
        document.pages.add(((0, 0, 30, 30), b"0 0 2 2 re f"))
        document.save(target)
    with Document.open_streaming(target, limits=PdfLoadLimits(max_content_stream_bytes=20)) as document:
        page = document.pages[0]
        before = page.content
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            page.translate_content(5, 7)
        assert page.content == before


def test_translation_refuses_disposed_documents():
    document = Document()
    page = document.pages.add()
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        page.translate_content(5, 7)
