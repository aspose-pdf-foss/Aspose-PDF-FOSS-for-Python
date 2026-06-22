import pytest

from aspose_pdf.images import ImagePlacementAbsorber, ImagePlacement, Rectangle


class DummyPage:
    """Simple page mock with various image containers."""

    def __init__(self):
        self.image_placements = []
        self.images = {}
        self.resources = {}


def test_visit_finds_images():
    # Prepare a page with images in three supported locations.
    page = DummyPage()
    # Direct ImagePlacement list
    placement = ImagePlacement(name="direct_img", image_data=b"AAA")
    page.image_placements.append(placement)
    # images dict
    page.images["dict_img"] = b"BBB"
    # resources XObject dict
    page.resources["XObject"] = {"res_img": b"CCC"}

    absorber = ImagePlacementAbsorber()
    absorber.visit(page)

    names = {p.name for p in absorber.image_placements}
    assert names == {"direct_img", "dict_img", "res_img"}


def test_visit_no_images():
    page = DummyPage()  # No image data added
    absorber = ImagePlacementAbsorber()
    absorber.visit(page)
    assert absorber.image_placements == []


def test_image_placement_rect_resolution_rotation_matrix():
    """ImagePlacement exposes rectangle, resolution, rotation, and matrix properties."""
    from aspose_pdf.images import ImagePlacement, DEFAULT_IMAGE_DPI

    # Default values when not set
    p = ImagePlacement(name="img1", image_data=b"data")
    assert p.rectangle.x == 0 and p.rectangle.width == 0 and p.rectangle.height == 0
    assert p.resolution == (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI)
    assert p.rotation == 0
    assert p.matrix == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    # With explicit geometry
    rect = Rectangle(10, 20, 100, 50)
    matrix = (2.0, 0.0, 0.0, 2.0, 10.0, 20.0)
    p2 = ImagePlacement(
        name="img2",
        image_data=b"xxx",
        page_index=1,
        rect=rect,
        resolution=(150.0, 150.0),
        rotation=90,
        matrix=matrix,
    )
    assert p2.rectangle.x == 10 and p2.rectangle.y == 20
    assert p2.rectangle.width == 100 and p2.rectangle.height == 50
    assert p2.resolution == (150.0, 150.0)
    assert p2.rotation == 90
    assert p2.matrix == matrix


def test_save_writes_image_file(tmp_path):
    """The save method should write the image bytes to the given file path."""
    # Minimal PNG header (just for the test, content is not validated as an image)
    image_bytes = b"\x89PNG\r\n\x1a\n"
    placement = ImagePlacement(name="test", image_data=image_bytes)

    output_file = tmp_path / "output.png"
    placement.save(output_file)

    assert output_file.is_file(), "The file was not created"
    assert output_file.read_bytes() == image_bytes


def test_save_invalid_path_raises_type_error():
    """Passing a non‑string, non‑path‑like object should raise TypeError."""
    placement = ImagePlacement(name="test", image_data=b"data")
    with pytest.raises(TypeError):
        placement.save(12345)  # Invalid path type


def test_save_on_disposed_instance_raises_runtime_error():
    """If the instance is marked as disposed, save should raise."""
    placement = ImagePlacement(name="test", image_data=b"data")
    placement._disposed = True
    with pytest.raises(Exception):
        placement.save("unused_path.png")


def test_replace_image_updates_content():
    original = b"original_image_bytes"
    new = b"new_image_bytes"
    placement = ImagePlacement(name="img1", image_data=original)
    assert placement.image_data == original
    placement.replace(new)
    assert placement.image_data == new


def test_replace_invalid_image_data_type():
    placement = ImagePlacement(name="img2", image_data=b"data")
    with pytest.raises(TypeError):
        placement.replace("not bytes")
    with pytest.raises(Exception):
        placement.replace(b"")
