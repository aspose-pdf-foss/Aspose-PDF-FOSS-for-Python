"""ZIP attachment import validates complete batches before changing the document."""

from __future__ import annotations

import io
import json
import stat
import struct
import warnings
import zipfile
import zlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine import attachment_zip_import
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _zip(entries=(), *, compression=zipfile.ZIP_STORED, manifest=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
        if manifest is not None:
            archive.writestr(
                "manifest.json",
                manifest if isinstance(manifest, bytes) else json.dumps(manifest),
            )
    return output.getvalue()


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _record(member="a.txt", name="original/a.txt", **updates):
    return {
        "name": name,
        "archive_name": member,
        "size": 1,
        "mime_type": "text/plain",
        "description": "Description",
        "relationship": "Data",
        "creation_date": "2026-10-23T10:20:30+02:00",
        "mod_date": None,
        **updates,
    }


def _manifest(*records):
    return {"schema_version": 1, "attachments": list(records)}


def _assert_rejected(data, *, limits=None, exception=PdfValidationException, **options):
    with Document(limits=limits) as document:
        document.pages.add()
        document.add_attachment(
            "existing", b"Keep", description="Keep", relationship="Source"
        )
        attachments = document.attachments
        before = dict(attachments)
        meta = deepcopy(document._engine_pdf.attachment_meta)
        read_meta = deepcopy(document._engine_pdf.attachment_read_meta)
        with pytest.raises(exception):
            document.import_attachments_zip(data, **options)
        assert document.attachments is attachments
        assert document.attachments == before
        assert document._engine_pdf.attachment_meta == meta
        assert document._engine_pdf.attachment_read_meta == read_meta


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize(
    "source_kind", ["bytes", "bytearray", "path", "str", "stream", "short_stream"]
)
def test_sources_payloads_and_file_round_trip(tmp_path, compression, source_kind):
    expected = {"资料.bin": bytes(range(256)), "empty": b"", "big": b"abc" * 100000}
    data = _zip(expected.items(), compression=compression)
    path = tmp_path / "input.zip"
    path.write_bytes(data)

    class ShortStream(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 7))

    stream = (ShortStream if source_kind == "short_stream" else io.BytesIO)(
        b"prefix" + data
    )
    stream.seek(6)
    source = {
        "bytes": data,
        "bytearray": bytearray(data),
        "path": path,
        "str": str(path),
        "stream": stream,
        "short_stream": stream,
    }[source_kind]
    with Document() as document:
        document.pages.add()
        assert document.import_attachments_zip(source) == 3
        assert document.attachments == expected
        assert not stream.closed
        output = tmp_path / "imported.pdf"
        document.save(output)
        saved = _saved(document)
    for argument in (output, saved):
        with Document(argument) as reopened:
            assert reopened.attachments == expected
    assert path.read_bytes() == data


def test_non_seekable_stream_is_supported_and_left_open():
    class Stream:
        def __init__(self, data):
            self.input = io.BytesIO(data)

        def read(self, size):
            return self.input.read(size)

    source = Stream(_zip([("a", b"a")]))
    with Document() as document:
        assert document.import_attachments_zip(source) == 1
    assert not source.input.closed


@pytest.mark.parametrize("entries", [[], [("folder/", b"")], [("folder\\", b"")]])
def test_empty_archives_and_directories_are_noops(entries):
    with Document() as document:
        assert document.import_attachments_zip(_zip(entries)) == 0
        assert document.attachments == {}


