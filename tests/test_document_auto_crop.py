"""Batch margin trimming plans all selected pages before changing their boxes."""

from __future__ import annotations

import io
import weakref
from itertools import repeat

import pytest

from aspose_pdf import Document, OutlineItem, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfName, PdfNumber
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfSecurityException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _document():
    document = Document()
    for rect in ((10, 20, 30, 40), (40, 30, 60, 50), None):
        page = document.pages.add(size=(100, 80))
        if rect is not None:
            x0, y0, x1, y1 = rect
            page.draw_rectangle(x0, y0, x1 - x0, y1 - y0,
                                fill_color="#000", stroke_color=None)
    return document


@pytest.mark.parametrize("uniform", [False, True])
@pytest.mark.parametrize("source_kind", ["live", "bytes", "file", "streaming"])
def test_batch_crops_and_original_content_survive_round_trips(
    uniform, source_kind, tmp_path,
):
    path = tmp_path / "source.pdf"
    with _document() as source:
        raw = _bytes(source)
    path.write_bytes(raw)
    if source_kind == "live":
        document = _document()
    elif source_kind == "bytes":
        document = Document(raw)
    else:
        document = (
            Document.open_streaming if source_kind == "streaming" else Document
        )(path)
    expected = (
        {0: (7, 17, 63, 53), 1: (7, 17, 63, 53), 2: (7, 17, 63, 53)}
        if uniform else {0: (7, 17, 33, 43), 1: (37, 27, 63, 53)}
    )
    with document:
        content = [page.content for page in document.pages]
        assert document.auto_crop(3, uniform=uniform) == expected
        assert document.auto_crop(3, uniform=uniform) == {}
        assert [page.content for page in document.pages] == content
        output_path = tmp_path / "cropped.pdf"
        document.save(output_path)
        for source in (_bytes(document), output_path):
            with Document(source) as reopened:
                assert reopened.page_count == 3 and reopened.validate()
                for index, page in enumerate(reopened.pages):
                    assert page.crop_box == expected.get(index, (0, 0, 100, 80))
                    assert page.media_box == (0, 0, 100, 80)
                    assert page.content == content[index]
    assert path.read_bytes() == raw


@pytest.mark.parametrize("uniform", [False, True])
def test_preview_is_detached_and_does_not_change_bytes_or_live_metadata(uniform):
    with _document() as document:
        item = document.outlines.add(OutlineItem("Keep", 1))
        cached = document.extract_text()
        before = _bytes(document)
        model = document._engine_pdf._outlines_data
        document._engine_pdf._page_text_cursor = 2
        planned = document.auto_crop(3, uniform=uniform, dry_run=True)
        assert planned and document.outlines[0] is item and item.page_index == 1
        assert document._engine_pdf._outlines_data is model
        assert document._engine_pdf._extracted_text is cached
        assert document._engine_pdf._page_text_cursor == 2
        assert all(page.crop_box == (0, 0, 100, 80) for page in document.pages)
        assert _bytes(document) == before
        assert document.auto_crop(3, uniform=uniform) == planned
        planned.clear()
        assert document.pages[0].crop_box == (7, 17, 63 if uniform else 33,
                                            53 if uniform else 43)


def test_selection_is_validated_deduplicated_and_keeps_first_selected_order(monkeypatch):
    with _document() as document:
        original = Page.render
        visited = []

        def render(page, **options):
            assert all(p.crop_box == (0, 0, 100, 80) for p in document.pages)
            visited.append(page.index)
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        result = document.auto_crop(pages=(i for i in (1, 0, 1)))
        assert list(result) == visited == [1, 0]
        assert result == {1: (40, 30, 60, 50), 0: (10, 20, 30, 40)}
        assert document.pages[2].crop_box == (0, 0, 100, 80)


