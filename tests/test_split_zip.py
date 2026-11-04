"""Split PDF archives preserve complete pages and release parts on failure."""

from __future__ import annotations

import gc
import io
import json
import weakref
import zipfile

import pytest

from aspose_pdf import Document, FitDestination, PdfLoadLimits, URIAction
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.outlines import OutlineItem


@pytest.fixture
def source():
    with Document() as document:
        for number in range(1, 6):
            document.pages.add(size=(240, 160)).add_text(f"PAGE {number}", 15, 120)
        document.info["Title"] = "Batch"
        document.info["Author"] = "Test author"
        yield document


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize(
    "size,counts", [(1, [1] * 5), (2, [2, 2, 1]), (5, [5]), (10, [5])]
)
def test_pdf_parts_and_manifest_retain_order_tail_metadata_and_source(
    source, size, counts
):
    source.outlines.add(OutlineItem("Live edit", destination=FitDestination(3)))
    source.pages[3].rotation = 90
    source.pages[3].add_link((10, 10, 60, 30), URIAction("https://example.org"))
    before = _bytes(source)
    model = source._engine_pdf._outlines_data
    files = _files(source.export_split_zip(pages_per_part=size, include_manifest=True))
    names = [f"part-{number:04d}.pdf" for number in range(1, len(counts) + 1)]
    assert list(files) == [*names, "manifest.json"]
    records = []
    start = 1
    for name, count in zip(names, counts):
        with Document(files[name]) as part:
            assert part.validate()
            assert part.page_count == count
            assert [page.extract_text().strip() for page in part.pages] == [
                f"PAGE {number}" for number in range(start, start + count)
            ]
            assert (
                part.info["Title"] == "Batch" and part.info["Author"] == "Test author"
            )
            if start <= 3 < start + count:
                assert part.outlines[0].title == "Live edit"
                assert part.outlines[0].destination.page == 4 - start
                assert part.pages[4 - start].rotation == 90
                assert (
                    part.pages[4 - start].annotations[0].get_property("A")["URI"]
                    == "https://example.org"
                )
        records.append(
            dict(
                archive_name=name,
                start_page=start,
                end_page=start + count - 1,
                page_count=count,
                size=len(files[name]),
            )
        )
        start += count
    assert json.loads(files["manifest.json"]) == dict(
        schema_version=1,
        pages_per_part=size,
        parts=records,
    )
    assert source._engine_pdf._outlines_data is model
    assert source.outlines[0].title == "Live edit" and _bytes(source) == before


@pytest.mark.parametrize(
    "compression,method",
    [("deflate", zipfile.ZIP_DEFLATED), (" STORED ", zipfile.ZIP_STORED)],
)
def test_default_per_page_export_uses_safe_names_and_fixed_member_metadata(
    source, compression, method
):
    data = source.export_split_zip(compression=compression, compresslevel=3)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == [
            f"part-{number:04d}.pdf" for number in range(1, 6)
        ]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3 and info.external_attr >> 16 == 0o100644
            assert info.compress_type == method
            assert "/" not in info.filename and "\\" not in info.filename
            with Document(archive.read(info)) as part:
                assert part.page_count == 1


@pytest.mark.parametrize("include_manifest", [False, True])
def test_empty_source_produces_a_valid_archive(include_manifest):
    with Document() as document:
        files = _files(document.export_split_zip(include_manifest=include_manifest))
        assert list(files) == (["manifest.json"] if include_manifest else [])
        if include_manifest:
            assert json.loads(files["manifest.json"]) == dict(
                schema_version=1, pages_per_part=1, parts=[]
            )


@pytest.mark.parametrize(
    "options,error",
    [
        ({"pages_per_part": 0}, ValueError),
        ({"pages_per_part": -1}, ValueError),
        ({"pages_per_part": True}, TypeError),
        ({"pages_per_part": "2"}, TypeError),
        ({"pages_per_part": 1.5}, TypeError),
        ({"pages_per_part": None}, TypeError),
        ({"compression": "gzip"}, PdfValidationException),
        ({"compression": None}, PdfValidationException),
        ({"compresslevel": -1}, PdfValidationException),
        ({"compresslevel": 10}, PdfValidationException),
        ({"compresslevel": True}, PdfValidationException),
        ({"compresslevel": 1.5}, PdfValidationException),
        ({"include_manifest": 1}, PdfValidationException),
        ({"overwrite": 1}, PdfValidationException),
    ],
)
def test_invalid_options_precede_import_and_output(source, monkeypatch, options, error):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid options must not import pages")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", unexpected)
    output = io.BytesIO(b"Keep")
    with pytest.raises(error):
        source.export_split_zip(output, **options)
    assert output.getvalue() == b"Keep" and not output.closed


def test_paths_streams_short_writes_and_overwrite_policy(source, tmp_path):
    path = tmp_path / "new" / "parts.zip"
    result = source.export_split_zip(path, pages_per_part=2)
    assert path.read_bytes() == result
    with pytest.raises(FileExistsError):
        source.export_split_zip(path, overwrite=False)
    assert path.read_bytes() == result

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        data = source.export_split_zip(output, pages_per_part=2)
        assert output.getvalue() == b"prefix" + data and not output.closed
        assert len(_files(data)) == 3


