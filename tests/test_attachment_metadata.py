"""Tests for attachment metadata (MIME / description / dates) and compression."""

from __future__ import annotations

import datetime
import io

from aspose_pdf import Document, FileSpecification
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _decode_mime_name,
    _encode_mime_name,
    _format_pdf_date,
    _parse_pdf_date,
)


def _reload(doc: Document) -> Document:
    reopened = Document()
    reopened.load_from(_save(doc))
    return reopened


def _save(doc: Document) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers (unit)
# ---------------------------------------------------------------------------


def test_encode_mime_name_escapes_slash():
    assert _encode_mime_name("text/plain").name == "/text#2Fplain"
    assert _encode_mime_name("application/pdf").name == "/application#2Fpdf"
    # '+' and '.' are regular name chars and stay literal.
    assert _encode_mime_name("image/svg+xml").name == "/image#2Fsvg+xml"


def test_format_pdf_date_variants():
    assert _format_pdf_date(None) is None
    # Naive datetime -> no zone suffix.
    assert _format_pdf_date(datetime.datetime(2026, 6, 8, 9, 5, 7)) == "D:20260608090507"
    # UTC -> 'Z'.
    utc = datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=datetime.timezone.utc)
    assert _format_pdf_date(utc) == "D:20260608090507Z"
    # Offset zone -> +HH'mm'.
    tz = datetime.timezone(datetime.timedelta(hours=2, minutes=30))
    assert _format_pdf_date(datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=tz)) == (
        "D:20260608090507+02'30'"
    )
    # Pre-formatted strings pass through untouched.
    assert _format_pdf_date("D:19990101000000Z") == "D:19990101000000Z"


# ---------------------------------------------------------------------------
# Filespec / EmbeddedFile metadata
# ---------------------------------------------------------------------------


def test_mime_subtype_written_and_content_roundtrips():
    doc = Document()
    payload = b"hello world " * 20
    doc.add_attachment("note.txt", payload, mime="text/plain")
    out = _save(doc)
    assert b"/Subtype /text#2Fplain" in out
    assert SimplePdf.from_bytes(out).attachments["note.txt"] == payload


def test_description_written_to_filespec():
    doc = Document()
    doc.add_attachment("data.bin", b"x" * 100, description="My description")
    out = _save(doc)
    assert b"/Desc (My description)" in out


def test_dates_written_to_params():
    doc = Document()
    doc.add_attachment(
        "log.txt",
        b"y" * 100,
        creation_date=datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.timezone.utc),
        mod_date=datetime.datetime(2026, 6, 9, 13, 30, 0, tzinfo=datetime.timezone.utc),
    )
    out = _save(doc)
    assert b"/CreationDate (D:20260608120000Z)" in out
    assert b"/ModDate (D:20260609133000Z)" in out


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


def test_compressible_payload_is_flate_compressed():
    doc = Document()
    payload = b"compress me " * 100  # very compressible
    doc.add_attachment("big.txt", payload)  # compress defaults True
    out = _save(doc)
    assert b"/FlateDecode" in out
    assert SimplePdf.from_bytes(out).attachments["big.txt"] == payload


def test_compress_false_stores_raw_payload():
    doc = Document()
    payload = b"keep me raw " * 100
    doc.add_attachment("raw.txt", payload, compress=False)
    out = _save(doc)
    # The verbatim payload appears in the output (not Flate-wrapped).
    assert payload in out
    assert SimplePdf.from_bytes(out).attachments["raw.txt"] == payload


def test_tiny_payload_not_inflated_by_compression():
    doc = Document()
    doc.add_attachment("tiny.bin", b"\x01\x02\x03")  # compress would enlarge it
    out = _save(doc)
    # Falls back to raw storage; still round-trips.
    assert SimplePdf.from_bytes(out).attachments["tiny.bin"] == b"\x01\x02\x03"


def test_raw_dict_assignment_still_works_and_roundtrips():
    # Backward compatibility: assigning into attachments (no metadata) is fine.
    doc = Document()
    doc.attachments["plain.dat"] = b"z" * 80
    out = _save(doc)
    assert b"/EmbeddedFile" in out
    assert SimplePdf.from_bytes(out).attachments["plain.dat"] == b"z" * 80


# ---------------------------------------------------------------------------
# Read-back helpers (unit) — inverses of the encode/format helpers
# ---------------------------------------------------------------------------


def test_decode_mime_name_reverses_encode():
    assert _decode_mime_name(_encode_mime_name("text/plain")) == "text/plain"
    assert _decode_mime_name(_encode_mime_name("application/pdf")) == "application/pdf"
    assert _decode_mime_name(_encode_mime_name("image/svg+xml")) == "image/svg+xml"
    # Missing / non-name inputs degrade to None.
    assert _decode_mime_name(None) is None


