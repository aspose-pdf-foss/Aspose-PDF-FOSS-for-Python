"""Tests for Document.extract_images() and Page.extract_images()."""
import zlib

import pytest

from aspose_pdf import Document, Image, PageSize
from aspose_pdf.engine.simple_pdf import SimplePdf


def _make_image_data() -> bytes:
    """Create minimal valid PNG data (1x1 pixel, blue)."""
    # Minimal PNG: IHDR + IDAT + IEND
    # IHDR chunk: 1x1, 8-bit RGB
    ihdr = b"\x00\x00\x00\x0dIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    ihdr_crc = b"\x90\x77\x53\xde"
    
    # IDAT chunk: single pixel (R=0, G=0, B=255)
    raw_data = b"\xff\x00\x00"
    compressed = zlib.compress(raw_data)
    idat = b"\x00\x00\x00\x0cIDAT" + compressed + b"\x87\x8d\xdb\x4a"
    
    # IEND chunk
    iend = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
    
    return b"\x89PNG\r\n\x1a\n" + ihdr + ihdr_crc + idat + iend


class TestExtractImages:
    """Tests for image extraction from documents and pages."""

    def test_extract_images_from_document_with_one_image(self, tmp_path):
        """A document with one image should return one Image object."""
        image_data = _make_image_data()
        
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        
        pdf._image_sizes["Im1"] = (10, 10)
        pdf._image_meta["Im1"] = {
            "width": 10,
            "height": 10,
            "bpc": 8,
            "cs_kind": "rgb",
            "n_comps": 3,
        }
        pdf._cached_images = {"Im1": image_data}
        pdf._cached_pmap = {0: ["Im1"]}
        
        doc = Document()
        doc._engine_pdf = pdf
        
        images = doc.extract_images()
        
        assert len(images) == 1
        img = images[0]
        assert isinstance(img, Image)
        assert img.name == "Im1"
        assert img.size == len(image_data)
        assert img.data == image_data

    def test_extract_images_from_page_with_one_image(self, tmp_path):
        """A page with one image should return one Image object."""
        image_data = _make_image_data()
        
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf._image_sizes["Im1"] = (10, 10)
        pdf._image_meta["Im1"] = {
            "width": 10,
            "height": 10,
            "bpc": 8,
            "cs_kind": "rgb",
            "n_comps": 3,
        }
        pdf._cached_images = {"Im1": image_data}
        pdf._cached_pmap = {0: ["Im1"]}
        
        doc = Document()
        doc._engine_pdf = pdf
        
        page = doc.pages[1]
        images = page.extract_images()
        
        assert len(images) == 1
        img = images[0]
        assert isinstance(img, Image)
        assert img.name == "Im1"

    def test_extract_images_from_document_multiple_pages(self, tmp_path):
        """Document with images on multiple pages should return all images."""
        image_data1 = _make_image_data()
        image_data2 = _make_image_data() * 2
        
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792), (0, 0, 612, 792)]
        pdf._image_sizes["Im1"] = (10, 10)
        pdf._image_sizes["Im2"] = (20, 20)
        pdf._image_meta["Im1"] = {"width": 10, "height": 10, "bpc": 8, "cs_kind": "rgb", "n_comps": 3}
        pdf._image_meta["Im2"] = {"width": 20, "height": 20, "bpc": 8, "cs_kind": "rgb", "n_comps": 3}
        pdf._cached_images = {"Im1": image_data1, "Im2": image_data2}
        pdf._cached_pmap = {0: ["Im1"], 1: ["Im2"]}
        
        doc = Document()
        doc._engine_pdf = pdf
        
        images = doc.extract_images()
        
        assert len(images) == 2
        names = {img.name for img in images}
        assert names == {"Im1", "Im2"}

    def test_extract_images_empty_document(self, tmp_path):
        """An empty document should return an empty list."""
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf._cached_images = {}
        pdf._cached_pmap = {}
        
        doc = Document()
        doc._engine_pdf = pdf
        
        images = doc.extract_images()
        
        assert images == []

    def test_extract_images_disposed_document(self, tmp_path):
        """A disposed document should raise an exception."""
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        
        doc = Document()
        doc._engine_pdf = pdf
        doc.dispose()
        
        with pytest.raises(Exception):
            doc.extract_images()

    def test_extract_images_page_disposed(self, tmp_path):
        """A disposed document with page should raise an exception."""
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        
        doc = Document()
        doc._engine_pdf = pdf
        doc.dispose()
        
        page = doc.pages[1]
        with pytest.raises(Exception):
            page.extract_images()

    def test_image_class_attributes(self, tmp_path):
        """Image class should have the expected attributes."""
        image_data = b"test image data"
        img = Image(data=image_data, name="test_img", size=len(image_data), format=None)
        
        assert img.data == image_data
        assert img.name == "test_img"
        assert img.size == len(image_data)
        assert img.format is None

    def test_image_repr(self, tmp_path):
        """Image repr should be informative."""
        image_data = b"test"
        img = Image(data=image_data, name="test_img", size=4, format=None)
        
        repr_str = repr(img)
        assert "test_img" in repr_str
        assert "4 bytes" in repr_str

    def test_page_extract_images_selective(self, tmp_path):
        """Page.extract_images() should only return images for that page."""
        image_data1 = _make_image_data()
        image_data2 = _make_image_data() * 2
        
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792), (0, 0, 612, 792)]
        pdf._image_sizes["Im1"] = (10, 10)
        pdf._image_sizes["Im2"] = (20, 20)
        pdf._image_meta["Im1"] = {"width": 10, "height": 10, "bpc": 8, "cs_kind": "rgb", "n_comps": 3}
        pdf._image_meta["Im2"] = {"width": 20, "height": 20, "bpc": 8, "cs_kind": "rgb", "n_comps": 3}
        pdf._cached_images = {"Im1": image_data1, "Im2": image_data2}
        pdf._cached_pmap = {0: ["Im1"], 1: ["Im2"]}
        
        doc = Document()
        doc._engine_pdf = pdf
        
        page1_images = doc.pages[1].extract_images()
        assert len(page1_images) == 1
        assert page1_images[0].name == "Im1"
        
        page2_images = doc.pages[2].extract_images()
        assert len(page2_images) == 1
        assert page2_images[0].name == "Im2"
