"""Rendered page archives preserve selected views, provenance and output safety."""

from __future__ import annotations

import builtins
import io
import json
import struct
import weakref
import zipfile
import zlib
from itertools import repeat

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)


def _page(document, color=None, size=(12, 8)):
    page = document.pages.add(size=size)
    if color:
        page.draw_rectangle(0, 0, *size, fill_color=color, stroke_color=None)
    return page


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, color = struct.unpack_from(">IIBB", data, 16)
    compressed = bytearray()
    position = 8
    while position < len(data):
        size = struct.unpack_from(">I", data, position)[0]
        if data[position + 4 : position + 8] == b"IDAT":
            compressed.extend(data[position + 8 : position + 8 + size])
        position += size + 12
    row_size = (width * (3 if color == 2 else 1) * depth + 7) // 8
    raw = zlib.decompress(compressed)
    assert len(raw) == height * (row_size + 1)
    assert all(raw[row * (row_size + 1)] == 0 for row in range(height))
    pixels = b"".join(
        raw[row * (row_size + 1) + 1 : (row + 1) * (row_size + 1)]
        for row in range(height)
    )
    return width, height, depth, color, pixels


def test_selected_pages_keep_order_repeats_and_manifest_provenance():
    with Document() as document:
        for color in ("#f00", "#0f0", "#00f"):
            _page(document, color)
        files = _files(
            document.export_pages_zip(
                pages=(index for index in (2, 0, 2)),
                antialias=False,
                include_manifest=True,
            )
        )
        names = [f"page-{index:04d}.png" for index in range(1, 4)]
        assert list(files) == [*names, "manifest.json"]
        for name, rgb in zip(
            names, (b"\x00\x00\xff", b"\xff\x00\x00", b"\x00\x00\xff"), strict=True
        ):
            assert _png(files[name]) == (12, 8, 8, 2, rgb * 96)
        manifest = json.loads(files["manifest.json"])
        assert manifest == dict(
            schema_version=1,
            image_format="png",
            mode="rgb",
            dpi=72.0,
            pages=[
                dict(
                    archive_name=name,
                    page_index=index,
                    width=12,
                    height=8,
                    size=len(files[name]),
                )
                for name, index in zip(names, (2, 0, 2), strict=True)
            ],
        )


@pytest.mark.parametrize(
    "compression,method",
    [("deflate", zipfile.ZIP_DEFLATED), (" STORED ", zipfile.ZIP_STORED)],
)
def test_default_all_pages_and_zip_metadata_are_deterministic(compression, method):
    with Document() as document:
        _page(document, "#f00")
        _page(document, "#0f0")
        options = dict(compression=compression, compresslevel=3, include_manifest=True)
        data = document.export_pages_zip(**options)
        assert document.export_pages_zip(**options) == data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.infolist()) == 3
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == method and info.create_system == 3
            assert info.external_attr >> 16 == 0o100644
            assert "/" not in info.filename and "\\" not in info.filename


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("antialias", [False, True, 2])
def test_crop_rotation_dpi_background_and_antialias_match_rendering(
    rotation, antialias
):
    with Document() as document:
        page = document.pages.add(((-5, 5, 20, 25), b"1 0 0 rg -5 5 10 20 re f"))
        page.crop_box = (-2, 7, 16, 20)
        page.rotation = rotation
        options = dict(dpi=96, antialias=antialias, background=(200, 220, 240))
        reference = document.render_page(0, **options)
        data = _files(document.export_pages_zip(**options))["page-0001.png"]
        assert _png(data) == (reference.width, reference.height, 8, 2, reference.pixels)
        assert page.rotation == rotation and page.crop_box == (-2, 7, 16, 20)


@pytest.mark.parametrize(
    "mode,depth,color", [("rgb", 8, 2), (" GREY ", 8, 0), ("bw", 1, 0)]
)
@pytest.mark.parametrize("dither", [False, True])
def test_color_modes_threshold_and_dithering_match_raster_encoders(
    mode, depth, color, dither
):
    with Document() as document:
        _page(document, "#789abc", size=(9, 3))
        raster = document.render_page(0, antialias=False)
        data = _files(
            document.export_pages_zip(
                mode=mode,
                threshold=170,
                dither=dither,
                antialias=False,
            )
        )["page-0001.png"]
        assert _png(data)[:4] == (9, 3, depth, color)
        assert data == raster.to_png(mode=mode, threshold=170, dither=dither)