@pytest.mark.parametrize("dpi", [36, 72, 144])
@pytest.mark.parametrize("margin", [0, 2])
def test_uniform_frames_follow_displayed_coordinates_with_rotated_offset_pages(
    dpi, margin,
):
    samples = [
        ((0, 0, 100, 80), 0, (10, 40, 30, 60), (10, 30, 60, 60)),
        ((10, -5, 90, 95), 90, (40, 35, 60, 55), (30, 5, 60, 55)),
        ((-20, 30, 80, 110), 180, (50, 50, 70, 70), (20, 50, 70, 80)),
        ((7, 11, 87, 111), 270, (37, 51, 57, 71), (37, 51, 67, 101)),
    ]
    with Document() as document:
        references = []
        expected = {}
        for index, (media, rotation, ink, crop) in enumerate(samples):
            page = document.pages.add((media, b""))
            page.rotation = rotation
            page.bleed_box = media
            page.trim_box = media
            page.art_box = ink
            x0, y0, x1, y1 = ink
            page.draw_rectangle(x0, y0, x1 - x0, y1 - y0,
                                fill_color="#000", stroke_color=None)
            expected[index] = (
                crop[0] - margin, crop[1] - margin,
                crop[2] + margin, crop[3] + margin,
            )
            references.append(page.render(clip=expected[index], dpi=dpi,
                                          antialias=False).pixels)
        assert document.auto_crop(margin, uniform=True, dpi=dpi) == expected
        sizes = set()
        for index, page in enumerate(document.pages):
            raster = page.render(dpi=dpi, antialias=False)
            sizes.add((raster.width, raster.height))
            assert raster.pixels == references[index]
            media, rotation, ink, _ = samples[index]
            assert page.media_box == media and page.rotation == rotation
            assert (page.bleed_box, page.trim_box, page.art_box) == (media, media, ink)
        assert len(sizes) == 1


@pytest.mark.parametrize("uniform", [False, True])
def test_empty_blank_and_full_bleed_batches_are_exact_noops(uniform):
    with Document() as document:
        assert document.auto_crop(uniform=uniform) == {}
        for _ in range(2):
            document.pages.add(size=(10, 10))
        before = _bytes(document)
        assert document.auto_crop(uniform=uniform) == {}
        assert document.auto_crop(pages=[], uniform=uniform) == {}
        assert _bytes(document) == before
        for page in document.pages:
            page.draw_rectangle(0, 0, 10, 10, fill_color="#000", stroke_color=None)
        before = _bytes(document)
        assert document.auto_crop(uniform=uniform) == {}
        assert _bytes(document) == before
    with _document() as document:
        before = _bytes(document)
        assert document.auto_crop(1e308, uniform=uniform) == {}
        assert _bytes(document) == before


def test_inherited_boxes_are_overridden_only_on_selected_pages():
    with _document() as document:
        pdf = document._engine_pdf
        parent = pdf._resolve(pdf._get_page_dict(0).mapping[PdfName("Parent")])
        inherited = PdfArray([PdfNumber(v) for v in (5, 5, 95, 75)])
        parent.mapping[PdfName("CropBox")] = inherited
        assert document.auto_crop(pages=[1]) == {1: (40, 30, 60, 50)}
        assert parent.mapping[PdfName("CropBox")] is inherited
        assert PdfName("CropBox") not in pdf._get_page_dict(0).mapping
        assert document.pages[0].crop_box == (5, 5, 95, 75)
        assert document.pages[2].crop_box == (5, 5, 95, 75)


def test_uniform_cropping_rejects_incompatible_sizes_before_rendering(monkeypatch):
    with _document() as document:
        document.pages[2].size = (80, 100)
        before = _bytes(document)
        monkeypatch.setattr(Page, "render", lambda *a, **k: pytest.fail("Size mismatch"))
        for dry_run in (False, True):
            with pytest.raises(PdfValidationException, match="uniform"):
                document.auto_crop(uniform=True, dry_run=dry_run)
        assert _bytes(document) == before


def test_uniform_union_reaching_all_edges_leaves_every_page_unchanged():
    with Document() as document:
        for x, y in ((0, 0), (90, 70)):
            document.pages.add(size=(100, 80)).draw_rectangle(
                x, y, 10, 10, fill_color="#000", stroke_color=None,
            )
        before = _bytes(document)
        assert document.auto_crop(uniform=True) == {}
        assert _bytes(document) == before


def test_individual_cropping_accepts_different_page_formats():
    with Document() as document:
        for size in ((100, 80), (80, 100)):
            document.pages.add(size=size).draw_rectangle(
                10, 20, 20, 20, fill_color="#000", stroke_color=None,
            )
        assert document.auto_crop() == {0: (10, 20, 30, 40), 1: (10, 20, 30, 40)}


@pytest.mark.parametrize("uniform", [False, True])
def test_padding_never_reveals_content_outside_current_crops(uniform):
    with _document() as document:
        for index in (0, 1):
            document.pages[index].crop_box = (8, 10, 40, 70)
        content = [page.content for page in document.pages]
        expected = {0: (8, 15, 35, 45)}
        if uniform:
            expected[1] = (8, 15, 35, 45)
        assert document.auto_crop(5, pages=[0, 1], uniform=uniform) == expected
        assert [page.content for page in document.pages] == content
        assert document.pages[2].crop_box == (0, 0, 100, 80)


