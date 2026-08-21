"""ImagePlacementAbsorber over real documents and pages.

The absorber used to understand only the engine object: handed the obvious
argument -- a `Page` -- it silently produced nothing. It also reported a
placement rectangle scaled by the raster's pixel dimensions, so a 200x100 image
drawn 100pt wide claimed to be 20000pt wide.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document, PdfExtractor
from aspose_pdf.images import ImagePlacementAbsorber

FIXTURES = Path(__file__).parent


def _png(width: int, height: int, rgb: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    raw = b"".join(b"\x00" + bytes(list(rgb) * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _two_page_document(tmp_path: Path) -> Path:
    document = Document()
    document.pages.add().add_image(_png(200, 100), 10, 600, 100, 50)
    document.pages.add().add_image(_png(4, 4, (0, 0, 255)), 20, 20, 40, 40)
    out = tmp_path / "images.pdf"
    document.save(out)
    document.dispose()
    return out


def test_visiting_a_page_collects_only_that_page(tmp_path: Path) -> None:
    path = _two_page_document(tmp_path)
    absorber = ImagePlacementAbsorber()
    with Document(path) as document:
        absorber.visit(document.pages[1])
        assert [p.page_index for p in absorber.image_placements] == [1]
        absorber.visit(document.pages[0])
        assert [p.page_index for p in absorber.image_placements] == [0]


def test_visiting_a_document_collects_every_page(tmp_path: Path) -> None:
    path = _two_page_document(tmp_path)
    with Document(path) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document)
    assert sorted(p.page_index for p in absorber.image_placements) == [0, 1]


def test_placement_rectangle_is_the_size_drawn_on_the_page(tmp_path: Path) -> None:
    """An image fills the unit square of its own space; the ``cm`` carries the size."""
    path = _two_page_document(tmp_path)
    with Document(path) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])

    rectangle = absorber.image_placements[0].rectangle
    assert (rectangle.x, rectangle.y) == (10.0, 600.0)
    assert (rectangle.width, rectangle.height) == (100.0, 50.0)


def test_resolution_is_pixels_over_the_drawn_size(tmp_path: Path) -> None:
    path = _two_page_document(tmp_path)
    with Document(path) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])

    placement = absorber.image_placements[0]
    assert (placement.width, placement.height) == (200, 100)
    # 200 pixels across 100 pt = 144 dpi.
    assert placement.resolution == pytest.approx((144.0, 144.0))


def test_encrypted_document_yields_its_image() -> None:
    with Document(FIXTURES / "fixtures_encrypted_image.pdf", password="user") as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])

    assert len(absorber.image_placements) == 1
    assert absorber.image_placements[0].image_data


def test_page_without_images_yields_nothing(tmp_path: Path) -> None:
    document = Document()
    document.pages.add().add_text("no images here", 72, 700)
    out = tmp_path / "text.pdf"
    document.save(out)
    document.dispose()

    with Document(out) as reloaded:
        absorber = ImagePlacementAbsorber()
        absorber.visit(reloaded.pages[0])
    assert absorber.image_placements == []


def test_pages_reusing_a_resource_name_keep_both_images(tmp_path: Path) -> None:
    """/Im1 on page 1 and /Im1 on page 2 are different images, not one.

    Resource names are page-local, and most producers restart at /Im0 on each
    page; storing images under the bare name lost every collision but the last.
    """
    path = _two_page_document(tmp_path)
    with Document(path) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document)

    by_page = {p.page_index: p for p in absorber.image_placements}
    assert len(by_page) == 2
    assert (by_page[0].width, by_page[0].height) == (200, 100)
    assert (by_page[1].width, by_page[1].height) == (4, 4)
    assert by_page[0].image_data != by_page[1].image_data
    assert by_page[0].name != by_page[1].name


def test_extractor_yields_both_colliding_images(tmp_path: Path) -> None:
    path = _two_page_document(tmp_path)
    extractor = PdfExtractor()
    extractor.bind_pdf(path)
    extractor.extract_image()
    images = []
    while extractor.has_next_image():
        images.append(extractor.get_next_image())
    extractor.close()
    assert len(images) == 2
    assert images[0] != images[1]


def test_one_image_shared_by_two_pages_stays_one_entry() -> None:
    """A single XObject referenced from both pages keeps one key, two placements."""
    image = b"\xff\x00\x00" * 4  # 2x2 RGB
    first = b"q 50 0 0 50 10 10 cm /Im0 Do Q"
    second = b"q 20 0 0 20 5 5 cm /Im0 Do Q"
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 2 /Kids [3 0 R 4 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /XObject << /Im0 7 0 R >> >> /Contents 5 0 R >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /XObject << /Im0 7 0 R >> >> /Contents 6 0 R >>",
            5: _stream(b"<< >>", first),
            6: _stream(b"<< >>", second),
            7: _stream(
                b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 >>",
                image,
            ),
        }
    )
    with Document(io.BytesIO(data)) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document)

    placements = sorted(absorber.image_placements, key=lambda p: p.page_index)
    assert [p.name for p in placements] == ["Im0", "Im0"]
    assert [(p.rectangle.x, p.rectangle.width) for p in placements] == [
        (10.0, 50.0),
        (5.0, 20.0),
    ]


def _stream(dictionary: bytes, content: bytes) -> bytes:
    return (
        dictionary[:-2]
        + f"/Length {len(content)} >>".encode()
        + b"\nstream\n"
        + content
        + b"\nendstream"
    )


def _raw_pdf(objects: dict[int, bytes], root: int = 1) -> bytes:
    """Assemble a minimal PDF from literal object bodies."""
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    top = max(objects) + 1
    out += f"xref\n0 {top}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, top):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {top} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
