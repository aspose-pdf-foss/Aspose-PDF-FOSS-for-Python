"""``PdfExtractor`` reads a document through the engine, not beside it.

The facade parsed page content itself, which is the engine's own job: a
document whose content is loaded lazily gave *no text at all*, and a page with
a damaged content stream lost the text the engine recovers. Its images came
back as the decoded samples -- the pixels, with nothing saying how wide they
are -- so writing one to a ``.png`` produced a file nothing opens.

Each page is now read with ``SimplePdf.extract_page_text`` and each image
reconstructed the way ``save_image`` writes it, through one new engine method
(``image_file``) that ``save_image`` uses too.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.lowcode import ByteArrayDataSource, TextExtractor, TextExtractorOptions

Image = pytest.importorskip("PIL.Image")


def _png(colour: tuple[int, int, int], size: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + bytes(colour) * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _jpeg(colour: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (8, 8), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def document(tmp_path_factory) -> str:
    """Two pages of text, a PNG and a JPEG, saved to a file."""
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    first.add_text("First page text", 72, 700, font_size=12)
    first.add_image(_png((10, 200, 30)), 100, 100, 80, 80)
    second.add_text("Second page text", 72, 700, font_size=12)
    second.add_image(_jpeg((200, 30, 10)), 100, 100, 80, 80)
    path = tmp_path_factory.mktemp("facade") / "document.pdf"
    document.save(path)
    return str(path)


def _bound(source) -> PdfExtractor:
    extractor = PdfExtractor()
    extractor.bind_pdf(source)
    return extractor


# --- text ---------------------------------------------------------------------------------


def test_the_text_is_the_engines(document):
    extractor = _bound(document)
    extractor.extract_text()
    assert extractor.get_text() == Document(document).extract_text()
    assert [extractor.get_next_page_text() for _ in range(2)] == ["First page text", "Second page text"]
    assert not extractor.has_next_page_text()


def test_a_lazily_loaded_document_is_read(document):
    # page_contents is empty until something materialises it; the facade used
    # to iterate that list and report nothing.
    engine = SimplePdf.from_file_lazy(document)
    assert engine.page_contents == []
    extractor = PdfExtractor()
    extractor._bound_pdf = engine
    extractor.extract_text()
    assert extractor.get_text() == engine.extract_text()
    assert "First page text" in extractor.get_text()


def test_a_damaged_content_stream_keeps_the_text_the_engine_recovers():
    document = Document()
    document.pages.add().add_text("Readable", 72, 700, font_size=12)
    buffer = io.BytesIO()
    document.save(buffer)
    data = bytearray(buffer.getvalue())
    data[data.index(b"BT") : data.index(b"BT") + 2] = b"B?"  # a broken operator
    broken = bytes(data)

    extractor = _bound(broken)
    extractor.extract_text()
    assert extractor.get_text() == Document(io.BytesIO(broken)).extract_text() == "Readable"


def test_nothing_bound_reads_as_nothing():
    extractor = PdfExtractor()
    extractor.extract_text()
    assert extractor.get_text() == "" and not extractor.has_next_page_text()


# --- images -------------------------------------------------------------------------------


def test_each_image_comes_back_as_a_file(document):
    extractor = _bound(document)
    extractor.extract_image()
    images = []
    while extractor.has_next_image():
        images.append((extractor.get_next_image_name(), extractor.get_next_image()))
    assert len(images) == 2
    kinds = sorted(Image.open(io.BytesIO(data)).format for _name, data in images)
    assert kinds == ["JPEG", "PNG"]
    # The PNG is the picture that went in, not its raw samples.
    png = next(data for _name, data in images if data[:8] == b"\x89PNG\r\n\x1a\n")
    assert Image.open(io.BytesIO(png)).convert("RGB").getpixel((0, 0)) == (10, 200, 30)
    assert extractor.get_next_image() is None and extractor.get_next_image_name() is None


def test_an_image_is_written_where_it_is_asked_for(document, tmp_path):
    extractor = _bound(document)
    extractor.extract_image()
    written = []
    while extractor.has_next_image():
        written.append(extractor.get_next_image(tmp_path / f"image{len(written)}.png"))
    by_format = {Image.open(path).format: path for path in written}
    # Everything asked for as .png is written under the suffix it really is.
    assert Image.open(by_format["PNG"]).size == (4, 4)
    assert by_format["PNG"].suffix == ".png"
    assert by_format["JPEG"].suffix == ".jpg" and Image.open(by_format["JPEG"]).size == (8, 8)

    stream = io.BytesIO()
    extractor.extract_image()
    assert extractor.get_next_image(stream) == stream.getvalue()


def test_an_image_the_engine_cannot_rebuild_comes_back_as_it_is():
    engine = SimplePdf()
    engine.images = {"raw": b"not an image"}
    extractor = PdfExtractor()
    extractor._bound_pdf = engine
    extractor.extract_image()
    assert extractor.get_next_image() == b"not an image"


# --- the low-code plugin ---------------------------------------------------------------------


def test_the_text_extractor_plugin_writes_its_result(document):
    options = TextExtractorOptions()
    options.add_input(ByteArrayDataSource(open(document, "rb").read()))
    sink = ByteArrayDataSource()
    options.add_output(sink)
    result = TextExtractor().process(options)
    assert result[0].to_string() == Document(document).extract_text()
    assert bytes(sink.data) == result[0].to_array()
