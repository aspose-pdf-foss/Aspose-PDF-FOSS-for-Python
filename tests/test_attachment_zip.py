"""Attachment ZIP export preserves payloads and uses bounded, portable member names."""

from __future__ import annotations

import datetime
import io
import json
import stat
import unicodedata
import zipfile

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine import attachment_zip, file_output
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _read(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize(
    "compression,method",
    [
        ("deflate", zipfile.ZIP_DEFLATED),
        ("stored", zipfile.ZIP_STORED),
        (" DEFLATE ", zipfile.ZIP_DEFLATED),
    ],
)
def test_all_attachments_export_in_sorted_order_with_unchanged_payloads(
    compression, method
):
    with Document() as document:
        document.add_attachment("z.bin", bytes(range(256)))
        document.add_attachment("alpha.txt", b"Hello " * 100)
        document.add_attachment("empty.dat", b"")
        original = dict(document.attachments)
        data = document.export_attachments_zip(compression=compression)
        assert _read(data) == original
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.namelist() == ["alpha.txt", "empty.dat", "z.bin"]
            assert all(info.compress_type == method for info in archive.infolist())
            assert all(
                info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
            )
            assert all(
                stat.S_ISREG(info.external_attr >> 16) for info in archive.infolist()
            )
            assert all(
                stat.S_IMODE(info.external_attr >> 16) == 0o644
                for info in archive.infolist()
            )
        assert document.attachments == original
        assert document.export_attachments_zip(compression=compression) == data


def test_compression_and_compresslevel_affect_size_without_changing_payload():
    with Document() as document:
        document.add_attachment("large.txt", b"repeat this data " * 1000)
        stored = document.export_attachments_zip(compression="stored")
        deflated = document.export_attachments_zip(compresslevel=9)
        uncompressed_deflate = document.export_attachments_zip(compresslevel=0)
        assert len(deflated) < len(stored) // 10
        assert len(deflated) < len(uncompressed_deflate) // 10
        assert _read(deflated) == _read(stored) == _read(uncompressed_deflate)


def test_explicit_names_preserve_order_deduplicate_and_accept_a_single_name():
    with Document() as document:
        for name in ("a.txt", "b.txt", "folder/c.txt"):
            document.add_attachment(name, name.encode())
        data = document.export_attachments_zip(
            names=(name for name in ("folder/c.txt", "b.txt", "folder/c.txt"))
        )
        assert list(_read(data)) == ["c.txt", "b.txt"]
        assert _read(document.export_attachments_zip(names="folder/c.txt")) == {
            "c.txt": b"folder/c.txt"
        }
        assert _read(document.export_attachments_zip(names=[])) == {}


@pytest.mark.parametrize("include_manifest", [False, True])
def test_empty_document_produces_a_valid_empty_archive(include_manifest):
    with Document() as document:
        files = _read(
            document.export_attachments_zip(include_manifest=include_manifest)
        )
    assert files == (
        {"manifest.json": b'{"schema_version":1,"attachments":[]}'}
        if include_manifest
        else {}
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../../evil.txt", "evil.txt"),
        ("/absolute/path.txt", "path.txt"),
        ("C:\\folder\\secret.txt", "secret.txt"),
        ("\\\\server\\share\\note.txt", "note.txt"),
        ("C:drive.txt", "C_drive.txt"),
        ("...", "attachment"),
        ("", "attachment"),
        ("folder/", "attachment"),
        (" .hidden. ", "hidden"),
        ('a<>:"|?*b.txt', "a_______b.txt"),
        ("before\x00after.txt", "before_after.txt"),
        ("line\nfeed.txt", "line_feed.txt"),
        ("rtl\u202etxt.exe", "rtl_txt.exe"),
        ("line\u2028break.txt", "line_break.txt"),
        ("bad\ud800name.txt", "bad_name.txt"),
        ("CON", "_CON"),
        ("aux.txt", "_aux.txt"),
        ("COM1.txt", "_COM1.txt"),
        ("LPT9", "_LPT9"),
        ("COM¹", "_COM¹"),
        ("NUL .txt", "_NUL .txt"),
        ("e\u0301.txt", "é.txt"),
        ("报告.txt", "报告.txt"),
    ],
)
def test_untrusted_member_names_are_flat_safe_and_portable(name, expected):
    with Document() as document:
        document.add_attachment(name, b"Payload")
        files = _read(document.export_attachments_zip(include_manifest=True))
    assert files[expected] == b"Payload"
    manifest = json.loads(files["manifest.json"])
    (record,) = manifest["attachments"]
    assert record["name"] == name and record["archive_name"] == expected
    assert len(expected.encode("utf-8")) <= 240
    assert "/" not in expected and "\\" not in expected
    assert all(
        unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for char in expected
    )


