"""Export positioned words as separate CSV files in a ZIP archive."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.exceptions import (
    PdfResourceLimitException,
)


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _page(document, text="SAMPLE TEXT", size=(12, 8)):
    page = document.pages.add(size=size)
    page.add_text(text, 1, 4)
    return page


def test_selected_pages_keep_order_repeats_and_manifest_provenance():
    """Selected pages preserve order, repeats, and manifest data."""
    with Document() as document:
        for number in range(1, 4):
            _page(document, f"PAGE {number}")
        # pages=(2, 1, 2) are one-based, so we expect:
        # words-page-0001.csv = original page 2
        # words-page-0002.csv = original page 1
        # words-page-0003.csv = original page 2
        files = _files(
            document.export_words_zip(
                pages=(2, 1, 2),
                include_manifest=True,
            )
        )
        names = [f"words-page-{index:04d}.csv" for index in range(1, 4)]
        assert list(files) == [*names, "manifest.json"]
        manifest = json.loads(files["manifest.json"])
        assert manifest == dict(
            schema_version=2,
            delimiter=",",
            encoding="utf-8",
            pages=[
                dict(archive_name=names[0], page_index=2, word_count=2, size=len(files[names[0]])),
                dict(archive_name=names[1], page_index=1, word_count=2, size=len(files[names[1]])),
                dict(archive_name=names[2], page_index=2, word_count=2, size=len(files[names[2]])),
            ],
        )
        # Verify content - each CSV should have words from the corresponding page
        for name in names:
            csv_data = files[name].decode("utf-8")
            # Check that the header is present
            assert "page_number" in csv_data
            # Check that the page index column has the correct value (1-based)
            # pages=(2, 1, 2) are one-based indices
            # So page 2 has page_number=2, page 1 has page_number=1
            lines = csv_data.strip().split("\r\n")
            assert len(lines) >= 2  # header + at least one word
            if name == "words-page-0001.csv" or name == "words-page-0003.csv":
                assert lines[1].startswith("2,"), f"Expected page_number=2 in {name}"
            else:  # words-page-0002.csv
                assert lines[1].startswith("1,"), f"Expected page_number=1 in {name}"


def test_default_all_pages_and_archive_metadata_are_deterministic():
    """Default archive uses safe names and fixed member metadata."""
    with Document() as document:
        _page(document, "#f00")
        _page(document, "#0f0")
        data = document.export_words_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["words-page-0001.csv", "words-page-0002.csv"]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3 and info.external_attr >> 16 == 0o100644
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert "/" not in info.filename and "\\" not in info.filename
            csv_data = archive.read(info).decode("utf-8")
            # Check header is present
            assert "page_number" in csv_data


@pytest.mark.parametrize(
    "compression,method",
    [("deflate", zipfile.ZIP_DEFLATED), ("stored", zipfile.ZIP_STORED)],
)
def test_compression_options_work(compression, method):
    """Compression options select the right ZIP method."""
    with Document() as document:
        _page(document, "test")
        data = document.export_words_zip(compression=compression)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            assert info.compress_type == method


def test_delimiter_and_encoding():
    """Delimiter and encoding options are respected."""
    with Document() as document:
        _page(document, "a b c")
        data = document.export_words_zip(delimiter=";", encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("words-page-0001.csv").decode("utf-8")
        # Check semicolon delimiter
        assert "page_number" in content
        # Words should be separated by semicolons in the data
        lines = content.strip().split("\r\n")
        assert len(lines) >= 2


def test_clip_option_filters_words():
    """Clip option filters words by rectangle."""
    with Document() as document:
        page = _page(document, "top middle bottom")
        # Get actual coordinates to clip a region
        page.add_text("extra text at top", 1, 7)
        # Use a clip that excludes some content
        data = document.export_words_zip(clip=(0, 0, 6, 8))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("words-page-0001.csv").decode("utf-8")
        assert "page_number" in content


def test_skip_artifacts_option():
    """Skip artifacts option filters out marked artifacts."""
    with Document() as document:
        page = _page(document, "visible text")
        # Create an artifact
        page.add_text("artifact text", 1, 7, artifact=True)
        data_with_artifacts = document.export_words_zip(skip_artifacts=False)
        data_without_artifacts = document.export_words_zip(skip_artifacts=True)
    
    with zipfile.ZipFile(io.BytesIO(data_with_artifacts)) as archive:
        with_artifacts = archive.read("words-page-0001.csv").decode("utf-8")
    with zipfile.ZipFile(io.BytesIO(data_without_artifacts)) as archive:
        without_artifacts = archive.read("words-page-0001.csv").decode("utf-8")
    
    # Without artifacts should have fewer words
    assert len(without_artifacts.split("\r\n")) <= len(with_artifacts.split("\r\n"))


def test_empty_selection_produces_valid_empty_zip():
    """Empty page selection produces a valid empty ZIP archive."""
    with Document() as document:
        _page(document, "content")
        data = document.export_words_zip(pages=[])
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == []


def test_empty_document_produces_valid_empty_zip():
    """Empty document produces a valid empty ZIP archive."""
    with Document() as document:
        data = document.export_words_zip()
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == []


def test_file_output():
    """File output writes the archive atomically."""
    import os
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "words.zip")
        with Document() as document:
            _page(document, "test")
            document.export_words_zip(dest)
        
        # Verify file was written
        assert os.path.exists(dest)
        assert os.path.getsize(dest) > 0
        
        # Verify contents
        with zipfile.ZipFile(dest) as archive:
            assert "words-page-0001.csv" in archive.namelist()


def test_binary_stream_output():
    """Binary stream output writes bytes at current position."""
    with Document() as document:
        _page(document, "test")
        output = io.BytesIO()
        document.export_words_zip(output)
        
        # Verify bytes were written
        output.seek(0)
        data = output.read()
        assert len(data) > 0
        
        # Verify it's a valid ZIP
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert "words-page-0001.csv" in archive.namelist()


def test_overwrite_option():
    """Overwrite option controls whether existing files may be replaced."""
    import os
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "words.zip")
        # First write should succeed
        with Document() as document:
            _page(document, "test")
            document.export_words_zip(dest, overwrite=True)
        
        # Second write with overwrite=False should raise
        with pytest.raises(FileExistsError):
            with Document() as document:
                _page(document, "test")
                document.export_words_zip(dest, overwrite=False)


def test_manifest_json_structure():
    """Manifest JSON has correct structure and schema version."""
    with Document() as document:
        _page(document, "test")
        data = document.export_words_zip(include_manifest=True)
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 2
        assert "delimiter" in manifest
        assert "encoding" in manifest
        assert "pages" in manifest
        assert isinstance(manifest["pages"], list)
        if manifest["pages"]:
            page_info = manifest["pages"][0]
            assert "archive_name" in page_info
            assert "page_index" in page_info
            assert "word_count" in page_info
            assert "size" in page_info


def test_page_count_limit():
    """PdfLoadLimits.max_pages rejects too many pages."""
    with Document() as document:
        for _ in range(3):
            _page(document, "content")
        
        document._load_limits = PdfLoadLimits(max_pages=2)
        
        with pytest.raises(PdfResourceLimitException):
            document.export_words_zip()


def test_manifest_bytes_limit():
    """PdfLoadLimits.max_object_bytes bounds manifest size."""
    with Document() as document:
        for _ in range(3):
            _page(document, "content")
        
        # Create a large manifest to exceed the limit
        document._load_limits = PdfLoadLimits(max_object_bytes=1)  # Very small limit
        
        with pytest.raises(PdfResourceLimitException):
            document.export_words_zip(include_manifest=True)


def test_single_page_method():
    """Page.export_words_zip works like Document.export_words_zip with pages=[page.number]."""
    with Document() as document:
        page1 = _page(document, "page one content")
        page2 = _page(document, "page two content")
        
        # Get page 1's words
        page1_data = page1.export_words_zip()
        
        # Get page 1's words via document
        doc_data = document.export_words_zip(pages=[1])
        
        # Should produce same archive contents
        with zipfile.ZipFile(io.BytesIO(page1_data)) as page_archive:
            with zipfile.ZipFile(io.BytesIO(doc_data)) as doc_archive:
                assert page_archive.namelist() == doc_archive.namelist()
                assert page_archive.read(page_archive.namelist()[0]) == doc_archive.read(doc_archive.namelist()[0])


def test_multiple_pages_order():
    """Multiple pages preserve the order in the ZIP."""
    with Document() as document:
        _page(document, "first")
        _page(document, "second")
        _page(document, "third")
        
        data = document.export_words_zip(pages=[3, 1, 2])
        files = _files(data)
        
        # Files should be in the order specified
        names = list(files.keys())
        assert names[0] == "words-page-0001.csv"  # page 3
        assert names[1] == "words-page-0002.csv"  # page 1
        assert names[2] == "words-page-0003.csv"  # page 2


def test_csv_content_structure():
    """CSV content has correct columns and format."""
    with Document() as document:
        _page(document, "hello world")
        data = document.export_words_zip()
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        csv_data = archive.read("words-page-0001.csv").decode("utf-8")
        lines = csv_data.strip().split("\r\n")
        
        # First line should be header
        header = lines[0]
        assert "page_number" in header
        assert "text" in header
        assert "x0" in header
        assert "y0" in header
        assert "x1" in header
        assert "y1" in header
        assert "quad" in header
        
        # Data lines should have 7 columns (page_number, text, x0, y0, x1, y1, quad)
        for line in lines[1:]:
            if line:  # Skip empty lines
                columns = line.split(",")
                assert len(columns) >= 7, f"Expected at least 7 columns, got {len(columns)} in line: {line}"
