"""GIF/WebP animations import as composited pages under a shared resource budget."""

from __future__ import annotations

import io
import struct

import pytest

from aspose_pdf import Document, PageSize, PdfLoadLimits
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
from aspose_pdf.images import ImagePlacementAbsorber
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    FileDataSource,
    ImagesToPdf,
    ImagesToPdfOptions,
    StreamDataSource,
)


@pytest.fixture
def pillow():
    return pytest.importorskip("PIL.Image")


def _chunk(kind, data):
    return kind + struct.pack("<I", len(data)) + data + bytes(len(data) & 1)


def _u24(number):
    return number.to_bytes(3, "little")


def _webp(
    pillow,
    disposal=1,
    *,
    blend=True,
    lossless=True,
    loop=0,
    exif=None,
    overlap=False,
    repeat_first=False,
):
    if not pytest.importorskip("PIL.features").check("webp"):
        pytest.skip("Pillow was built without WebP")
    pieces = []
    specifications = [
        ((8, 6), (0, 0), (255, 0, 0, 255)),
        ((2, 2), (4, 0), (0, 255, 0, 128)),
        ((2, 2), (0, 4), (0, 0, 255, 255)),
    ]
    for index, (size, (x, y), color) in enumerate(specifications):
        if index == 1 and overlap:
            x = y = 0
        with pillow.new("RGBA", size, color) as frame, io.BytesIO() as output:
            if index == 0:
                frame.paste((0, 0, 0, 0), (2, 0, 8, 6))
                frame.paste((0, 0, 0, 0), (0, 2, 2, 6))
            frame.save(output, format="WEBP", lossless=lossless, quality=85)
            data = output.getvalue()
        subchunks = b""
        position = 12
        while position < len(data):
            size = int.from_bytes(data[position + 4 : position + 8], "little")
            end = position + 8 + size + (size & 1)
            if data[position : position + 4] in (b"VP8 ", b"VP8L", b"ALPH"):
                subchunks += data[position:end]
            position = end
        flags = (disposal if index == 1 else 0) | (0 if blend else 2)
        header = (
            _u24(x // 2)
            + _u24(y // 2)
            + _u24(frame.width - 1)
            + _u24(frame.height - 1)
            + _u24((100, 0, 500)[index])
            + bytes([flags])
        )
        pieces.append(_chunk(b"ANMF", header + subchunks))
    if repeat_first:
        pieces = [pieces[0], pieces[0]]
    flags = 2 | 16 | (8 if exif else 0)
    payload = _chunk(b"VP8X", bytes([flags, 0, 0, 0]) + _u24(7) + _u24(5))
    payload += _chunk(b"ANIM", bytes(4) + struct.pack("<H", loop)) + b"".join(pieces)
    if exif:
        payload += _chunk(b"EXIF", exif)
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload


def _gif(pillow, disposal=2, *, loop=0, repeat_first=False):
    palette = [0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * 756
    pieces = []
    header = None
    for index, (size, offset, color) in enumerate(
        [
            ((8, 6), (0, 0), 1),
            ((2, 2), (4, 0), 2),
            ((2, 2), (0, 4), 3),
        ]
    ):
        with pillow.new("P", size, color) as frame, io.BytesIO() as output:
            frame.putpalette(palette)
            if index == 0:
                frame.paste(0, (2, 0, 8, 6))
                frame.paste(0, (0, 2, 2, 6))
            frame.save(
                output,
                format="GIF",
                transparency=0,
                optimize=False,
                disposal=disposal if index == 1 else 1,
                duration=(100, 0, 500)[index],
            )
            data = output.getvalue()
        table_end = 13 + (3 << ((data[10] & 7) + 1))
        if header is None:
            header = data[:table_end]
        piece = bytearray(data[table_end:-1])
        assert piece[:3] == b"\x21\xf9\x04" and piece[8] == 0x2C
        struct.pack_into("<HH", piece, 9, *offset)
        pieces.append(piece)
    if repeat_first:
        pieces = [pieces[0], pieces[0]]
    repeat = b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    return header + repeat + b"".join(pieces) + b"\x3b"


def _animation(pillow, kind, **kwargs):
    return (_gif if kind == "GIF" else _webp)(pillow, **kwargs)


def _convert(data, **kwargs):
    return ImagesToPdf().process(
        ImagesToPdfOptions(**kwargs).add_input(ByteArrayDataSource(data))
    )


def _reference(pillow, data):
    frames = []
    with pillow.open(io.BytesIO(data)) as image:
        for index in range(image.n_frames):
            image.seek(index)
            with image.convert("RGBA") as frame:
                frames.append(write_png(*frame.size, "RGBA", frame.tobytes()))
    return frames


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
@pytest.mark.parametrize("source_kind", ["bytes", "file", "stream"])
@pytest.mark.parametrize("single", [True, False])
def test_frames_match_composited_pngs_and_survive_pdf_saves(
    pillow, kind, source_kind, single, tmp_path
):
    data = _animation(pillow, kind, loop=5)
    path = tmp_path / "animation.jpg"
    path.write_bytes(data)
    with io.BytesIO(data) as stream:
        source = {
            "bytes": ByteArrayDataSource(data),
            "file": FileDataSource(path),
            "stream": StreamDataSource(stream),
        }[source_kind]
        results = ImagesToPdf().process(
            ImagesToPdfOptions(single_document=single).add_input(source)
        )
        assert not stream.closed
    references = _reference(pillow, data)
    assert len(references) == 3 and len(results) == (1 if single else 3)
    actual = []
    for result_index, result in enumerate(results):
        with Document(result.to_array()) as document:
            assert all(page.size == PageSize(8, 6) for page in document.pages)
            for page in document.pages:
                actual.append(
                    [
                        page.render(background=background, antialias=False).pixels
                        for background in [(255, 255, 255), (0, 255, 0)]
                    ]
                )
            target = tmp_path / f"frames-{result_index}.pdf"
            document.save(target)
            with Document(target) as reopened:
                assert (
                    reopened.pages[-1].render().pixels
                    == document.pages[-1].render().pixels
                )
    for rendered, png in zip(actual, references, strict=True):
        with Document(_convert(png)[0].to_array()) as reference:
            assert rendered == [
                reference.pages[0].render(background=background, antialias=False).pixels
                for background in [(255, 255, 255), (0, 255, 0)]
            ]
    assert path.read_bytes() == data


@pytest.mark.parametrize("disposal", [1, 2, 3])
def test_gif_restores_background_or_previous_canvas_as_requested(pillow, disposal):
    with Document(_convert(_gif(pillow, disposal))[0].to_array()) as document:
        rasters = [page.render(antialias=False) for page in document.pages]
        assert rasters[0].get_pixel(0, 0) == (255, 0, 0)
        assert rasters[1].get_pixel(4, 0) == (0, 255, 0)
        assert rasters[2].get_pixel(0, 0) == (255, 0, 0)
        assert rasters[2].get_pixel(0, 4) == (0, 0, 255)
        assert rasters[2].get_pixel(4, 0) == (
            (0, 255, 0) if disposal == 1 else (255, 255, 255)
        )


@pytest.mark.parametrize("disposal", [0, 1])
@pytest.mark.parametrize("blend", [False, True])
@pytest.mark.parametrize("lossless", [False, True])
def test_webp_blend_disposal_and_lossy_frames_match_decoder(
    pillow, disposal, blend, lossless
):
    data = _webp(
        pillow, disposal=disposal, blend=blend, lossless=lossless, overlap=True
    )
    references = _reference(pillow, data)
    with Document(_convert(data)[0].to_array()) as document:
        if lossless:
            expected_pixel = (127, 128, 0) if blend else (127, 255, 127)
            assert document.pages[1].render(antialias=False).get_pixel(
                0, 0
            ) == pytest.approx(expected_pixel, abs=1)
        for page, png in zip(document.pages, references, strict=True):
            with Document(_convert(png)[0].to_array()) as expected:
                assert (
                    page.render(antialias=False).pixels
                    == expected.pages[0].render(antialias=False).pixels
                )


@pytest.mark.parametrize("single", [False, True])
def test_mixed_inputs_keep_source_and_frame_order(pillow, single):
    png = write_png(2, 2, "RGB", bytes([0, 0, 0] * 4))
    options = ImagesToPdfOptions(single_document=single)
    for data in [png, _gif(pillow), _webp(pillow), png]:
        options.add_input(ByteArrayDataSource(data))
    results = ImagesToPdf().process(options)
    sizes = []
    for result in results:
        with Document(result.to_array()) as document:
            sizes.extend(page.size for page in document.pages)
    assert sizes == [PageSize(2, 2)] + [PageSize(8, 6)] * 6 + [PageSize(2, 2)]


@pytest.mark.parametrize("single", [False, True])
def test_webp_global_resolution_applies_to_every_frame(pillow, single):
    exif = pillow.Exif()
    exif[282], exif[283], exif[296] = 144, 72, 2
    data = _webp(pillow, exif=exif.tobytes())
    for options, size in [
        ({"dpi": "auto"}, PageSize(4, 6)),
        ({"dpi": 144}, PageSize(4, 3)),
    ]:
        for result in _convert(data, single_document=single, **options):
            with Document(result.to_array()) as document:
                assert all(page.size == size for page in document.pages)


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_fixed_page_fitting_uses_full_canvas_for_every_frame(pillow, kind):
    with Document(
        _convert(_animation(pillow, kind), page_size=(20, 20), margin=2)[0].to_array()
    ) as document:
        for page in document.pages:
            absorber = ImagePlacementAbsorber()
            absorber.visit(page)
            rect = absorber.image_placements[0].rectangle
            assert (rect.x, rect.y, rect.width, rect.height) == pytest.approx(
                (6, 7, 8, 6)
            )


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_pages", 2),
        ("max_image_pixels", 47),
        ("max_decoded_stream_bytes", 197),
        ("max_codec_work_bytes", 2000),
        ("max_total_decoded_bytes", 191),
        ("max_container_items", 1),
    ],
)
def test_limits_are_enforced_before_opening_decoder(
    pillow, monkeypatch, kind, limit, value
):
    data = _animation(pillow, kind)

    def forbidden(*args, **kwargs):
        pytest.fail("Limits must be checked before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfResourceLimitException, match=limit):
        _convert(data, limits=PdfLoadLimits(**{limit: value}))


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
@pytest.mark.parametrize("single", [False, True])
def test_cumulative_budget_is_checked_before_seeking_next_frame(
    pillow, monkeypatch, kind, single
):
    data = _animation(pillow, kind)
    plugin = pytest.importorskip(f"PIL.{'Gif' if kind == 'GIF' else 'WebP'}ImagePlugin")
    cls = plugin.GifImageFile if kind == "GIF" else plugin.WebPImageFile
    original = cls.seek

    def guarded(self, frame):
        assert frame == 0, (
            "The next frame must not be sought after exceeding the budget"
        )
        return original(self, frame)

    monkeypatch.setattr(cls, "seek", guarded)
    with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
        _convert(
            data,
            single_document=single,
            limits=PdfLoadLimits(max_total_decoded_bytes=383),
        )


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_global_page_limit_counts_preceding_inputs_before_decoder_creation(
    pillow, monkeypatch, kind
):
    data = _animation(pillow, kind)
    options = ImagesToPdfOptions(limits=PdfLoadLimits(max_pages=3))
    options.add_input(ByteArrayDataSource(write_png(1, 1, "L", b"\xff")))
    options.add_input(ByteArrayDataSource(data))

    def forbidden(*args, **kwargs):
        pytest.fail(
            "Cumulative page limits must be checked before decoder construction"
        )

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfResourceLimitException, match="max_pages"):
        ImagesToPdf().process(options)


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_later_decode_failure_does_not_write_partial_outputs_and_closes_decoder(
    pillow, monkeypatch, tmp_path, kind
):
    data = _animation(pillow, kind)
    original = pillow.open
    images = []

    def opened(*args, **kwargs):
        image = original(*args, **kwargs)
        images.append(image)
        seek = image.seek

        def broken(frame):
            if frame == 2:
                raise OSError("Damaged later frame")
            return seek(frame)

        image.seek = broken
        return image

    monkeypatch.setattr(pillow, "open", opened)
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"original")
    stream = io.BytesIO(b"original")
    options = ImagesToPdfOptions(single_document=False).add_input(
        ByteArrayDataSource(data)
    )
    options.add_output(FileDataSource(target)).add_output(StreamDataSource(stream))
    with pytest.raises(PdfValidationException, match="Damaged later frame"):
        ImagesToPdf().process(options)
    assert target.read_bytes() == stream.getvalue() == b"original"
    assert images and all(image.fp is None for image in images)