def test_casefold_normalization_and_generated_suffix_collisions_preserve_every_file():
    names = [
        "folder/report.txt",
        "report.txt",
        "REPORT.TXT",
        "report_2.txt",
        "é.txt",
        "e\u0301.txt",
        "Straße.txt",
        "STRASSE.txt",
    ]
    with Document() as document:
        for name in names:
            document.add_attachment(name, name.encode())
        files = _read(document.export_attachments_zip(names=names))
    assert list(files) == [
        "report.txt",
        "report_2.txt",
        "REPORT_3.TXT",
        "report_2_2.txt",
        "é.txt",
        "é_2.txt",
        "Straße.txt",
        "STRASSE_2.txt",
    ]
    assert list(files.values()) == [name.encode() for name in names]
    assert len({name.casefold() for name in files}) == len(names)


@pytest.mark.parametrize(
    "name", ["界" * 200 + ".txt", "a" * 239 + " " + "b" * 30, "x." + "z" * 300]
)
def test_long_names_are_bounded_after_truncation_and_collision_suffixes(name):
    with Document() as document:
        document.add_attachment("left/" + name, b"Left")
        document.add_attachment("right/" + name, b"Right")
        files = _read(document.export_attachments_zip())
    assert len(files) == 2 and set(files.values()) == {b"Left", b"Right"}
    for member in files:
        assert len(member.encode("utf-8")) <= 240
        assert member.strip(" .") == member
    if name.endswith(".txt"):
        assert all(member.endswith(".txt") for member in files)


def test_manifest_metadata_dates_and_reserved_filename_are_preserved():
    created = datetime.datetime(
        2026, 10, 21, 9, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    with Document() as document:
        document.add_attachment(
            "manifest.json",
            b"Real attachment",
            mime="application/json",
            description="Original metadata ✓",
            creation_date=created,
            mod_date="D:20261021102030Z",
            relationship="Data",
        )
        files = _read(document.export_attachments_zip(include_manifest=True))
        assert files["manifest_2.json"] == b"Real attachment"
        manifest = json.loads(files["manifest.json"])
        assert manifest == {
            "schema_version": 1,
            "attachments": [
                {
                    "name": "manifest.json",
                    "archive_name": "manifest_2.json",
                    "size": 15,
                    "mime_type": "application/json",
                    "description": "Original metadata ✓",
                    "relationship": "Data",
                    "creation_date": "2026-10-21T09:15:00+02:00",
                    "mod_date": "2026-10-21T10:20:30+00:00",
                }
            ],
        }
        assert _read(document.export_attachments_zip()) == {
            "manifest.json": b"Real attachment"
        }


def test_file_stream_and_returned_bytes_match_without_closing_the_stream(tmp_path):
    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        expected = document.export_attachments_zip()
        path = tmp_path / "nested" / "files.zip"
        assert document.export_attachments_zip(path) == path.read_bytes() == expected
        stream = io.BytesIO(b"prefix")
        stream.seek(0, 2)
        assert document.export_attachments_zip(stream) == expected
        assert not stream.closed and stream.getvalue() == b"prefix" + expected


def test_binary_stream_short_writes_are_retried():
    class ShortStream(io.BytesIO):
        def write(self, data):
            return super().write(data[:7])

    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        stream = ShortStream()
        data = document.export_attachments_zip(stream)
        assert stream.getvalue() == data and not stream.closed


@pytest.mark.parametrize("result", [None, False, 0, -1, "all", 10**9])
def test_binary_stream_invalid_write_progress_is_rejected(result):
    class StalledStream:
        def write(self, data):
            return result

    with Document() as document:
        with pytest.raises(AsposePdfException, match="Stream write"):
            document.export_attachments_zip(StalledStream())


def test_existing_destination_is_refused_or_atomically_replaced(tmp_path):
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        with pytest.raises(FileExistsError):
            document.export_attachments_zip(path, overwrite=False)
        assert path.read_bytes() == b"Original"
        document.export_attachments_zip(path)
        assert _read(path.read_bytes()) == {"note.txt": b"Note"}


def test_atomic_replace_failure_keeps_existing_file_and_cleans_staging(
    tmp_path, monkeypatch
):
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")

    def fail(*args):
        raise OSError("Cannot replace")

    monkeypatch.setattr(file_output.os, "replace", fail)
    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        with pytest.raises(OSError, match="Cannot replace"):
            document.export_attachments_zip(path)
    assert path.read_bytes() == b"Original" and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "name,value",
    [
        ("compression", None),
        ("compression", "lzma"),
        ("compression", 8),
        ("compresslevel", True),
        ("compresslevel", -1),
        ("compresslevel", 10),
        ("compresslevel", "6"),
        ("compresslevel", 6.5),
        ("include_manifest", 1),
        ("overwrite", None),
        ("names", 3),
        ("names", b"note.txt"),
        ("names", ["note.txt", None]),
        ("names", ["note.txt", 4]),
    ],
)
def test_invalid_options_are_rejected_without_touching_destination(
    tmp_path, monkeypatch, name, value
):
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        monkeypatch.setattr(
            attachment_zip.zipfile,
            "ZipFile",
            lambda *a, **kw: pytest.fail("validate before compression"),
        )
        with pytest.raises(PdfValidationException):
            document.export_attachments_zip(path, **{name: value})
    assert path.read_bytes() == b"Original"


