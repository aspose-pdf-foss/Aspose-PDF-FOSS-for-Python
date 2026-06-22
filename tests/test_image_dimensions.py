from aspose_pdf.engine.simple_pdf import SimplePdf


def test_image_sizes_field_exists():
    """Test that _image_sizes field exists and is initialized correctly."""
    pdf = SimplePdf()
    assert hasattr(pdf, "_image_sizes")
    assert pdf._image_sizes == {}
    assert isinstance(pdf._image_sizes, dict)


def test_image_dimensions_in_output():
    """Test that actual dimensions are written to the PDF output."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    # Add a dummy image
    pdf.images = {"Img0": b"\x89PNG fake image data"}
    # Manually set dimensions
    pdf._image_sizes = {"Img0": (640, 480)}

    out = pdf.to_bytes()

    # Check that /Width 640 and /Height 480 are present
    assert b"/Width 640" in out
    assert b"/Height 480" in out
    # Ensure the old hardcoded values are NOT used for this image
    # Note: we search for the specific pattern for this image's dict
    # But simply ensuring /Width 1 /Height 1 is NOT present near Subtype /Image is good enough
    # or checking that we don't see the exact old string
    assert b"/Width 1 /Height 1" not in out


def test_image_dimensions_default_fallback():
    """Test fallback to 1x1 if dimensions are missing."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.images = {"Img0": b"fake"}
    # Don't set _image_sizes

    out = pdf.to_bytes()

    assert b"/Width 1" in out
    assert b"/Height 1" in out


def test_image_dimensions_roundtrip():
    """Test that dimensions are preserved after saving and reloading."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.images = {"Img0": b"fake data"}
    pdf._image_sizes = {"Img0": (100, 200)}

    # We need to ensure the data is valid enough to be parsed back
    # The current mock PdfCosParser might handle minimal data.
    # However, to be parsed back, we need to ensure CosExtractor can extract the dimensions.
    # But CosExtractor extracts from the XObject dictionary in the PDF.
    # So if we write it correctly with to_bytes(), from_bytes() should read it back.

    data = pdf.to_bytes()

    # Parse back
    pdf2 = SimplePdf.from_bytes(data)

    assert "Img0" in pdf2.images
    assert "Img0" in pdf2._image_sizes
    assert pdf2._image_sizes["Img0"] == (100, 200)


def test_multiple_images_dimensions():
    """Test output with multiple images having different dimensions."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.images = {"Img1": b"data1", "Img2": b"data2"}
    pdf._image_sizes = {"Img1": (800, 600), "Img2": (300, 300)}

    out = pdf.to_bytes()

    assert b"/Width 800 /Height 600" in out
    assert b"/Width 300 /Height 300" in out