def test_selection_is_exact_ordered_and_deduplicated():
    data = _zip([("z/a.txt", b"Z"), ("x/a.txt", b"X"), ("b", b"B"), ("folder/", b"")])
    with Document() as document:
        assert (
            document.import_attachments_zip(
                data,
                names=(n for n in ["x/a.txt", "z/a.txt", "x/a.txt"]),
                on_conflict="rename",
            )
            == 2
        )
        assert list(document.attachments.items()) == [
            ("a.txt", b"X"),
            ("a_2.txt", b"Z"),
        ]
        assert document.import_attachments_zip(data, names="b") == 1
        assert document.import_attachments_zip(data, names=[]) == 0
    for name in ("a.txt", "folder/", "missing"):
        _assert_rejected(data, names=name, exception=KeyError)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../../unsafe.txt", "unsafe.txt"),
        ("/absolute/data.txt", "data.txt"),
        ("C:\\folder\\secret.txt", "secret.txt"),
        ("C:drive.txt", "C_drive.txt"),
        ("...", "attachment"),
        (" .hidden. ", "hidden"),
        ("CON.txt", "_CON.txt"),
        ("a<>|?*.txt", "a_____.txt"),
        ("line\nfeed.txt", "line_feed.txt"),
        ("rtl\u202etxt.exe", "rtl_txt.exe"),
        ("e\u0301.txt", "é.txt"),
        ("资料.txt", "资料.txt"),
        ("文" * 200 + ".txt", "文" * 78 + ".txt"),
    ],
)
def test_plain_names_are_portable_basenames_without_disk_extraction(
    tmp_path, monkeypatch, name, expected
):
    monkeypatch.chdir(tmp_path)
    with Document() as document:
        assert document.import_attachments_zip(_zip([(name, b"Contents")])) == 1
        assert document.attachments == {expected: b"Contents"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "policy,expected,count",
    [
        ("skip", {"a.txt": b"Keep", "new": b"New"}, 1),
        ("replace", {"a.txt": b"Second", "new": b"New"}, 3),
        (
            "rename",
            {
                "a.txt": b"Keep",
                "a_2.txt": b"First",
                "a_3.txt": b"Second",
                "new": b"New",
            },
            3,
        ),
    ],
)
def test_conflicts_with_existing_and_earlier_imports(policy, expected, count):
    data = _zip([("dir/a.txt", b"First"), ("other/a.txt", b"Second"), ("new", b"New")])
    with Document() as document:
        document.add_attachment("a.txt", b"Keep", mime="text/plain", description="Keep")
        mapping = document.attachments
        assert document.import_attachments_zip(data, on_conflict=policy) == count
        assert document.attachments is mapping
        assert document.attachments == expected
        if policy == "skip":
            assert document.get_embedded_file("a.txt").description == "Keep"
        if policy == "replace":
            assert document.get_embedded_file("a.txt").description is None
            assert document.get_embedded_file("a.txt").mime_type is None


def test_error_policy_rejects_conflicts_atomically():
    _assert_rejected(_zip([("new", b"New"), ("existing", b"Replace")]))
    _assert_rejected(_zip([("x/a", b"X"), ("y/a", b"Y")]))


def test_rename_avoids_generated_names_and_keeps_normal_names_bounded():
    long_name = "文" * 78 + ".txt"
    with Document() as document:
        document.add_attachment("a_2.txt", b"Keep")
        data = _zip(
            [
                ("a.txt", b"1"),
                ("b/a.txt", b"2"),
                ("c/a.txt", b"3"),
                (long_name, b"4"),
                ("dir/" + long_name, b"5"),
            ]
        )
        assert document.import_attachments_zip(data, on_conflict="rename") == 5
        assert document.attachments["a_3.txt"] == b"2"
        assert document.attachments["a_4.txt"] == b"3"
        assert all(len(n.encode()) <= 240 for n in document.attachments)


def test_pdf_name_conflicts_are_case_sensitive():
    with Document() as document:
        assert document.import_attachments_zip(_zip([("A", b"A"), ("a", b"a")])) == 2