def test_parse_pdf_date_reverses_format():
    # Naive datetime (no zone) round-trips.
    naive = datetime.datetime(2026, 6, 8, 9, 5, 7)
    assert _parse_pdf_date(_format_pdf_date(naive)) == naive
    # UTC ('Z').
    utc = datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=datetime.timezone.utc)
    assert _parse_pdf_date(_format_pdf_date(utc)) == utc
    # Offset zone (+02'30').
    tz = datetime.timezone(datetime.timedelta(hours=2, minutes=30))
    aware = datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=tz)
    assert _parse_pdf_date(_format_pdf_date(aware)) == aware
    # Date-only / garbage handling.
    assert _parse_pdf_date("D:20260608") == datetime.datetime(2026, 6, 8, 0, 0, 0)
    assert _parse_pdf_date(None) is None
    assert _parse_pdf_date("not-a-date") is None


# ---------------------------------------------------------------------------
# Typed read API — Document.embedded_files / get_embedded_file
# ---------------------------------------------------------------------------


def test_embedded_files_reads_mime_after_roundtrip():
    doc = Document()
    doc.add_attachment("note.txt", b"hello world " * 20, mime="text/plain")
    spec = _reload(doc).get_embedded_file("note.txt")
    assert isinstance(spec, FileSpecification)
    assert spec.name == "note.txt"
    assert spec.mime_type == "text/plain"
    assert spec.contents == b"hello world " * 20
    assert spec.size == len(b"hello world " * 20)


def test_embedded_files_reads_description_after_roundtrip():
    doc = Document()
    doc.add_attachment("data.bin", b"x" * 100, description="My description")
    spec = _reload(doc).get_embedded_file("data.bin")
    assert spec is not None
    assert spec.description == "My description"


def test_embedded_files_reads_dates_after_roundtrip():
    created = datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
    modified = datetime.datetime(2026, 6, 9, 13, 30, 0, tzinfo=datetime.timezone.utc)
    doc = Document()
    doc.add_attachment("log.txt", b"y" * 100, creation_date=created, mod_date=modified)
    spec = _reload(doc).get_embedded_file("log.txt")
    assert spec is not None
    assert spec.creation_date == created
    assert spec.mod_date == modified


def test_embedded_files_full_metadata_roundtrip_and_ordering():
    created = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    doc = Document()
    doc.add_attachment(
        "report.pdf",
        b"%PDF-fake",
        mime="application/pdf",
        description="Quarterly report",
        creation_date=created,
    )
    doc.add_attachment("a.txt", b"alpha", mime="text/plain")
    specs = _reload(doc).embedded_files
    # Ordered by name.
    assert [s.name for s in specs] == ["a.txt", "report.pdf"]
    report = {s.name: s for s in specs}["report.pdf"]
    assert report.mime_type == "application/pdf"
    assert report.description == "Quarterly report"
    assert report.creation_date == created
    assert report.mod_date is None


def test_embedded_files_available_before_save():
    # In-memory metadata is surfaced without needing a save/reload first.
    doc = Document()
    doc.add_attachment("inmem.txt", b"data", mime="text/plain", description="d")
    spec = doc.get_embedded_file("inmem.txt")
    assert spec is not None
    assert spec.mime_type == "text/plain"
    assert spec.description == "d"
    assert spec.contents == b"data"


def test_embedded_files_without_metadata_have_none_fields():
    doc = Document()
    doc.attachments["plain.dat"] = b"z" * 80
    spec = _reload(doc).get_embedded_file("plain.dat")
    assert spec is not None
    assert spec.mime_type is None
    assert spec.description is None
    assert spec.creation_date is None
    assert spec.mod_date is None
    assert spec.contents == b"z" * 80


def test_get_embedded_file_missing_returns_none():
    doc = Document()
    doc.add_attachment("present.txt", b"x")
    assert doc.get_embedded_file("absent.txt") is None


def test_file_specification_save_writes_contents(tmp_path):
    doc = Document()
    doc.add_attachment("note.txt", b"save me", mime="text/plain")
    spec = _reload(doc).get_embedded_file("note.txt")
    assert spec is not None
    out_path = tmp_path / "extracted.txt"
    spec.save(out_path)
    assert out_path.read_bytes() == b"save me"


def test_embedded_files_is_read_only_view():
    # The typed view is a snapshot; mutating it does not change the document.
    doc = Document()
    doc.add_attachment("x.txt", b"data")
    first = doc.embedded_files
    first.clear()
    assert len(doc.embedded_files) == 1
    # FileSpecification instances are frozen.
    import dataclasses

    spec = doc.embedded_files[0]
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        spec.name = "y.txt"  # type: ignore[misc]


def test_engine_level_read_meta_populated_on_load():
    doc = Document()
    doc.add_attachment("note.txt", b"hi", mime="text/plain", description="d")
    reopened = SimplePdf.from_bytes(_save(doc))
    assert reopened.attachment_read_meta["note.txt"]["mime"] == "text/plain"
    assert reopened.attachment_read_meta["note.txt"]["description"] == "d"