@pytest.mark.parametrize("mode", ["rgb", "gray"])
def test_jpeg_quality_and_alias_match_existing_encoder(mode):
    with Document() as document:
        _page(document, "#a7235b")
        options = dict(
            image_format=" .JPG ", mode=mode, quality=73, dpi=144, antialias=False
        )
        data = _files(document.export_pages_zip(**options))["page-0001.jpg"]
        raster = document.render_page(0, dpi=144, antialias=False)
        assert data == raster.to_jpeg(mode=mode, quality=73)
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")


@pytest.mark.parametrize("mode", ["rgb", "gray", "bilevel"])
def test_webp_lossless_exports_readable_images(mode):
    image = pytest.importorskip("PIL.Image")
    features = pytest.importorskip("PIL.features")
    if not features.check("webp"):
        pytest.skip("Pillow was built without WebP")
    with Document() as document:
        _page(document, "#f00")
        files = _files(
            document.export_pages_zip(
                image_format="webp",
                lossless=True,
                method=0,
                mode=mode,
                include_manifest=True,
            )
        )
        with image.open(io.BytesIO(files["page-0001.webp"])) as decoded:
            assert decoded.format == "WEBP" and decoded.size == (12, 8)
            expected = (
                (255, 0, 0)
                if mode == "rgb"
                else (76, 76, 76)
                if mode == "gray"
                else (0, 0, 0)
            )
            assert decoded.convert("RGB").getpixel((5, 5)) == expected


def test_png_and_jpeg_need_no_pillow_and_webp_reports_missing_extra(monkeypatch):
    original = builtins.__import__

    def without_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Pillow unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pillow)
    with Document() as document:
        _page(document)
        for image_format in ("png", "jpeg"):
            assert (
                len(_files(document.export_pages_zip(image_format=image_format))) == 1
            )
        assert _files(document.export_pages_zip(image_format="webp", pages=[])) == {}
        monkeypatch.setattr(
            document, "render_page", lambda *a, **k: pytest.fail("check codec first")
        )
        output = io.BytesIO(b"Keep")
        with pytest.raises(UnsupportedFeatureException, match="images"):
            document.export_pages_zip(output, image_format="webp")
        assert output.getvalue() == b"Keep" and not output.closed


@pytest.mark.parametrize("include_manifest", [False, True])
def test_empty_documents_and_selections_produce_valid_archives(include_manifest):
    with Document() as document:
        for populated in (False, True):
            if populated:
                _page(document)
            for pages in ([], None) if not populated else ([],):
                files = _files(
                    document.export_pages_zip(
                        pages=pages, include_manifest=include_manifest
                    )
                )
                if include_manifest:
                    assert list(files) == ["manifest.json"]
                    assert json.loads(files["manifest.json"])["pages"] == []
                else:
                    assert files == {}


@pytest.mark.parametrize(
    "options",
    [
        {"image_format": None},
        {"image_format": "tiff"},
        {"dpi": True},
        {"dpi": "72"},
        {"dpi": 0},
        {"dpi": float("inf")},
        {"dpi": 5e-324},
        {"background": [0, 0]},
        {"background": [0, 0, 256]},
        {"background": [True, 0, 0]},
        {"antialias": 0},
        {"antialias": 9},
        {"antialias": 1.5},
        {"include_annotations": 1},
        {"mode": 1},
        {"mode": "invalid"},
        {"threshold": -1},
        {"threshold": 256},
        {"threshold": True},
        {"dither": 1},
        {"quality": True},
        {"quality": "85"},
        {"quality": 101},
        {"image_format": "jpeg", "quality": 0},
        {"image_format": "jpeg", "quality": 85.5},
        {"image_format": "jpeg", "mode": "bilevel"},
        {"lossless": 1},
        {"method": True},
        {"method": 7},
        {"compression": None},
        {"compression": "gzip"},
        {"compresslevel": True},
        {"compresslevel": 10},
        {"include_manifest": 1},
        {"overwrite": None},
        {"pages": 0},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 9]},
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_and_pages_fail_before_any_render_or_output(
    monkeypatch, options, empty
):
    with Document() as document:
        if not empty:
            _page(document)
        monkeypatch.setattr(
            document, "render_page", lambda *a, **k: pytest.fail("validate first")
        )
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfValidationException):
            document.export_pages_zip(output, **options)
        assert output.getvalue() == b"Keep"