@pytest.mark.parametrize("names", [None, "data.bin"])
def test_export_manifest_restores_names_and_all_metadata(tmp_path, names):
    created = datetime(
        2026, 10, 23, 10, 20, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    modified = datetime(2026, 10, 23, 11, 20, 30)
    with Document() as source:
        source.add_attachment(
            "folder/data.bin",
            b"Data",
            mime="application/custom",
            description="Описание",
            creation_date=created,
            mod_date=modified,
            relationship="Source",
        )
        source.add_attachment("manifest.json", b"Actual attachment")
        data = source.export_attachments_zip(include_manifest=True)
    with Document() as document:
        document.pages.add()
        assert document.import_attachments_zip(data, names=names) == (
            2 if names is None else 1
        )
        spec = document.get_embedded_file("folder/data.bin")
        assert spec.contents == b"Data" and spec.description == "Описание"
        assert spec.mime_type == "application/custom" and spec.relationship == "Source"
        assert spec.creation_date == created and spec.mod_date == modified
        if names is None:
            assert document.attachments["manifest.json"] == b"Actual attachment"
        path = tmp_path / "round-trip.pdf"
        document.save(path)
    with Document(path) as reopened:
        assert reopened.get_embedded_file("folder/data.bin") == spec


def test_manifest_empty_and_unusual_original_names_round_trip():
    with Document() as source, Document() as destination:
        assert (
            destination.import_attachments_zip(
                source.export_attachments_zip(include_manifest=True)
            )
            == 0
        )
        for name in ("", "../a", "a\\b", "bad\x00name", "bad\ud800name", "文" * 250):
            source.add_attachment(name, b"Value")
        assert (
            destination.import_attachments_zip(
                source.export_attachments_zip(include_manifest=True)
            )
            == 6
        )
        assert destination.attachments == source.attachments


def test_manifest_false_imports_json_as_payload_and_drops_metadata():
    data = _zip([("a.txt", b"A")], manifest=_manifest(_record()))
    with Document() as document:
        assert document.import_attachments_zip(data, use_manifest=False) == 2
        assert document.attachments["a.txt"] == b"A"
        assert "manifest.json" in document.attachments
        assert document.get_embedded_file("a.txt").description is None
    _assert_rejected(data, names="manifest.json", exception=KeyError)
    with Document() as document:
        assert (
            document.import_attachments_zip(
                _zip(manifest=b"arbitrary"), use_manifest=False
            )
            == 1
        )


def test_manifest_names_rename_without_sanitizing_or_losing_metadata():
    name = "folder/" + "文" * 100 + ".txt"
    data = _zip([("a.txt", b"A")], manifest=_manifest(_record(name=name)))
    with Document() as document:
        document.add_attachment(name, b"Keep")
        assert document.import_attachments_zip(data, on_conflict="rename") == 1
        spec = document.get_embedded_file(name[:-4] + "_2.txt")
        assert spec.contents == b"A" and spec.description == "Description"


def test_manifest_dates_accept_exported_fractional_seconds_before_pdf_save():
    date = datetime(2026, 10, 23, 11, 22, 33, 123456, tzinfo=UTC)
    with Document() as source, Document() as destination:
        source.add_attachment("a", b"A", creation_date=date)
        assert (
            destination.import_attachments_zip(
                source.export_attachments_zip(include_manifest=True)
            )
            == 1
        )
        assert destination.get_embedded_file("a").creation_date == date


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize("local_zip64", [False, True])
def test_data_descriptors_and_local_zip64_sizes(method, local_zip64):
    class NonSeekable(io.BytesIO):
        def seek(self, *args):
            raise io.UnsupportedOperation("not seekable")

    output = NonSeekable()
    with zipfile.ZipFile(output, "w", compression=method) as archive:
        with archive.open("stream.bin", "w", force_zip64=local_zip64) as member:
            member.write(b"Data" * 1000)
    with Document() as document:
        assert document.import_attachments_zip(output.getvalue()) == 1
        assert document.attachments == {"stream.bin": b"Data" * 1000}


def test_zip_comments_extra_fields_cp437_names_and_prepended_bytes():
    output = io.BytesIO()
    info = zipfile.ZipInfo("x.txt")
    info.extra = struct.pack("<HH4s", 12345, 4, b"Data")
    info.comment = b"Member comment"
    with zipfile.ZipFile(output, "w") as archive:
        archive.comment = b"Archive comment"
        archive.writestr(info, b"Contents")
    data = bytearray(output.getvalue())
    data[30] = 0x82
    data[data.index(b"PK\x01\x02") + 46] = 0x82
    with Document() as document:
        assert document.import_attachments_zip(b"prefix" + data) == 1
        assert document.attachments == {"é.txt": b"Contents"}


def test_selection_and_stream_failures_leave_attachments_intact():
    def selection():
        yield "new"
        raise LookupError("selection failed")

    class BrokenStream:
        def read(self, size):
            raise OSError("read failed")

    _assert_rejected(_zip([("new", b"New")]), names=selection(), exception=LookupError)
    _assert_rejected(BrokenStream(), exception=OSError)


def test_unselected_local_structural_errors_are_rejected():
    data = bytearray(_zip([("good", b"Good"), ("bad", b"Bad")]))
    central = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 4)
    local = struct.unpack_from("<L", data, central + 42)[0]
    data[local + 30] = ord("X")
    _assert_rejected(data, names="good")


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_actual_output_is_checked_even_when_declared_size_and_crc_match_prefix(method):
    data = bytearray(_zip([("new", b"A" * 100)], compression=method))
    central = data.index(b"PK\x01\x02")
    crc = zlib.crc32(b"A" * 50)
    for offset in (14, central + 16):
        struct.pack_into("<L", data, offset, crc)
    for offset in (22, central + 24):
        struct.pack_into("<L", data, offset, 50)
    _assert_rejected(data)