@pytest.mark.parametrize(
    "options",
    [
        dict(compresslevel=-1),
        dict(compression="gzip"),
        dict(include_manifest=1),
        dict(overwrite=1),
    ],
)
def test_empty_selections_still_validate_all_options(options):
    with Document() as document:
        with pytest.raises(PdfValidationException):
            document.export_attachments_zip(names=[], **options)


def test_unknown_late_selection_name_is_rejected_before_compression_or_stream_writes(
    monkeypatch,
):
    with Document() as document:
        document.add_attachment("note.txt", b"Note")
        monkeypatch.setattr(
            attachment_zip.zipfile,
            "ZipFile",
            lambda *a, **kw: pytest.fail("validate the full selection first"),
        )
        output = io.BytesIO(b"Original")
        with pytest.raises(KeyError, match=r"missing\.txt"):
            document.export_attachments_zip(
                output, names=iter(("note.txt", "missing.txt"))
            )
        assert output.getvalue() == b"Original"


@pytest.mark.parametrize("payload", ["text", None, [1, 2]])
def test_invalid_direct_attachment_payload_is_rejected(payload):
    with Document() as document:
        document.attachments["bad.bin"] = payload
        with pytest.raises(PdfValidationException, match="contents must be bytes"):
            document.export_attachments_zip()


def test_mutable_payloads_are_supported_without_modifying_them():
    with Document() as document:
        contents = bytearray(b"Mutable")
        document.attachments["note.txt"] = contents
        assert _read(document.export_attachments_zip()) == {"note.txt": b"Mutable"}
        assert document.attachments["note.txt"] is contents and contents == b"Mutable"


def test_invalid_manifest_metadata_fails_before_destination_write(tmp_path):
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    with Document() as document:
        document.add_attachment("note.txt", b"Note", description=123)
        with pytest.raises(
            PdfValidationException, match="description must be a string"
        ):
            document.export_attachments_zip(path, include_manifest=True)
        assert _read(document.export_attachments_zip()) == {"note.txt": b"Note"}
    assert path.read_bytes() == b"Original"