@pytest.mark.parametrize(
    "limits,options,match",
    [
        (PdfLoadLimits(max_pages=1), {"pages": [0, 0]}, "max_pages"),
        (
            PdfLoadLimits(max_container_items=1),
            {"include_manifest": True},
            "max_container_items",
        ),
        (PdfLoadLimits(max_image_pixels=95), {}, "max_image_pixels"),
        (PdfLoadLimits(max_raster_pixels=100), {}, "max_raster_pixels"),
        (PdfLoadLimits(max_object_bytes=200), {}, "max_object_bytes"),
        (PdfLoadLimits(max_decoded_stream_bytes=200), {}, "max_decoded_stream_bytes"),
        (PdfLoadLimits(max_total_decoded_bytes=200), {}, "max_total_decoded_bytes"),
        (PdfLoadLimits(max_codec_work_bytes=512), {}, "max_codec_work_bytes"),
    ],
)
def test_planned_budgets_fail_before_rendering(monkeypatch, limits, options, match):
    with Document() as document:
        _page(document)
        document._load_limits = limits
        monkeypatch.setattr(
            document, "render_page", lambda *a, **k: pytest.fail("preflight first")
        )
        with pytest.raises(PdfResourceLimitException, match=match):
            document.export_pages_zip(**options)


def test_unbounded_page_generators_are_limited():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        _page(document)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.export_pages_zip(pages=repeat(0))


def test_file_stream_overwrite_short_writes_and_late_render_failures(
    tmp_path, monkeypatch
):
    with Document() as document:
        _page(document)
        _page(document, "#f00")
        expected = document.export_pages_zip()
        path = tmp_path / "new" / "pages.zip"
        assert document.export_pages_zip(path) == path.read_bytes() == expected
        with pytest.raises(FileExistsError):
            document.export_pages_zip(path, overwrite=False)

        class Short(io.BytesIO):
            def write(self, data):
                return super().write(data[:3])

        with Short(b"prefix") as output:
            output.seek(6)
            assert document.export_pages_zip(output) == expected
            assert output.getvalue() == b"prefix" + expected and not output.closed
        original = document.render_page

        def render(index, **kwargs):
            if index == 1:
                raise PdfValidationException("Damaged later page")
            return original(index, **kwargs)

        monkeypatch.setattr(document, "render_page", render)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfValidationException, match="Damaged"):
            document.export_pages_zip(output)
        assert output.getvalue() == b"Keep" and not output.closed
        with pytest.raises(PdfValidationException, match="Damaged"):
            document.export_pages_zip(path)
        assert path.read_bytes() == expected


def test_disposed_documents_and_stalled_streams_are_rejected():
    with Document() as document:
        _page(document)

        class Stalled:
            def write(self, data):
                return 0

        with pytest.raises(AsposePdfException):
            document.export_pages_zip(Stalled())
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_pages_zip()


@pytest.mark.parametrize(
    "image_format,width", [("jpeg", 65536), ("webp", 16384), ("png", 2**31)]
)
def test_format_dimension_limits_are_rejected_before_rendering(
    monkeypatch, image_format, width
):
    with Document(limits=PdfLoadLimits.unlimited()) as document:
        _page(document, size=(width, 1))
        monkeypatch.setattr(
            document, "render_page", lambda *a, **k: pytest.fail("validate first")
        )
        with pytest.raises(PdfValidationException, match="dimensions"):
            document.export_pages_zip(image_format=image_format, antialias=False)


@pytest.mark.parametrize("include_manifest", [False, True])
def test_encoded_files_and_manifest_obey_byte_budgets(tmp_path, include_manifest):
    with Document() as document:
        _page(document, size=(1, 1))
        document._load_limits = PdfLoadLimits(
            max_object_bytes=70 if include_manifest else 20
        )
        path = tmp_path / "unchanged.zip"
        path.write_bytes(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.export_pages_zip(path, include_manifest=include_manifest)
        assert path.read_bytes() == b"Keep"


def test_cumulative_encoded_bytes_are_bounded_before_writing_destination():
    with Document() as document:
        _page(document, size=(1, 1))
        one = _files(document.export_pages_zip())["page-0001.png"]
        document._load_limits = PdfLoadLimits(max_total_decoded_bytes=2 * len(one) - 1)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="page ZIP selected files"):
            document.export_pages_zip(output, pages=[0, 0])
        assert output.getvalue() == b"Keep"