def test_manifest_bytes_are_included_in_exact_cumulative_and_work_limits():
    manifest = json.dumps(_manifest(_record())).encode()
    data = _zip([("a.txt", b"A")], manifest=manifest)
    total = len(manifest) + 1
    with Document(
        limits=PdfLoadLimits(
            max_total_decoded_bytes=total, max_codec_work_bytes=len(data) + total
        )
    ) as document:
        assert document.import_attachments_zip(data) == 1
    for limits in (
        PdfLoadLimits(max_total_decoded_bytes=total - 1),
        PdfLoadLimits(max_codec_work_bytes=len(data) + total - 1),
    ):
        _assert_rejected(data, limits=limits, exception=PdfResourceLimitException)


def test_manifest_collision_replacement_clears_stale_loaded_metadata():
    with Document() as source:
        source.pages.add()
        source.add_attachment(
            "original/a.txt",
            b"Old",
            mime="old/type",
            description="Old",
            relationship="Source",
        )
        saved = _saved(source)
    with Document(saved) as document:
        assert document._engine_pdf.attachment_read_meta
        assert (
            document.import_attachments_zip(
                _zip(
                    [("a.txt", b"A")],
                    manifest=_manifest(
                        _record(mime_type=None, description=None, relationship=None)
                    ),
                ),
                on_conflict="replace",
            )
            == 1
        )
        spec = document.get_embedded_file("original/a.txt")
        assert (
            spec.contents == b"A"
            and spec.description is None
            and spec.mime_type is None
        )
        assert "original/a.txt" not in document._engine_pdf.attachment_read_meta
        with Document(_saved(document)) as reopened:
            assert reopened.get_embedded_file("original/a.txt").description is None


@pytest.mark.parametrize("compress", [False, True])
def test_pdf_compression_is_independent_of_zip_compression(compress):
    with Document() as document:
        document.pages.add()
        document.import_attachments_zip(
            _zip([("a", b"A" * 1000)], compression=zipfile.ZIP_DEFLATED),
            compress=compress,
        )
        assert document._engine_pdf.attachment_meta["a"]["compress"] is compress
        with Document(_saved(document)) as reopened:
            assert reopened.attachments == {"a": b"A" * 1000}


@pytest.mark.parametrize(
    "options",
    [
        {"on_conflict": "unknown"},
        {"on_conflict": True},
        {"on_conflict": []},
        {"on_conflict": "RENAME"},
        {"use_manifest": 1},
        {"compress": "yes"},
    ],
)
def test_invalid_options_are_rejected_before_reading_source(options):
    class Source:
        def read(self, *args):
            pytest.fail("invalid options must not read the source")

    _assert_rejected(Source(), **options)


@pytest.mark.parametrize("names", [1, b"a", [1], ["a", None]])
def test_invalid_selection_is_atomic(names):
    _assert_rejected(_zip([("a", b"A")]), names=names)


def test_source_errors_and_document_lifecycle(tmp_path):
    for source, error in [
        (None, TypeError),
        (123, TypeError),
        (io.StringIO("text"), TypeError),
        (tmp_path / "absent.zip", FileNotFoundError),
    ]:
        _assert_rejected(source, exception=error)
    document = Document()
    document.dispose()
    with pytest.raises(AsposePdfException):
        document.import_attachments_zip(_zip())


@pytest.mark.parametrize(
    "data", [b"", b"not a ZIP", b"PK\x03\x04", _zip()[:-1], _zip() + b"trailing"]
)
def test_malformed_archives_are_atomic(data):
    _assert_rejected(data)


@pytest.mark.parametrize(
    "kind", ["duplicate", "symlink", "fifo", "directory_payload", "nul", "empty"]
)
def test_ambiguous_or_special_members_are_rejected(kind):
    if kind == "duplicate":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            data = _zip([("a", b"A"), ("a", b"B")])
    elif kind in {"symlink", "fifo"}:
        info = zipfile.ZipInfo("special")
        info.create_system = 3
        info.external_attr = (
            (stat.S_IFLNK if kind == "symlink" else stat.S_IFIFO) | 0o644
        ) << 16
        data = _zip([(info, b"target")])
    elif kind == "directory_payload":
        data = _zip([("folder/", b"Data")])
    else:
        data = bytearray(_zip([("abc", b"A")]))
        local = 30
        central = data.index(b"PK\x01\x02") + 46
        for offset in (local, central):
            data[offset + (1 if kind == "nul" else 0)] = 0
    _assert_rejected(data, names=[])


