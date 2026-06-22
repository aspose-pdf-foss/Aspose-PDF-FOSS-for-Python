"""Tests for :class:`aspose_pdf.facades.PdfFileEditor`."""

import pytest
from pathlib import Path

from aspose_pdf.facades import PdfFileEditor, PdfExtractor
from tests.helpers_make_pdfs import write_min_pdf


class DummySimplePdf:
    """Minimal stand-in for SimplePdf in tests."""

    dummy_pages_map = {}
    last_saved_instance = None
    last_saved_path = None

    def __init__(self):
        self._pages = []
        self.page_contents = []
        self.images = {}
        self.encrypted = False

    @property
    def pages(self):
        return self._pages

    @pages.setter
    def pages(self, value):
        self._pages = value

    @property
    def page_count(self):
        return len(self._pages)

    @classmethod
    def from_file(cls, path):
        instance = cls()
        key = str(path)
        if key in cls.dummy_pages_map:
            instance.pages = list(cls.dummy_pages_map[key])
        return instance

    def save(self, path):
        type(self).last_saved_instance = self
        type(self).last_saved_path = Path(path)


class _FakePdf:
    """Stub for PdfFileEditor.delete tests."""

    def __init__(self, pages):
        self.pages = pages

    @property
    def page_count(self):
        return len(self.pages)

    def save(self, path):
        Path(path).write_text(str(self.page_count))


def _patch_simple_pdf_for_delete(monkeypatch, initial_pages):
    """Patch SimplePdf for delete tests."""

    class _DummySimplePdf:
        @staticmethod
        def from_file(_):
            return _FakePdf(pages=list(initial_pages))

    monkeypatch.setattr("aspose_pdf.facades.SimplePdf", _DummySimplePdf, raising=False)


class _FakeSimplePdfForInsert:
    """Small stub for insert tests."""

    _registry = {}

    def __init__(self):
        self.pages = []
        self.page_contents = []
        self.images = {}

    @classmethod
    def from_file(cls, path):
        p = Path(path)
        if p not in cls._registry:
            raise FileNotFoundError(p)
        original = cls._registry[p]
        copy = cls()
        copy.pages = list(original.pages)
        copy.page_contents = list(original.page_contents)
        copy.images = dict(original.images)
        return copy

    def save(self, path):
        p = Path(path)
        stored = _FakeSimplePdfForInsert()
        stored.pages = list(self.pages)
        stored.page_contents = list(self.page_contents)
        stored.images = dict(self.images)
        self.__class__._registry[p] = stored

    @property
    def page_count(self):
        return len(self.pages)


@pytest.fixture
def reset_dummy():
    """Reset DummySimplePdf state."""
    DummySimplePdf.dummy_pages_map = {}
    DummySimplePdf.last_saved_instance = None
    DummySimplePdf.last_saved_path = None
    yield


def test_concatenate_creates_output(tmp_path: Path):
    """Concatenate two PDFs and verify output exists."""
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    out = tmp_path / "out.pdf"

    write_min_pdf(a)
    write_min_pdf(b)

    editor = PdfFileEditor()
    ok = editor.concatenate([a, b], out)

    assert ok is True
    assert out.exists()
    assert out.stat().st_size >= 0


def test_concatenate_two_pdfs_preserves_order(tmp_path: Path):
    """Concatenate two PDFs and verify combined size."""
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    out = tmp_path / "out.pdf"

    write_min_pdf(a)
    write_min_pdf(b)

    editor = PdfFileEditor()
    ok = editor.concatenate([a, b], out)

    assert ok is True
    assert out.exists()
    assert out.stat().st_size >= a.stat().st_size


def test_extract_creates_output(tmp_path: Path):
    """Extract pages from a PDF and verify output exists."""
    src = tmp_path / "src.pdf"
    out = tmp_path / "extract.pdf"

    write_min_pdf(src)

    editor = PdfFileEditor()
    ok = editor.extract(src, out)

    assert ok is True


def test_insert_pages_into_target(tmp_path: Path):
    """Insert pages from one PDF into another."""
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.pdf"
    output = tmp_path / "output.pdf"

    write_min_pdf(source)

    editor = PdfFileEditor()
    ok_target = editor.concatenate([source, source], target)
    assert ok_target is True
    assert target.exists()

    ok_insert = editor.insert(source, target, output, 2)
    assert ok_insert is True
    assert output.exists()


def test_delete_range_reduces_page_count(tmp_path: Path):
    """Delete a range of pages and verify output is smaller."""
    source_page = tmp_path / "page.pdf"
    write_min_pdf(source_page)

    multi_page_pdf = tmp_path / "multi.pdf"
    editor = PdfFileEditor()
    ok_concat = editor.concatenate(
        [source_page, source_page, source_page], multi_page_pdf
    )
    assert ok_concat is True
    assert multi_page_pdf.exists()

    output_pdf = tmp_path / "out.pdf"
    ok_delete = editor.delete(multi_page_pdf, output_pdf, 2, 3)
    assert ok_delete is True
    assert output_pdf.exists()


def test_get_text_returns_string():
    """PdfExtractor.get_text should return a string."""
    extractor = PdfExtractor()
    result = extractor.get_text()
    assert isinstance(result, str)
    assert result == ""


def test_image_extraction_iterator():
    """Test image extraction iterator."""
    extractor = PdfExtractor()

    class DummyPdf:
        def __init__(self, images):
            self.images = images
            self.page_count = 1
            self.page_contents = []

    extractor._bound_pdf = DummyPdf({"img1": b"data1", "img2": b"data2"})
    extractor.extract_image()

    found = []
    while extractor.has_next_image():
        img = extractor.get_next_image()
        found.append(img)

    assert set(found) == {b"data1", b"data2"}
    assert not extractor.has_next_image()
    assert extractor.get_next_image() is None


def test_extractor_disposed_behavior():
    """Disposed extractor should raise."""
    extractor = PdfExtractor()
    extractor.dispose()
    with pytest.raises(Exception):
        extractor.extract_image()
    with pytest.raises(Exception):
        extractor.has_next_image()
    with pytest.raises(Exception):
        extractor.get_next_image()


class _DummyPdf:
    """Minimal stub for PdfExtractor text tests."""

    def __init__(self, page_contents, images=None):
        self.page_contents = list(page_contents)
        self.page_count = len(self.page_contents)
        self.images = images or {}


def _bind_dummy_pdf(extractor: PdfExtractor, dummy: _DummyPdf) -> None:
    """Inject a dummy PDF into the extractor."""
    extractor._bound_pdf = dummy


def test_extract_text_returns_content():
    """Extract text from a simple PDF page."""
    extractor = PdfExtractor()
    dummy = _DummyPdf([b"BT (Hello World) Tj ET"])
    _bind_dummy_pdf(extractor, dummy)

    extractor.extract_text()
    result = extractor.get_text()
    assert result == "Hello World"


def test_extract_text_empty_document():
    """Empty PDF should produce empty string."""
    extractor = PdfExtractor()
    dummy = _DummyPdf([])
    _bind_dummy_pdf(extractor, dummy)

    extractor.extract_text()
    result = extractor.get_text()
    assert result == ""


def test_extract_text_image_only_document():
    """PDF with only images returns empty string."""
    extractor = PdfExtractor()
    dummy = _DummyPdf([b"q 0 0 200 200 re W n /Im0 Do Q"])
    _bind_dummy_pdf(extractor, dummy)

    extractor.extract_text()
    result = extractor.get_text()
    assert result == ""