def test_gif_counts_headers_and_places_each_frame_before_seeking_next(
    pillow, monkeypatch
):
    data = _gif(pillow)
    plugin = pytest.importorskip("PIL.GifImagePlugin")

    def forbidden(self):
        pytest.fail("GIF frame counting must not make Pillow seek through all frames")

    monkeypatch.setattr(plugin.GifImageFile, "n_frames", property(forbidden))
    placed = []
    original_place = ImagesToPdf._place

    def place(*args, **kwargs):
        original_place(*args, **kwargs)
        placed.append(1)

    original_seek = plugin.GifImageFile.seek

    def seek(self, index):
        assert len(placed) >= index
        return original_seek(self, index)

    monkeypatch.setattr(ImagesToPdf, "_place", staticmethod(place))
    monkeypatch.setattr(plugin.GifImageFile, "seek", seek)
    with Document(_convert(data)[0].to_array()) as document:
        assert document.page_count == len(placed) == 3


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_truncated_late_blocks_are_rejected_before_opening_decoder(
    pillow, monkeypatch, kind
):
    data = _animation(pillow, kind)

    def forbidden(*args, **kwargs):
        pytest.fail("Container bounds must be checked before opening a decoder")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfValidationException, match="Cannot decode"):
        _convert(data[:-2])