@pytest.mark.parametrize("option,value", [
    ("margin", -1), ("margin", True), ("margin", "3"), ("margin", float("inf")),
    ("dpi", 0), ("dpi", False), ("dpi", float("nan")), ("dpi", 5e-324),
    ("white_threshold", 0), ("white_threshold", 250.0), ("white_threshold", True),
    ("include_annotations", 1), ("uniform", 1), ("uniform", None), ("dry_run", 1),
    ("pages", [0, 3]), ("pages", [True]), ("pages", "0"),
])
def test_invalid_options_fail_before_rendering_even_on_empty_input(
    monkeypatch, option, value,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid options must not render or write a page box")

    with _document() as document, Document() as empty:
        monkeypatch.setattr(Page, "render", forbidden)
        monkeypatch.setattr(document._engine_pdf, "set_page_box", forbidden)
        for instance in (document, empty):
            with pytest.raises(PdfValidationException):
                instance.auto_crop(**{option: value})


@pytest.mark.parametrize("uniform", [False, True])
@pytest.mark.parametrize("error", [PdfParseException, PdfResourceLimitException])
def test_late_render_failure_leaves_every_crop_intact(monkeypatch, uniform, error):
    with _document() as document:
        before = _bytes(document)
        render = Page.render

        def fail(page, **options):
            if page.index == 2:
                raise error("Cannot render page")
            return render(page, **options)

        monkeypatch.setattr(Page, "render", fail)
        with pytest.raises(error, match="Cannot render"):
            document.auto_crop(uniform=uniform)
        assert _bytes(document) == before


def test_raster_and_selection_limits_are_enforced_without_editing_boxes():
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        document.pages.add(size=(10, 10))
        before = _bytes(document)
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.auto_crop()
        assert _bytes(document) == before
    with Document(limits=PdfLoadLimits(max_container_items=3)) as document:
        document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.auto_crop(pages=repeat(0))


@pytest.mark.parametrize("uniform", [False, True])
def test_each_raster_is_released_before_the_next_page(monkeypatch, uniform):
    references = []
    original = Page.render

    def render(page, **options):
        assert all(reference() is None for reference in references)
        raster = original(page, **options)
        references.append(weakref.ref(raster))
        return raster

    with _document() as document:
        monkeypatch.setattr(Page, "render", render)
        assert document.auto_crop(uniform=uniform)
        assert all(reference() is None for reference in references)


@pytest.mark.parametrize("uniform", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_selected_scans_keep_content_lazy_and_password_protection(
    tmp_path, monkeypatch, uniform, encrypted,
):
    path = tmp_path / "scans.pdf"
    with Document() as source:
        png = write_png(1, 1, "RGB", b"\0\0\0")
        for x, y in ((10, 20), (40, 30)):
            source.pages.add(size=(100, 80)).add_image(png, x, y, 20, 20)
        source.pages.add(size=(100, 80))
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    password = "secret" if encrypted else None
    with Document.open_streaming(path, password=password) as document:
        pdf = document._engine_pdf
        original = pdf.get_page_content
        visited = []

        def content(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(pdf, "get_page_content", content)
            result = document.auto_crop(pages=[2, 0, 2], uniform=uniform)
            assert list(result) == ([2, 0] if uniform else [0])
            assert visited == [2, 0] and pdf.page_contents == []
        saved = _bytes(document)
    with Document(saved, password=password) as reopened:
        assert reopened.pages[0].crop_box == (10, 20, 30, 40)
        assert reopened.pages[1].crop_box == (0, 0, 100, 80)
        assert reopened.pages[2].crop_box == (
            (10, 20, 30, 40) if uniform else (0, 0, 100, 80)
        )
    if encrypted:
        with pytest.raises(PdfSecurityException):
            Document(saved)
    assert path.read_bytes() == before


@pytest.mark.parametrize("include", [False, True])
def test_annotations_thresholds_and_form_coordinates_follow_existing_rendering(include):
    with Document() as document:
        page = document.pages.add(size=(100, 80))
        page.draw_rectangle(40, 30, 20, 20, fill_color=(255, 255, 249), stroke_color=None)
        note = page.annotations.add_square((10, 10, 20, 20), interior_color="#000")
        rect = note.rect
        assert document.auto_crop(include_annotations=include) == {
            0: (10, 10, 60, 50) if include else (40, 30, 60, 50),
        }
        assert note.rect == rect
    with Document() as document:
        page = document.pages.add(size=(100, 80))
        field = document.form.add_text_field("name", 0, (10, 20, 90, 50), value="Keep")
        rect = field.rect
        assert document.auto_crop(include_annotations=False) == {}
        assert document.auto_crop()
        assert field.rect == rect and field.value == "Keep"
    with Document() as document:
        document.pages.add()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.auto_crop()