@pytest.mark.parametrize(
    "limits,options",
    [
        (PdfLoadLimits(max_container_items=1), {}),
        (PdfLoadLimits(max_container_items=1), dict(names=["a", "a"])),
        (PdfLoadLimits(max_container_items=1), dict(names="a", include_manifest=True)),
        (PdfLoadLimits(max_object_bytes=1), dict(names="long")),
        (PdfLoadLimits(max_decoded_stream_bytes=2), dict(names="a")),
        (PdfLoadLimits(max_total_decoded_bytes=5), dict(names=["a", "b"])),
    ],
)
def test_input_limits_are_checked_before_zip_allocation(monkeypatch, limits, options):
    with Document(limits=limits) as document:
        for name in ("a", "b", "long"):
            document.add_attachment(name, b"123")
        monkeypatch.setattr(
            attachment_zip.zipfile,
            "ZipFile",
            lambda *a, **kw: pytest.fail("check limits before compression"),
        )
        with pytest.raises(PdfResourceLimitException):
            document.export_attachments_zip(**options)


def test_manifest_byte_limit_and_metadata_text_limit_are_checked(tmp_path):
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    with Document(limits=PdfLoadLimits(max_object_bytes=100)) as document:
        document.add_attachment("a", b"x")
        with pytest.raises(PdfResourceLimitException, match="manifest bytes"):
            document.export_attachments_zip(path, include_manifest=True)
        document.add_attachment("a", b"x", description="x" * 101)
        with pytest.raises(PdfResourceLimitException, match="attachment description"):
            document.export_attachments_zip(path, include_manifest=True)
    assert path.read_bytes() == b"Original"


def test_archive_output_limit_includes_central_directory_and_exact_boundary(tmp_path):
    with Document() as document:
        document.add_attachment("a", b"x")
        expected = document.export_attachments_zip(compression="stored")
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    with Document(
        limits=PdfLoadLimits(max_codec_work_bytes=len(expected) - 1)
    ) as document:
        document.add_attachment("a", b"x")
        with pytest.raises(PdfResourceLimitException, match="ZIP output bytes"):
            document.export_attachments_zip(path, compression="stored")
    assert path.read_bytes() == b"Original"
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=len(expected))) as document:
        document.add_attachment("a", b"x")
        assert document.export_attachments_zip(compression="stored") == expected