@pytest.mark.parametrize(
    "kind",
    [
        "encrypted",
        "unsupported",
        "multi_disk",
        "count",
        "central_length",
        "zip64",
        "local_flags",
        "local_method",
        "local_name",
        "crc",
        "truncated_deflate",
        "extra_deflate",
        "declared_small",
        "declared_large",
    ],
)
def test_corrupt_metadata_or_payload_is_atomic(kind):
    method = (
        zipfile.ZIP_DEFLATED
        if kind in {"truncated_deflate", "extra_deflate"}
        else zipfile.ZIP_STORED
    )
    data = bytearray(_zip([("new", b"A"), ("bad", b"B" * 100)], compression=method))
    central = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 4)
    local = struct.unpack_from("<L", data, central + 42)[0]
    end = data.rfind(b"PK\x05\x06")
    if kind in {"encrypted", "local_flags"}:
        struct.pack_into("<H", data, local + 6, 1)
        if kind == "encrypted":
            struct.pack_into("<H", data, central + 8, 1)
    elif kind == "unsupported":
        struct.pack_into("<H", data, central + 10, 12)
    elif kind == "multi_disk":
        struct.pack_into("<H", data, end + 4, 1)
    elif kind == "count":
        struct.pack_into("<2H", data, end + 8, 1, 1)
    elif kind == "central_length":
        struct.pack_into("<L", data, end + 12, 1)
    elif kind == "zip64":
        struct.pack_into("<2H", data, end + 8, 65535, 65535)
    elif kind == "local_method":
        struct.pack_into("<H", data, local + 8, 8)
    elif kind == "local_name":
        data[local + 30] = ord("X")
    elif kind == "crc":
        data[local + 33] ^= 1
    elif kind in {"truncated_deflate", "extra_deflate"}:
        size = struct.unpack_from("<L", data, central + 20)[0]
        struct.pack_into(
            "<L",
            data,
            central + 20,
            size - 1 if kind == "truncated_deflate" else size + 1,
        )
    else:
        size = 50 if kind == "declared_small" else 101
        struct.pack_into("<L", data, central + 24, size)
        if kind == "declared_small":
            struct.pack_into("<L", data, central + 16, zlib.crc32(b"B" * 50))
    _assert_rejected(data)


def test_only_selected_non_skipped_payloads_are_decoded():
    data = bytearray(_zip([("good", b"Good"), ("bad", b"Bad")]))
    central = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 4)
    struct.pack_into("<L", data, central + 16, 0)
    with Document() as document:
        assert document.import_attachments_zip(data, names="good") == 1
        document.add_attachment("bad", b"Keep")
        assert document.import_attachments_zip(data, on_conflict="skip") == 0
        assert document.attachments["bad"] == b"Keep"


@pytest.mark.parametrize(
    "payload",
    [
        b"not JSON",
        b"\xff",
        b"[]",
        b'{"schema_version":1,"schema_version":1,"attachments":[]}',
        b'{"schema_version":NaN,"attachments":[]}',
        {"schema_version": True, "attachments": []},
        {"schema_version": 2, "attachments": []},
        {"schema_version": 1, "attachments": {}},
        {},
        _manifest(),
        _manifest(_record(size=True)),
        _manifest(_record(size=2)),
        _manifest(_record(archive_name="missing")),
        _manifest(_record(archive_name="manifest.json")),
        _manifest(_record(), _record()),
        _manifest(_record(name=None)),
        _manifest(_record(mime_type=1)),
        _manifest(_record(description=[])),
        _manifest(_record(relationship="Invalid")),
        _manifest(_record(creation_date="2026-15-01")),
        _manifest(_record(mod_date=123)),
        _manifest({"name": "missing fields"}),
        _manifest({**_record(), "extra": "unsupported"}),
    ],
)
def test_invalid_manifests_are_rejected_before_any_import(payload):
    _assert_rejected(_zip([("a.txt", b"A")], manifest=payload), names=[])


def test_manifest_missing_and_duplicate_original_names_are_rejected():
    _assert_rejected(
        _zip([("a.txt", b"A"), ("b.txt", b"B")], manifest=_manifest(_record()))
    )
    _assert_rejected(
        _zip(
            [("a.txt", b"A"), ("b.txt", b"B")],
            manifest=_manifest(_record(), _record(member="b.txt")),
        )
    )