@pytest.mark.parametrize("failure", ["import", "serialize", "zip"])
def test_later_failure_disposes_parts_restores_source_and_preserves_destination(
    source, tmp_path, monkeypatch, failure
):
    path = tmp_path / "parts.zip"
    path.write_bytes(b"Keep")
    output = io.BytesIO(b"Keep")
    model = source._engine_pdf._outlines_data
    original_import = source._engine_pdf.extract_pages
    original_save = Document.save
    from aspose_pdf.engine import split_zip

    original_write = split_zip._write_entry
    imported = []

    def extract(selection):
        if failure == "import" and imported:
            raise PdfValidationException("Later failure")
        part = original_import(selection)
        imported.append(part)
        return part

    def save(part, *args, **kwargs):
        if failure == "serialize" and len(imported) == 2:
            raise PdfValidationException("Later failure")
        return original_save(part, *args, **kwargs)

    def write(*args):
        if failure == "zip" and len(imported) == 2:
            raise PdfValidationException("Later failure")
        return original_write(*args)

    monkeypatch.setattr(source._engine_pdf, "extract_pages", extract)
    monkeypatch.setattr(Document, "save", save)
    monkeypatch.setattr(split_zip, "_write_entry", write)
    for destination in (path, output):
        imported.clear()
        with pytest.raises(PdfValidationException, match="Later failure"):
            source.export_split_zip(destination, pages_per_part=2)
        assert path.read_bytes() == output.getvalue() == b"Keep"
        assert source._engine_pdf._outlines_data is model
        assert source.page_count == 5
        for part in imported:
            with pytest.raises(AsposePdfException, match="disposed"):
                part._ensure_not_disposed()


def test_parts_are_released_before_importing_the_next_without_text_or_rendering(
    source, monkeypatch
):
    original = source._engine_pdf.extract_pages
    previous = []

    def extract(selection):
        gc.collect()
        assert all(reference() is None for reference in previous)
        part = original(selection)
        previous.append(weakref.ref(part))
        return part

    def unexpected(*args, **kwargs):
        raise AssertionError("Splitting must not extract text or render pages")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", extract)
    monkeypatch.setattr(SimplePdf, "extract_text", unexpected)
    monkeypatch.setattr(Document, "render_page", unexpected)
    assert len(_files(source.export_split_zip(pages_per_part=2))) == 3
    gc.collect()
    assert len(previous) == 3 and all(reference() is None for reference in previous)


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_encrypted_streaming_source_yields_independent_unencrypted_pdfs(
    source, tmp_path, algorithm
):
    path = tmp_path / "encrypted.pdf"
    source.encrypt(user_password="secret", owner_password="owner", algorithm=algorithm)
    source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(path, password="secret") as document:
        files = _files(document.export_split_zip(pages_per_part=2))
        assert document._engine_pdf._lazy and document._engine_pdf.page_contents == []
    assert path.read_bytes() == before
    assert list(files) == ["part-0001.pdf", "part-0002.pdf", "part-0003.pdf"]
    for name, count in zip(files, [2, 2, 1]):
        with Document(files[name]) as part:
            assert part.page_count == count and part.validate()


@pytest.mark.parametrize(
    "limit,options",
    [
        ("max_object_bytes", {}),
        ("max_decoded_stream_bytes", {}),
        ("max_total_decoded_bytes", {}),
        ("max_codec_work_bytes", {}),
        ("max_container_items", {"include_manifest": True}),
    ],
)
def test_archive_limits_preserve_destination(source, limit, options):
    source._load_limits = PdfLoadLimits(**{limit: 5})
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfResourceLimitException, match=limit):
        source.export_split_zip(output, **options)
    assert output.getvalue() == b"Keep"


def test_cumulative_pdf_and_manifest_bytes_are_bounded(source, monkeypatch):
    from aspose_pdf.engine.encryption import EncryptionUtils

    monkeypatch.setattr(EncryptionUtils, "generate_file_id", lambda: b"\0" * 16)
    files = _files(source.export_split_zip(pages_per_part=2))
    total = sum(map(len, files.values()))
    output = io.BytesIO(b"Keep")
    for limit, manifest in [(total - 1, False), (total, True)]:
        source._load_limits = PdfLoadLimits(max_total_decoded_bytes=limit)
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            source.export_split_zip(output, pages_per_part=2, include_manifest=manifest)
        assert output.getvalue() == b"Keep"


def test_manifest_and_central_directory_growth_are_bounded():
    with Document() as empty:
        files = _files(empty.export_split_zip(include_manifest=True))
        empty._load_limits = PdfLoadLimits(
            max_object_bytes=len(files["manifest.json"]) - 1
        )
        with pytest.raises(PdfResourceLimitException, match="split ZIP manifest bytes"):
            empty.export_split_zip(include_manifest=True)
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=21)) as empty:
        with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes"):
            empty.export_split_zip()


def test_disposed_source_and_stalled_output_are_rejected(source):
    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="Stream write"):
        source.export_split_zip(Stalled())
    source.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        source.export_split_zip()
