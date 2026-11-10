"""``Document.add_pages_from_images()`` adds one PDF page per image file."""

from __future__ import annotations

import struct
import zlib

import pytest

from aspose_pdf import Document
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


def create_jpeg(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a minimal JPEG image for testing.
    
    This creates a valid JPEG header with dummy JPEG data.
    """
    # Minimal JPEG structure with SOI marker and dummy data
    # This is a minimal valid JPEG for testing purposes
    return b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * (width * height // 10)


def create_bmp(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a minimal BMP image for testing.
    
    This creates a valid BMP header with dummy pixel data.
    """
    row_size = ((width * 3 + 3) // 4) * 4  # Rows are padded to 4-byte boundaries
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size
    
    # BMP File Header (14 bytes)
    file_header = b"BM" + struct.pack("<I", file_size) + b"\x00\x00\x00\x00" + struct.pack("<I", 54)
    
    # DIB Header (BITMAPINFOHEADER - 40 bytes)
    dib_header = struct.pack("<I", 40) + struct.pack("<ii", width, height)
    dib_header += struct.pack("<HH", 1, 24) + b"\x00\x00\x00\x00" + struct.pack("<I", pixel_data_size)
    dib_header += b"\x13\x0B\x00\x00\x13\x0B\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    
    # Pixel data (BGR format, padded to 4-byte boundaries)
    pixel_data = bytearray()
    for _ in range(height):
        for _ in range(width):
            pixel_data.extend(color[::-1])  # BGR order
        # Padding to 4-byte boundary
        padding = row_size - width * 3
        pixel_data.extend(b"\x00" * padding)
    
    return file_header + dib_header + bytes(pixel_data)


def create_gif(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a minimal GIF image for testing.
    
    This creates a valid GIF89a header with dummy data.
    """
    # GIF89a header
    header = b"GIF89a"
    
    # Logical Screen Descriptor
    header += struct.pack("<HH", width, height)
    header += struct.pack("<B", 0xF0)  # Global Color Table + 8-bit color
    header += struct.pack("<B", 0)  # Background color
    header += struct.pack("<B", 0)  # Pixel aspect ratio
    
    # Global Color Table (256 colors, 3 bytes each)
    for i in range(256):
        header += struct.pack("<BBB", i, i, i)
    
    # Image Descriptor
    header += b"\x2C"
    header += struct.pack("<HH", 0, 0)
    header += struct.pack("<HH", width, height)
    header += struct.pack("<B", 0)
    
    # Minimal LZW compressed data
    header += b"\x02\x02\x44\x01\x00"  # Minimal valid LZW data
    
    # Terminator
    header += b"\x3B"
    
    return header
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
    """Non-existent image path raises FileNotFoundError with filename."""
    path = tmp_path / "missing.png"

    with pytest.raises(FileNotFoundError, match=r"Image not found:.*missing\.png"):
        document.add_pages_from_images([str(path)])


def test_add_images_invalid_layout_raises(document, png_100x50, tmp_path):
    """Invalid layout mode raises PdfValidationException."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    with pytest.raises(PdfValidationException, match="layout"):
        document.add_pages_from_images([str(path)], layout="invalid")





def test_add_images_not_a_list_raises(document):
    """Non-list image_paths raises TypeError with type info."""
    with pytest.raises(TypeError, match=r"list.*got str"):
        document.add_pages_from_images("not_a_list")


def test_add_images_not_a_list_int_raises(document):
    """Non-list image_paths raises TypeError with int type info."""
    with pytest.raises(TypeError, match=r"list.*got int"):
        document.add_pages_from_images(123)


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


def test_add_images_none_in_list_raises(document, png_100x50):
    """None in list raises TypeError."""
    with pytest.raises(TypeError, match="None"):
        document.add_pages_from_images([png_100x50, None])


def test_add_images_empty_string_raises(document, tmp_path):
    """Empty string in list raises FileNotFoundError or OSError."""
    with pytest.raises((FileNotFoundError, OSError)):
        document.add_pages_from_images([""])


def test_add_images_mixed_none_bytes_raises(document):
    """List with None and bytes raises TypeError."""
    with pytest.raises(TypeError):
        document.add_pages_from_images([None, b""])


def test_add_images_from_bytes(document, png_100x50):
    """Adding images from bytes works correctly."""
    page_numbers = document.add_pages_from_images([png_100x50])

    assert page_numbers == [1]
    assert document.page_count == 1


def test_add_images_from_bytearray(document, png_100x50):
    """Adding images from bytearray works correctly."""
    page_numbers = document.add_pages_from_images([bytearray(png_100x50)])

    assert page_numbers == [1]
    assert document.page_count == 1


def test_add_images_mixed_paths_and_bytes(document, png_100x50, png_200x150, tmp_path):
    """Adding images with mixed paths and bytes works correctly."""
    # Write one image to file
    path = tmp_path / "image1.png"
    path.write_bytes(png_100x50)

    # Add first from path, second from bytes
    page_numbers = document.add_pages_from_images([str(path), png_200x150])

    assert page_numbers == [1, 2]
    assert document.page_count == 2


def test_add_images_bytes_automatic_size(document, png_100x50):
    """Adding images from bytes uses automatic size from pixels."""
    page_numbers = document.add_pages_from_images([png_100x50])

    assert page_numbers == [1]
    # PNG is 100x50 pixels, at 72 DPI that's 100x50 points
    assert document.pages[1].size.width == pytest.approx(100)
    assert document.pages[1].size.height == pytest.approx(50)


def test_add_images_bytes_custom_dpi(document, png_100x50):
    """Adding images from bytes respects DPI setting."""
    page_numbers = document.add_pages_from_images([png_100x50], dpi=144)

    assert page_numbers == [1]
    # At 144 DPI, 100 pixels becomes 50 points
    assert document.pages[1].size.width == pytest.approx(50)
    assert document.pages[1].size.height == pytest.approx(25)


def test_add_images_bytes_custom_size(document, png_100x50):
    """Adding images from bytes with explicit size works."""
    page_numbers = document.add_pages_from_images([png_100x50], size="A4")

    assert page_numbers == [1]
    assert document.pages[1].size.width == pytest.approx(595.2755905511812, rel=1e-6)
    assert document.pages[1].size.height == pytest.approx(841.89)


def test_add_images_invalid_bytes_raises(document, png_100x50):
    """Invalid image bytes raises PdfValidationException."""
    # Create invalid PNG data (truncated)
    invalid_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with pytest.raises(PdfValidationException, match="image"):
        document.add_pages_from_images([invalid_data])


def test_add_images_empty_bytes_raises(document):
    """Empty bytes raises PdfValidationException."""
    with pytest.raises(PdfValidationException):
        document.add_pages_from_images([b""])


def test_add_pages_from_images_from_bytesio(document, png_100x50):
    """Adding pages from BytesIO works correctly."""
    from io import BytesIO

    image_io = BytesIO(png_100x50)
    page_numbers = document.add_pages_from_images([image_io])

    assert page_numbers == [1]
    assert document.page_count == 1


def test_add_pages_from_images_from_file(document, png_100x50, tmp_path):
    """Adding pages from opened file object works correctly."""
    path = tmp_path / "image.png"
    path.write_bytes(png_100x50)

    with open(path, "rb") as f:
        page_numbers = document.add_pages_from_images([f])

    assert page_numbers == [1]
    assert document.page_count == 1


def test_add_pages_from_images_mixed_sources(document, png_100x50, tmp_path):
    """Adding pages from mixed sources (path, bytes, BytesIO) works."""
    from io import BytesIO

    # Path
    path = tmp_path / "image1.png"
    path.write_bytes(png_100x50)

    # BytesIO
    image_io = BytesIO(png_100x50)

    page_numbers = document.add_pages_from_images([str(path), png_100x50, image_io])

    assert page_numbers == [1, 2, 3]
    assert document.page_count == 3


def test_add_pages_from_images_bytesio_read_returns_str_raises(document, png_100x50):
    """BytesIO that reads str raises TypeError."""
    from io import StringIO

    # StringIO reads str, which should raise TypeError
    string_io = StringIO("not image data")

    with pytest.raises(TypeError, match="read\\(\\) must return bytes or bytearray"):
        document.add_pages_from_images([string_io])


def test_add_pages_from_images_type_name(document):
    """Test that the parameter name in error messages is correct."""
    with pytest.raises(TypeError, match="list"):
        document.add_pages_from_images("not_a_list")
