"""``Document.add_pages_from_images()`` adds one PDF page per image file."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import PdfValidationException


@pytest.fixture
def document():
    """Create a Document for testing."""
    return Document()


def png_chunk(tag: bytes, payload: bytes) -> bytes:
    """Create a PNG chunk with length, tag, payload, and CRC."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def create_png(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a simple PNG image with solid color."""
    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        for _ in range(width):
            raw.extend(color)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)

    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")


@pytest.fixture
def png_100x50():
    """Create a 100x50 PNG image."""
    return create_png(100, 50)


@pytest.fixture
def png_200x150():
    """Create a 200x150 PNG image."""
    return create_png(200, 150, (0, 128, 255))


@pytest.fixture
def png_640x480():
    """Create a 640x480 PNG image."""
    return create_png(640, 480, (255, 255, 0))


def test_add_single_image_returns_one_page_number(document, png_100x50, tmp_path):
    """Adding one image returns a list with one page number."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    page_numbers = document.add_pages_from_images([str(path)])

    assert page_numbers == [1]
    assert document.page_count == 1


def test_add_multiple_images_returns_multiple_page_numbers(document, png_100x50, png_200x150, tmp_path):
    """Adding multiple images returns page numbers in order."""
    path1 = tmp_path / "image1.png"
    path1.write_bytes(png_100x50)
    path2 = tmp_path / "image2.png"
    path2.write_bytes(png_200x150)

    page_numbers = document.add_pages_from_images([str(path1), str(path2)])

    assert page_numbers == [1, 2]
    assert document.page_count == 2


def test_add_images_preserves_order(document, png_100x50, png_200x150, png_640x480, tmp_path):
    """Pages are added in the same order as image paths."""
    paths = []
    for i, png_data in enumerate([png_100x50, png_200x150, png_640x480]):
        path = tmp_path / f"image{i}.png"
        path.write_bytes(png_data)
        paths.append(str(path))

    page_numbers = document.add_pages_from_images(paths)

    assert page_numbers == [1, 2, 3]
    assert document.page_count == 3


def test_add_images_from_path_objects(document, png_100x50, tmp_path):
    """Adding images works with Path objects."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    page_numbers = document.add_pages_from_images([path])

    assert page_numbers == [1]


def test_add_images_empty_list_returns_empty(document):
    """Adding no images returns an empty list."""
    page_numbers = document.add_pages_from_images([])

    assert page_numbers == []
    assert document.page_count == 0


def test_add_images_automatic_size_from_pixels(document, tmp_path):
    """Without explicit size, page matches image size at 72 DPI."""
    png_data = create_png(200, 100)
    path = tmp_path / "image.png"
    path.write_bytes(png_data)

    document.add_pages_from_images([str(path)])

    assert document.pages[1].size.width == pytest.approx(200)
    assert document.pages[1].size.height == pytest.approx(100)


def test_add_images_custom_dpi_changes_size(document, tmp_path):
    """Changing DPI scales the automatic page size."""
    png_data = create_png(200, 100)
    path = tmp_path / "image.png"
    path.write_bytes(png_data)

    document.add_pages_from_images([str(path)], dpi=144)

    assert document.pages[1].size.width == pytest.approx(100)
    assert document.pages[1].size.height == pytest.approx(50)


def test_add_images_explicit_size_override(document, tmp_path):
    """Explicit size overrides automatic sizing."""
    png_data = create_png(200, 100)
    path = tmp_path / "image.png"
    path.write_bytes(png_data)

    document.add_pages_from_images([str(path)], size="A4")

    assert document.pages[1].size.width == pytest.approx(595.2755905511812, rel=1e-6)
    assert document.pages[1].size.height == pytest.approx(841.89)


def test_add_images_custom_page_size(document, tmp_path):
    """Custom (width, height) pair is respected."""
    png_data = create_png(200, 100)
    path = tmp_path / "image.png"
    path.write_bytes(png_data)

    document.add_pages_from_images([str(path)], size=(300, 400))

    assert document.pages[1].size.width == 300
    assert document.pages[1].size.height == 400


def test_add_images_page_size_string_names(document, tmp_path):
    """Page size can be specified by name."""
    png_data = create_png(200, 100)
    path = tmp_path / "image.png"
    path.write_bytes(png_data)

    document.add_pages_from_images([str(path)], size="LETTER")

    assert document.pages[1].size.width == 612
    assert document.pages[1].size.height == 792


def test_add_images_nonexistent_file_raises(document, tmp_path):
    """Non-existent image path raises FileNotFoundError."""
    path = tmp_path / "missing.png"

    with pytest.raises(FileNotFoundError):
        document.add_pages_from_images([str(path)])


def test_add_images_invalid_layout_raises(document, png_100x50, tmp_path):
    """Invalid layout mode raises PdfValidationException."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    with pytest.raises(PdfValidationException, match="layout"):
        document.add_pages_from_images([str(path)], layout="invalid")


def test_add_images_not_a_list_raises(document):
    """Non-list image_paths raises TypeError."""
    with pytest.raises(TypeError, match="list"):
        document.add_pages_from_images("not_a_list")


def test_add_png_images(document, png_100x50, tmp_path):
    """PNG images are added successfully."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    page_numbers = document.add_pages_from_images([str(path)])

    assert page_numbers == [1]


def test_add_images_roundtrip_save(document, png_100x50, tmp_path):
    """Pages from images can be saved and re-opened."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    document.add_pages_from_images([str(path)])

    out_path = tmp_path / "output.pdf"
    document.save(out_path)

    reloaded = Document(str(out_path))

    assert reloaded.page_count == 1