def test_archive_growth_including_central_directory_is_bounded():
    with Document() as document:
        _page(document, size=(1, 1))
        payload_size = len(_files(document.export_pages_zip())["page-0001.png"])
        # Tiny entries keep codec work small while their ZIP headers accumulate.
        document._load_limits = PdfLoadLimits(max_codec_work_bytes=530000)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="page ZIP output bytes"):
            document.export_pages_zip(
                output,
                pages=[0] * (530000 // (payload_size + 100) + 1000),
                compression="stored",
                antialias=False,
            )
        assert output.getvalue() == b"Keep"


def test_completed_rasters_are_released_before_rendering_next_page(monkeypatch):
    with Document() as document:
        _page(document)
        original = document.render_page
        references = []

        def render(index, **options):
            assert all(reference() is None for reference in references)
            raster = original(index, **options)
            references.append(weakref.ref(raster))
            return raster

        monkeypatch.setattr(document, "render_page", render)
        assert len(_files(document.export_pages_zip(pages=[0, 0, 0]))) == 3
        assert all(reference() is None for reference in references)


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_saved_sources_only_read_selected_content_and_preserve_template(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        for color in ("#f00", "#0f0", "#00f"):
            _page(source, color)
        source.info["Title"] = "Original"
        source.add_attachment("keep.txt", b"Keep")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    raw = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        raw if kind == "bytes" else path, password="secret" if encrypted else None
    ) as document:
        pdf = document._engine_pdf
        before = (
            document.is_encrypted,
            document.info["Title"],
            document.attachments.copy(),
        )
        original = pdf.get_page_content
        visited = []

        def content(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        monkeypatch.setattr(pdf, "get_page_content", content)
        files = _files(document.export_pages_zip(pages=[2, 0, 2], antialias=False))
        assert [_png(data)[-1][:3] for data in files.values()] == [
            b"\x00\x00\xff",
            b"\xff\x00\x00",
            b"\x00\x00\xff",
        ]
        assert set(visited) == {0, 2}
        assert (
            document.is_encrypted,
            document.info["Title"],
            document.attachments,
        ) == before
        if kind == "streaming":
            assert pdf.page_contents == [] and pdf._extracted_text is None
    assert path.read_bytes() == raw


def test_form_appearances_are_included_and_can_be_excluded():
    with Document() as document:
        page = _page(document, size=(12, 12))
        document.form.add_checkbox("agree", page, (2, 2, 10, 10), checked=True)
        included = _files(document.export_pages_zip(antialias=False))["page-0001.png"]
        excluded = _files(
            document.export_pages_zip(antialias=False, include_annotations=False)
        )["page-0001.png"]
        assert included != excluded
        assert (
            included
            == document.render_page(0, antialias=False, draw_annotations=True).to_png()
        )
        assert _png(excluded)[-1] == b"\xff" * (12 * 12 * 3)
        assert document.form["agree"].value is True


def test_webp_without_codec_support_fails_before_rendering(monkeypatch):
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda codec: False)
    with Document() as document:
        _page(document)
        monkeypatch.setattr(
            document, "render_page", lambda *a, **k: pytest.fail("check codec first")
        )
        with pytest.raises(UnsupportedFeatureException, match="WebP support"):
            document.export_pages_zip(image_format="webp")


def test_later_unsupported_page_geometry_is_checked_before_any_render(monkeypatch):
    with Document(limits=PdfLoadLimits.unlimited()) as document:
        _page(document)
        _page(document, size=(65536, 1))
        monkeypatch.setattr(
            document,
            "render_page",
            lambda *a, **k: pytest.fail("preflight whole selection"),
        )
        with pytest.raises(PdfValidationException, match="JPEG dimensions"):
            document.export_pages_zip(image_format="jpeg")


def test_streaming_page_content_limits_still_apply(tmp_path):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _page(source).add_text("Some text", 1, 4, font_size=3)
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=40)
    ) as document:
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            document.export_pages_zip(antialias=False)
