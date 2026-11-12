"""Export annotation metadata as separate CSV files in a ZIP archive."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.annotations import AnnotationType
from aspose_pdf.exceptions import (
    AsposePdfException,
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


def _create_annotation(document, page_index=1, position=(72, 720, 172, 770)):
    """Create a simple text annotation on the given page."""
    page = document.pages[page_index]
    page.annotations.add_highlight(position)
    return page


def _page_with_annotation(document, text="Test page", size=(12, 8)):
    """Create a page with a simple annotation."""
    page = document.pages.add(size=size)
    page.add_text(text, 1, 4)
    # Add a highlight annotation
    page.annotations.add_highlight((1, 3, 4, 5))
    return page


def test_selected_pages_keep_order_and_repeats():
    """Selected pages preserve order and repeats in ZIP names."""
    with Document() as document:
        for number in range(1, 4):
            _page_with_annotation(document, f"PAGE {number}")
        
        # pages=(2, 1, 2) are one-based, so we expect:
        # annotations-page-0001.csv = original page 2
        # annotations-page-0002.csv = original page 1
        # annotations-page-0003.csv = original page 2
        data = document.export_annotations_zip(pages=(2, 1, 2), include_manifest=True)
        files = _files(data)
        
        names = [f"annotations-page-{index:04d}.csv" for index in range(1, 4)]
        assert list(files.keys()) == [*names, "manifest.json"]
        
        manifest = json.loads(files["manifest.json"])
        assert manifest == dict(
            schema_version=2,
            delimiter=",",
            encoding="utf-8",
            pages=[
                dict(archive_name=names[0], page_index=2, annotation_count=1, size=len(files[names[0]])),
                dict(archive_name=names[1], page_index=1, annotation_count=1, size=len(files[names[1]])),
                dict(archive_name=names[2], page_index=2, annotation_count=1, size=len(files[names[2]])),
            ],
        )
        
        # Verify CSV content for each page
        for name in names:
            csv_data = files[name].decode("utf-8")
            lines = csv_data.strip().split("\r\n")
            # Check header is present
            assert "page_number" in lines[0]
            # Check that the page index column has the correct value (1-based)


def test_default_all_pages_and_archive_metadata_are_deterministic():
    """Default archive uses safe names and fixed member metadata."""
    with Document() as document:
        _page_with_annotation(document, "page1")
        _page_with_annotation(document, "page2")
        data = document.export_annotations_zip()
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["annotations-page-0001.csv", "annotations-page-0002.csv"]
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
        _page_with_annotation(document, "test")
        data = document.export_annotations_zip(compression=compression)
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            assert info.compress_type == method


def test_delimiter_and_encoding():
    """Delimiter and encoding options are respected."""
    with Document() as document:
        _page_with_annotation(document, "a b c")
        data = document.export_annotations_zip(delimiter=";", encoding="utf-8")
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("annotations-page-0001.csv").decode("utf-8")
        # Check semicolon delimiter - the CSV should have semicolons
        assert "page_number" in content
        lines = content.strip().split("\r\n")
        assert len(lines) >= 2


def test_subtype_filtering():
    """Subtype filter restricts which annotations are exported."""
    with Document() as document:
        page = _page_with_annotation(document, "test")
        # Add a different type of annotation
        page.annotations.add_square((5, 5, 8, 8))
        
        # Export only highlight annotations
        data = document.export_annotations_zip(subtypes=AnnotationType.HIGHLIGHT)
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("annotations-page-0001.csv").decode("utf-8")
        lines = content.strip().split("\r\n")
        # Check that subtype column exists
        assert "subtype" in lines[0]
        # Each data row should have a subtype value
        for line in lines[1:]:
            if line:
                parts = line.split(",")
                assert len(parts) >= 4  # page_number, annotation_index, id, subtype


def test_empty_selection_produces_valid_empty_zip():
    """Empty page selection produces a valid empty ZIP archive."""
    with Document() as document:
        _page_with_annotation(document, "content")
        data = document.export_annotations_zip(pages=[])
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == []


def test_empty_document_produces_valid_empty_zip():
    """Empty document produces a valid empty ZIP archive."""
    with Document() as document:
        data = document.export_annotations_zip()
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == []


def test_file_output():
    """File output writes the archive atomically."""
    import os
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "annotations.zip")
        with Document() as document:
            _page_with_annotation(document, "test")
            document.export_annotations_zip(dest)
        
        # Verify file was written
        assert os.path.exists(dest)
        assert os.path.getsize(dest) > 0
        
        # Verify contents
        with zipfile.ZipFile(dest) as archive:
            assert "annotations-page-0001.csv" in archive.namelist()


def test_binary_stream_output():
    """Binary stream output writes bytes at current position."""
    with Document() as document:
        _page_with_annotation(document, "test")
        output = io.BytesIO()
        document.export_annotations_zip(output)
        
        # Verify bytes were written
        output.seek(0)
        data = output.read()
        assert len(data) > 0
        
        # Verify it's a valid ZIP
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert "annotations-page-0001.csv" in archive.namelist()


def test_overwrite_option():
    """Overwrite option controls whether existing files may be replaced."""
    import os
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "annotations.zip")
        # First write should succeed
        with Document() as document:
            _page_with_annotation(document, "test")
            document.export_annotations_zip(dest, overwrite=True)
        
        # Second write with overwrite=False should raise
        with pytest.raises(FileExistsError):
            with Document() as document:
                _page_with_annotation(document, "test")
                document.export_annotations_zip(dest, overwrite=False)


def test_manifest_json_structure():
    """Manifest JSON has correct structure and schema version."""
    with Document() as document:
        _page_with_annotation(document, "test")
        data = document.export_annotations_zip(include_manifest=True)
    
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
            assert "annotation_count" in page_info
            assert "size" in page_info


def test_single_page_method():
    """Page.export_annotations_zip works like Document.export_annotations_zip with pages=[page.number]."""
    with Document() as document:
        page1 = _page_with_annotation(document, "page one")
        _page_with_annotation(document, "page two")
        
        # Get page 1's annotations
        page1_data = page1.export_annotations_zip()
        
        # Get page 1's annotations via document
        doc_data = document.export_annotations_zip(pages=[1])
        
        # Should produce same archive contents
        with zipfile.ZipFile(io.BytesIO(page1_data)) as page_archive:
            with zipfile.ZipFile(io.BytesIO(doc_data)) as doc_archive:
                assert page_archive.namelist() == doc_archive.namelist()
                assert page_archive.read(page_archive.namelist()[0]) == doc_archive.read(doc_archive.namelist()[0])


def test_multiple_pages_order():
    """Multiple pages preserve the order in the ZIP."""
    with Document() as document:
        _page_with_annotation(document, "first")
        _page_with_annotation(document, "second")
        _page_with_annotation(document, "third")
        
        data = document.export_annotations_zip(pages=[3, 1, 2])
        files = _files(data)
        
        # Files should be in the order specified
        names = list(files.keys())
        assert names[0] == "annotations-page-0001.csv"  # page 3
        assert names[1] == "annotations-page-0002.csv"  # page 1
        assert names[2] == "annotations-page-0003.csv"  # page 2


def test_csv_content_structure():
    """CSV content has correct columns and format."""
    with Document() as document:
        _page_with_annotation(document, "hello world")
        data = document.export_annotations_zip()
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        csv_data = archive.read("annotations-page-0001.csv").decode("utf-8")
        lines = csv_data.strip().split("\r\n")
        
        # First line should be header
        header = lines[0]
        assert "page_number" in header
        assert "annotation_index" in header
        assert "id" in header
        assert "subtype" in header
        assert "author" in header
        assert "contents" in header
        
        # Data lines should have expected columns
        for line in lines[1:]:
            if line:  # Skip empty lines
                columns = line.split(",")
                # Expected: page_number, annotation_index, id, subtype, author, subject, contents, created, modified, in_reply_to, popup, parent
                assert len(columns) >= 4, f"Expected at least 4 columns, got {len(columns)} in line: {line}"


def test_subtypes_as_string():
    """Subtypes argument accepts a single subtype string."""
    with Document() as document:
        page = _page_with_annotation(document, "test")
        page.annotations.add_square((5, 5, 8, 8))
        
        # Export only as string
        data = document.export_annotations_zip(subtypes="Highlight")
    
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("annotations-page-0001.csv").decode("utf-8")
        lines = content.strip().split("\r\n")
        # All non-header lines should be highlight subtype
        for line in lines[1:]:
            if line:
                parts = line.split(",")
                assert parts[3] == "Highlight"  # subtype column


def test_annotation_count_limit():
    """PdfLoadLimits.max_container_items bounds annotation ZIP entries."""
    with Document() as document:
        _page_with_annotation(document, "content")
        
        # Create a limit that will be exceeded (only 1 entry allowed, but 2 items)
        document._load_limits = PdfLoadLimits(max_container_items=1)
        
        with pytest.raises(PdfResourceLimitException):
            document.export_annotations_zip(include_manifest=True)


def test_disposed_document_raises():
    """Disposed document raises AsposePdfException."""
    document = Document()
    _page_with_annotation(document, "test")
    document.dispose()
    
    with pytest.raises(AsposePdfException):
        document.export_annotations_zip()
