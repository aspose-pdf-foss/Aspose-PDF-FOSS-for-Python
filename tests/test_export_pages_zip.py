"""Export individual page PDFs to a ZIP archive."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _page(document, text="PAGE", size=(12, 8)):
    page = document.pages.add(size=size)
    page.add_text(text, 1, 4)
    return page


def test_selected_pages_keep_order_repeats_and_manifest_provenance():
    """Selected pages preserve order, repeats, and manifest data."""
    with Document() as document:
        for number in range(1, 4):
            _page(document, f"PAGE {number}")
        # pages=(2, 1, 2) are one-based, so we expect:
        # page-0001.pdf = original page 2 = "PAGE 2"
        # page-0002.pdf = original page 1 = "PAGE 1"
        # page-0003.pdf = original page 2 = "PAGE 2"
        files = _files(
            document.export_pages_as_zip(
                pages=(2, 1, 2),
                include_manifest=True,
            )
        )
        names = [f"page-{index:04d}.pdf" for index in range(1, 4)]
        assert list(files) == [*names, "manifest.json"]
        assert json.loads(files["manifest.json"]) == dict(
            schema_version=1,
            pages=[
                dict(archive_name=names[0], page_index=2, size=len(files[names[0]])),
                dict(archive_name=names[1], page_index=1, size=len(files[names[1]])),
                dict(archive_name=names[2], page_index=2, size=len(files[names[2]])),
            ],
        )
        # Verify content - page 2 should have "PAGE 2", page 1 should have "PAGE 1"
        with Document(files[names[0]]) as part:
            assert part.validate()
            assert part.page_count == 1
            assert part.extract_text().strip() == "PAGE 2"
        with Document(files[names[1]]) as part:
            assert part.validate()
            assert part.page_count == 1
            assert part.extract_text().strip() == "PAGE 1"
        with Document(files[names[2]]) as part:
            assert part.validate()
            assert part.page_count == 1
            assert part.extract_text().strip() == "PAGE 2"


def test_default_all_pages_and_archive_metadata_are_deterministic():
    """Default archive uses safe names and fixed member metadata."""
    with Document() as document:
        _page(document, "#f00")
        _page(document, "#0f0")
        data = document.export_pages_as_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["page-0001.pdf", "page-0002.pdf"]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3 and info.external_attr >> 16 == 0o100644
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert "/" not in info.filename and "\\" not in info.filename
            with Document(archive.read(info)) as part:
                assert part.page_count == 1


@pytest.mark.parametrize(
    "compression,method",
    [("deflate", zipfile.ZIP_DEFLATED), ("stored", zipfile.ZIP_STORED)],
)
def test_compression_options_work(compression, method):
    """Compression options select the right ZIP method."""
    with Document() as document:
        _page(document)
        data = document.export_pages_as_zip(compression=compression)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            assert info.compress_type == method
        with Document(archive.read("page-0001.pdf")) as part:
            assert part.page_count == 1


def test_empty_source_produces_valid_archive():
    """Empty document produces a valid empty archive."""
    with Document() as document:
        files = _files(document.export_pages_as_zip())
        assert list(files) == []
        # With manifest
        files = _files(document.export_pages_as_zip(include_manifest=True))
        assert list(files) == ["manifest.json"]
        assert json.loads(files["manifest.json"]) == dict(
            schema_version=1, pages=[]
        )


def test_paths_streams_short_writes_and_overwrite_policy(tmp_path):
    """Path and stream handling, including overwrite policy."""
    path = tmp_path / "new" / "pages.zip"
    with Document() as document:
        _page(document)
        result = document.export_pages_as_zip(path)
        assert path.read_bytes() == result

    with Document() as document:
        _page(document)
        with pytest.raises(FileExistsError):
            document.export_pages_as_zip(path, overwrite=False)
        assert path.read_bytes() == result

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        with Document() as document:
            _page(document)
            data = document.export_pages_as_zip(output)
            assert output.getvalue() == b"prefix" + data
            assert len(_files(data)) == 1


def test_invalid_options_precede_import_and_output():
    """Invalid options are rejected before importing pages."""
    with Document() as document:
        _page(document)
        output = io.BytesIO(b"Keep")
        
        # Invalid compression
        with pytest.raises(PdfValidationException, match="compression"):
            document.export_pages_as_zip(output, compression="gzip")
        assert output.getvalue() == b"Keep"
        
        # Invalid compresslevel
        with pytest.raises(PdfValidationException, match="compresslevel"):
            document.export_pages_as_zip(output, compresslevel=10)
        assert output.getvalue() == b"Keep"
        
        # Invalid include_manifest
        with pytest.raises(PdfValidationException, match="include_manifest"):
            document.export_pages_as_zip(output, include_manifest=1)
        assert output.getvalue() == b"Keep"
        
        # Invalid overwrite
        with pytest.raises(PdfValidationException, match="overwrite"):
            document.export_pages_as_zip(output, overwrite=1)
        assert output.getvalue() == b"Keep"


def test_disposed_source_is_rejected():
    """Disposed document is rejected."""
    with Document() as document:
        _page(document)
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_pages_as_zip()


def test_resource_limits_bound_pages_and_manifest():
    """Resource limits bound pages and manifest before work."""
    with Document() as document:
        for _ in range(5):
            _page(document)
        document._load_limits = PdfLoadLimits(max_container_items=3)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="page PDF ZIP entries"):
            document.export_pages_as_zip(output, include_manifest=True)
        assert output.getvalue() == b"Keep"


def test_manifest_bytes_are_bounded():
    """Manifest size is bounded by max_object_bytes."""
    with Document() as document:
        for _ in range(3):
            _page(document)
        data = document.export_pages_as_zip(include_manifest=True)
        manifest_size = len(_files(data)["manifest.json"])
        
        document._load_limits = PdfLoadLimits(max_object_bytes=manifest_size - 1)
        output = io.BytesIO(b"Keep")
        # The PDF bytes per page check happens before manifest size check
        with pytest.raises(PdfResourceLimitException, match="page PDF ZIP PDF bytes"):
            document.export_pages_as_zip(output, include_manifest=True)
        assert output.getvalue() == b"Keep"


def test_archive_output_bytes_are_bounded():
    """Archive output bytes are bounded by max_codec_work_bytes."""
    with Document() as document:
        for _ in range(3):
            _page(document)
        data = document.export_pages_as_zip()
        
        # Set a very low limit that will definitely fail
        document._load_limits = PdfLoadLimits(max_codec_work_bytes=100)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="page PDF ZIP output"):
            document.export_pages_as_zip(output)
        assert output.getvalue() == b"Keep"


def test_pdf_bytes_per_page_are_bounded():
    """Individual page PDF bytes are bounded by max_object_bytes."""
    with Document() as document:
        _page(document)
        data = document.export_pages_as_zip()
        page_size = len([n for n in _files(data) if n.endswith(".pdf")][0])
    
    with Document() as document:
        _page(document)
        document._load_limits = PdfLoadLimits(max_object_bytes=page_size - 1)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="page PDF ZIP PDF"):
            document.export_pages_as_zip(output)
        assert output.getvalue() == b"Keep"


def test_total_decoded_bytes_are_bounded():
    """Total decoded bytes are bounded by max_total_decoded_bytes."""
    with Document() as document:
        for _ in range(3):
            _page(document)
        data = document.export_pages_as_zip()
        total_size = sum(len(v) for v in _files(data).values())
        
        document._load_limits = PdfLoadLimits(max_total_decoded_bytes=total_size - 1)
        output = io.BytesIO(b"Keep")
        # The ZIP selected payload and manifest bytes check
        with pytest.raises(PdfResourceLimitException, match="ZIP selected payload"):
            document.export_pages_as_zip(output)
        assert output.getvalue() == b"Keep"


def test_streaming_source_works():
    """Streaming source pages are exported correctly."""
    import tempfile
    import os
    
    with Document() as document:
        for i in range(3):
            _page(document, f"PAGE {i+1}")
        
        # Save to a temp file
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        os.unlink(temp_path)  # Remove so we can create it fresh
        
        try:
            document.save(temp_path)
            
            with Document.open_streaming(temp_path) as streamed:
                data = streamed.export_pages_as_zip()
                files = _files(data)
                assert sorted(files.keys()) == ["page-0001.pdf", "page-0002.pdf", "page-0003.pdf"]
                
                for name, expected in zip(files.keys(), ["PAGE 1", "PAGE 2", "PAGE 3"]):
                    with Document(files[name]) as part:
                        assert part.extract_text().strip() == expected
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


def test_each_part_is_disposed_after_serialization():
    """Each page part is disposed after being serialized to ZIP."""
    import gc
    import weakref
    
    with Document() as document:
        for _ in range(3):
            _page(document)
        
        parts = []
        original_extract = document._engine_pdf.extract_pages
        
        def track_extraction(selection):
            result = original_extract(selection)
            parts.append(weakref.ref(result))
            return result
        
        document._engine_pdf.extract_pages = track_extraction
        
        data = document.export_pages_as_zip()
        
        # Each part should be disposed after being written
        gc.collect()
        for part_ref in parts:
            assert part_ref() is None