def test_webp_conflicting_frame_and_bitstream_dimensions_are_rejected(
    pillow, monkeypatch
):
    data = bytearray(_webp(pillow))
    position = data.index(b"ANMF") + 8
    data[position + 6 : position + 9] = _u24(6)

    def forbidden(*args, **kwargs):
        pytest.fail(
            "WebP frame dimensions must match bitstream dimensions before opening"
        )

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfValidationException, match="Cannot decode WebP"):
        _convert(bytes(data))


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_identical_stored_frames_are_kept_as_separate_pages(pillow, kind):
    data = _animation(pillow, kind, repeat_first=True)
    with Document(_convert(data)[0].to_array()) as document:
        assert document.page_count == 2
        assert document.pages[0].render().pixels == document.pages[1].render().pixels


@pytest.mark.parametrize(
    "mutation", ["extent", "nested_chunk", "bitstream", "missing_animation_header"]
)
def test_invalid_webp_animation_headers_fail_before_decoder_creation(
    pillow, monkeypatch, mutation
):
    data = bytearray(_webp(pillow))
    frame = data.index(b"ANMF") + 8
    if mutation == "extent":
        data[frame : frame + 3] = _u24(100)
    elif mutation == "nested_chunk":
        struct.pack_into("<I", data, frame + 20, len(data))
    elif mutation == "bitstream":
        position = data.index(b"VP8L", frame) + 9
        data[position : position + 4] = struct.pack("<I", 0x0FFFFFFF)
    else:
        start = data.index(b"ANIM")
        del data[start : start + 14]
        struct.pack_into("<I", data, 4, len(data) - 8)

    def forbidden(*args, **kwargs):
        pytest.fail(
            "Malformed animation headers must be checked before decoder construction"
        )

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfValidationException, match="Cannot decode WebP"):
        _convert(bytes(data))


def test_later_gif_frame_cannot_expand_the_checked_canvas(pillow, monkeypatch):
    data = bytearray(_gif(pillow))
    descriptor = data.index(b"\x2c\x04\x00\x00\x00\x02\x00\x02\x00")
    struct.pack_into("<H", data, descriptor + 1, 100)

    def forbidden(*args, **kwargs):
        pytest.fail("Later frame extents must be checked before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfValidationException, match="beyond its canvas"):
        _convert(bytes(data))


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_placement_failure_closes_the_animation_decoder(pillow, monkeypatch, kind):
    data = _animation(pillow, kind)
    original = pillow.open
    images = []

    def opened(*args, **kwargs):
        image = original(*args, **kwargs)
        images.append(image)
        return image

    def failed(*args, **kwargs):
        raise PdfValidationException("Cannot place this frame")

    monkeypatch.setattr(pillow, "open", opened)
    monkeypatch.setattr(ImagesToPdf, "_place", staticmethod(failed))
    with pytest.raises(PdfValidationException, match="Cannot place this frame"):
        _convert(data)
    assert images and all(image.fp is None for image in images)