@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_input_bytes", 50),
        ("max_codec_work_bytes", 50),
        ("max_container_items", 1),
        ("max_object_bytes", 1),
        ("max_decoded_stream_bytes", 9),
        ("max_total_decoded_bytes", 19),
        ("max_compression_ratio", 1),
    ],
)
def test_limits_are_enforced_atomically(limit, value):
    data = _zip(
        [("new", b"N" * 10), ("other", b"O" * 10)], compression=zipfile.ZIP_DEFLATED
    )
    _assert_rejected(
        data,
        limits=PdfLoadLimits(**{limit: value}),
        exception=PdfResourceLimitException,
    )


def test_input_and_central_count_limits_precede_zipfile_allocation(monkeypatch):
    data = _zip([("a", b"A"), ("b", b"B")])
    monkeypatch.setattr(
        attachment_zip_import.zipfile,
        "ZipFile",
        lambda *a, **k: pytest.fail("must reject before ZipFile allocation"),
    )
    for limits in (
        PdfLoadLimits(max_input_bytes=1),
        PdfLoadLimits(max_container_items=1),
    ):
        _assert_rejected(data, limits=limits, exception=PdfResourceLimitException)


def test_exact_limits_and_unlimited_configuration():
    data = _zip([("a", b"A" * 10), ("b", b"B" * 10)])
    for limits in (
        PdfLoadLimits(
            max_input_bytes=len(data),
            max_codec_work_bytes=len(data) + 20,
            max_container_items=2,
            max_decoded_stream_bytes=10,
            max_total_decoded_bytes=20,
        ),
        PdfLoadLimits.unlimited(),
    ):
        with Document(limits=limits) as document:
            assert document.import_attachments_zip(data) == 2
    _assert_rejected(
        data,
        limits=PdfLoadLimits(max_codec_work_bytes=len(data) + 19),
        exception=PdfResourceLimitException,
    )


def test_selection_and_result_counts_are_bounded():
    data = _zip([("a", b"A")])
    _assert_rejected(
        data,
        limits=PdfLoadLimits(max_container_items=1),
        exception=PdfResourceLimitException,
    )
    with Document(limits=PdfLoadLimits(max_container_items=1)) as document:
        with pytest.raises(PdfResourceLimitException):
            document.import_attachments_zip(data, names=["a", "a"])
        assert document.attachments == {}


def test_sanitized_attachment_names_also_obey_text_limit():
    _assert_rejected(
        _zip([(" ", b"A")]),
        limits=PdfLoadLimits(max_object_bytes=1),
        exception=PdfResourceLimitException,
    )


@pytest.mark.parametrize("source_kind", ["bytes", "stream"])
def test_archive_buffer_limit_reports_the_configured_policy(source_kind):
    data = _zip([("a", b"A")])
    source = data if source_kind == "bytes" else io.BytesIO(data)
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=10)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes=10"):
            document.import_attachments_zip(source)


@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_object_bytes", 100),
        ("max_decoded_stream_bytes", 100),
        ("max_total_decoded_bytes", 100),
        ("max_nesting_depth", 2),
    ],
)
def test_manifest_limits(limit, value):
    data = _zip([("a.txt", b"A")], manifest=_manifest(_record()))
    _assert_rejected(
        data,
        limits=PdfLoadLimits(**{limit: value}),
        exception=PdfResourceLimitException,
    )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_loaded_documents_keep_existing_content_metadata_and_lazy_pages(
    tmp_path, monkeypatch, streaming, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        source.pages.add().add_text("Keep page text", 10, 10)
        source.add_attachment("old", b"Old", description="Keep")
        source.info["Title"] = "Keep title"
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as document:
        protected = document.is_encrypted
        with monkeypatch.context() as patch:
            patch.setattr(
                document._engine_pdf,
                "get_page_content",
                lambda *a, **k: pytest.fail("page content must stay lazy"),
            )
            assert document.import_attachments_zip(_zip([("new", b"New")])) == 1
            assert document.info["Title"] == "Keep title"
            assert document.get_embedded_file("old").description == "Keep"
            assert document.is_encrypted is protected
            if streaming:
                assert document._engine_pdf.page_contents == []
                assert document._engine_pdf._extracted_text is None
        document.save(tmp_path / "output.pdf")
    with Document(
        tmp_path / "output.pdf", password="secret" if encrypted else None
    ) as reopened:
        assert reopened.attachments == {"old": b"Old", "new": b"New"}
        assert "Keep page text" in reopened.extract_text()
    assert path.read_bytes() == before
