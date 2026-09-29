"""An image keeps its own pixel size through a save and a reload.

The writer used to put ``/Width 1 /Height 1`` on every image it emitted,
whatever the samples were, so a reader scaled the picture to a single pixel.
These tests came from fixing that, against the writer of the day; they now go
through the authoring path -- the one that puts an image XObject in a document --
and check the size arrives at the other end rather than checking the bytes of a
particular writer.
"""

from __future__ import annotations

from aspose_pdf.engine.simple_pdf import SimplePdf


def _with_images(*images: tuple[str, int, int]) -> SimplePdf:
    """One page drawing each ``(name, width, height)`` as raw RGB samples."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    for name, width, height in images:
        samples = bytes([(index * 7) % 256 for index in range(width * height * 3)])
        pdf.add_image_to_page(
            0, samples, 10, 10, width, height,
            pixel_width=width, pixel_height=height,
            color_space="DeviceRGB", bits_per_component=8, name=name,
        )
    return pdf


def test_image_sizes_field_exists():
    pdf = SimplePdf()
    assert pdf._image_sizes == {}


def test_an_image_is_written_at_its_own_size():
    pdf = _with_images(("Img0", 64, 48))
    assert pdf._image_sizes["Img0"] == (64, 48)
    out = pdf.to_bytes()
    assert b"/Width 64" in out and b"/Height 48" in out
    # The size that used to be written for everything.
    assert b"/Width 1 /Height 1" not in out


def test_the_size_survives_a_reload():
    data = _with_images(("Img0", 100, 200)).to_bytes()
    reopened = SimplePdf.from_bytes(data)
    assert "Img0" in reopened.images
    assert reopened._image_sizes["Img0"] == (100, 200)


def test_several_images_keep_their_own_sizes():
    pdf = _with_images(("Img1", 80, 60), ("Img2", 30, 30))
    reopened = SimplePdf.from_bytes(pdf.to_bytes())
    assert reopened._image_sizes["Img1"] == (80, 60)
    assert reopened._image_sizes["Img2"] == (30, 30)
    # Each is its own XObject, so one does not take the other's size.
    assert len(reopened.images) == 2