def test_manifest_counts_toward_payload_limits_and_unlimited_limits_work():
    with Document(limits=PdfLoadLimits(max_decoded_stream_bytes=100)) as document:
        document.add_attachment("a", b"x")
        with pytest.raises(PdfResourceLimitException, match="manifest bytes"):
            document.export_attachments_zip(include_manifest=True)
    with Document(limits=PdfLoadLimits.unlimited()) as document:
        document.add_attachment("a", b"x")
        files = _read(document.export_attachments_zip(include_manifest=True))
        total = sum(map(len, files.values()))
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=total - 1)) as document:
        document.add_attachment("a", b"x")
        with pytest.raises(PdfResourceLimitException, match="payload and manifest"):
            document.export_attachments_zip(include_manifest=True)


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_round_trip_sources_and_metadata_stay_unchanged_without_page_decoding(
    tmp_path, monkeypatch, source_kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        source.pages.add(size=(10, 10)).add_text("Private", 1, 1, font_size=2)
        source.add_attachment(
            "资料.txt",
            b"Data",
            mime="text/plain",
            description="Keep",
            creation_date="D:20261021",
            relationship="Source",
        )
        source.add_attachment("folder/notes.txt", b"Notes")
        source.info["Title"] = "Original PDF"
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(path)
    original_bytes = path.read_bytes()
    opener = Document.open_streaming if source_kind == "streaming" else Document
    argument = original_bytes if source_kind == "bytes" else path
    with opener(argument, password="secret" if encrypted else None) as document:
        attachments = dict(document.attachments)
        metadata = dict(document._engine_pdf.attachment_read_meta)
        protected = document.is_encrypted
        monkeypatch.setattr(
            document._engine_pdf,
            "get_page_content",
            lambda *a, **kw: pytest.fail("must not decode page content"),
        )
        data = document.export_attachments_zip(include_manifest=True)
        files = _read(data)
        assert files["资料.txt"] == b"Data" and files["notes.txt"] == b"Notes"
        manifest = json.loads(files["manifest.json"])
        record = next(
            record for record in manifest["attachments"] if record["name"] == "资料.txt"
        )
        assert record["mime_type"] == "text/plain" and record["description"] == "Keep"
        assert (
            record["creation_date"] == "2026-10-21T00:00:00"
            and record["relationship"] == "Source"
        )
        assert document.attachments == attachments
        assert document._engine_pdf.attachment_read_meta == metadata
        assert (
            document.is_encrypted is protected
            and document.info["Title"] == "Original PDF"
        )
        assert document.export_attachments_zip(include_manifest=True) == data
        if source_kind == "streaming":
            assert document._engine_pdf.page_contents == []
            assert document._engine_pdf._extracted_text is None
    assert path.read_bytes() == original_bytes


def test_export_does_not_change_pdf_serialization_or_reuse_stale_attachment_data():
    with Document() as document:
        document.pages.add()
        document.add_attachment("note.txt", b"Original", description="Original")
        before = _saved(document)
        document.export_attachments_zip(include_manifest=True)
        assert _saved(document) == before
        document.update_attachment(
            "note.txt", content=b"Updated", description="Updated"
        )
        files = _read(document.export_attachments_zip(include_manifest=True))
        assert files["note.txt"] == b"Updated"
        assert (
            json.loads(files["manifest.json"])["attachments"][0]["description"]
            == "Updated"
        )


def test_disposed_document_is_rejected():
    document = Document()
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_attachments_zip()


def test_large_manifest_strings_escape_correctly_across_chunk_boundaries():
    description = "a" * 4095 + '"\\\n界😀\ud800' + "b" * 5000
    with Document() as document:
        document.add_attachment("data", b"Data", description=description)
        files = _read(document.export_attachments_zip(include_manifest=True))
    assert (
        json.loads(files["manifest.json"])["attachments"][0]["description"]
        == description
    )


def test_hundreds_of_identical_basenames_have_unique_names_and_unchanged_payloads():
    with Document() as document:
        for index in range(500):
            document.add_attachment(f"folder-{index}/data.txt", str(index).encode())
        files = _read(document.export_attachments_zip())
    assert len(files) == 500
    assert {name.casefold() for name in files} == {"data.txt"} | {
        f"data_{index}.txt" for index in range(2, 501)
    }
    assert set(files.values()) == {str(index).encode() for index in range(500)}


def test_only_selected_payloads_and_metadata_are_inspected():
    with Document() as document:
        document.add_attachment("good", b"Good")
        document.attachments["bad"] = None
        document._engine_pdf.attachment_meta["bad"] = {"description": object()}
        assert _read(document.export_attachments_zip(names="good")) == {"good": b"Good"}


def test_utf8_name_bytes_are_limited_even_when_character_count_fits():
    with Document(limits=PdfLoadLimits(max_object_bytes=2)) as document:
        document.add_attachment("界", b"x")
        with pytest.raises(PdfResourceLimitException, match="attachment name"):
            document.export_attachments_zip()


def test_compression_failure_does_not_write_to_file_or_stream(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("Cannot compress")

    monkeypatch.setattr(attachment_zip, "_write_entry", fail)
    path = tmp_path / "files.zip"
    path.write_bytes(b"Original")
    stream = io.BytesIO(b"Original")
    with Document() as document:
        document.add_attachment("a", b"x")
        for destination in (path, stream, tmp_path / "new" / "files.zip"):
            with pytest.raises(OSError, match="Cannot compress"):
                document.export_attachments_zip(destination)
    assert path.read_bytes() == stream.getvalue() == b"Original"
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("legacy_info", [False, True])
def test_entry_writer_chunks_payloads_and_sets_compression_level_on_supported_python_versions(
    monkeypatch, legacy_info
):
    class Info:
        def __init__(self, name, date_time):
            self.filename, self.date_time = name, date_time
            if not legacy_info:
                self.compress_level = None

    class Member(io.BytesIO):
        def write(self, data):
            assert len(data) <= 64 * 1024
            return super().write(data)

    class Archive:
        def open(self, info, mode):
            self.info = info
            assert mode == "w"
            return Member()

    monkeypatch.setattr(attachment_zip.zipfile, "ZipInfo", Info)
    archive = Archive()
    attachment_zip._write_entry(archive, "data", b"x" * 150000, zipfile.ZIP_DEFLATED, 9)
    info = archive.info
    assert info.compress_type == zipfile.ZIP_DEFLATED and info.file_size == 150000
    assert (info._compresslevel if legacy_info else info.compress_level) == 9
