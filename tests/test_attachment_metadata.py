"""Tests for attachment metadata (MIME / description / dates) and compression."""

from __future__ import annotations

import datetime
import io

import pytest

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


def test_encode_mime_name_keeps_the_media_type_it_was_given():
    """The escaping is the writer's -- a name escapes whatever it holds -- so
    what this has to get right is the type, not its spelling in the file.
    ``test_mime_subtype_written_and_content_roundtrips`` checks the bytes."""
    assert _encode_mime_name("text/plain").name == "/text/plain"
    assert _encode_mime_name("application/pdf").name == "/application/pdf"
    assert _encode_mime_name("image/svg+xml").name == "/image/svg+xml"
    # A media type is a registry token, so anything outside ASCII is refused
    # rather than carried.
    assert _encode_mime_name("  text/plain  ").name == "/text/plain"


def test_format_pdf_date_variants():
    assert _format_pdf_date(None) is None
    # Naive datetime -> no zone suffix.
    assert _format_pdf_date(datetime.datetime(2026, 6, 8, 9, 5, 7)) == "D:20260608090507"
    # UTC -> 'Z'.
    utc = datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=datetime.UTC)
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
        creation_date=datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.UTC),
        mod_date=datetime.datetime(2026, 6, 9, 13, 30, 0, tzinfo=datetime.UTC),
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
    utc = datetime.datetime(2026, 6, 8, 9, 5, 7, tzinfo=datetime.UTC)
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
    created = datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.UTC)
    modified = datetime.datetime(2026, 6, 9, 13, 30, 0, tzinfo=datetime.UTC)
    doc = Document()
    doc.add_attachment("log.txt", b"y" * 100, creation_date=created, mod_date=modified)
    spec = _reload(doc).get_embedded_file("log.txt")
    assert spec is not None
    assert spec.creation_date == created
    assert spec.mod_date == modified


def test_embedded_files_full_metadata_roundtrip_and_ordering():
    created = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
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


# ---------------------------------------------------------------------------
# Associated-file relationship (/AFRelationship) and typed removal
# ---------------------------------------------------------------------------


def test_add_attachment_relationship_round_trips():
    doc = Document()
    doc.add_attachment("data.csv", b"a,b\n1,2\n", mime="text/csv", relationship="Data")
    doc.add_attachment("origin.txt", b"src", relationship="Source")

    reopened = _reload(doc)
    by_name = {spec.name: spec for spec in reopened.embedded_files}
    assert by_name["data.csv"].relationship == "Data"
    assert by_name["origin.txt"].relationship == "Source"


def test_default_relationship_reads_back_as_none():
    # The writer stamps the default "Unspecified"; the typed read model surfaces
    # that as None so only meaningful relationships appear.
    doc = Document()
    doc.add_attachment("plain.txt", b"hi")
    assert _reload(doc).embedded_files[0].relationship is None


def test_add_attachment_rejects_invalid_relationship():
    doc = Document()
    with __import__("pytest").raises(ValueError):
        doc.add_attachment("x.txt", b"y", relationship="NotAReal")


def test_file_specification_exposes_relationship_field():
    doc = Document()
    doc.add_attachment("d.bin", b"\x00\x01", relationship="Supplement")
    spec = doc.embedded_files[0]
    assert isinstance(spec, FileSpecification)
    assert spec.relationship == "Supplement"


def test_remove_attachment_removes_one_and_keeps_others():
    doc = Document()
    doc.add_attachment("a.txt", b"a")
    doc.add_attachment("b.txt", b"b")

    assert doc.remove_attachment("a.txt") is True
    reopened = _reload(doc)
    assert [spec.name for spec in reopened.embedded_files] == ["b.txt"]


def test_remove_attachment_returns_false_when_absent():
    doc = Document()
    doc.add_attachment("a.txt", b"a")
    assert doc.remove_attachment("missing.txt") is False
    assert [spec.name for spec in doc.embedded_files] == ["a.txt"]


def test_remove_last_attachment_clears_embedded_files_tree():
    doc = Document()
    doc.add_attachment("only.txt", b"x")
    reopened = _reload(doc)  # now the tree exists in the loaded COS
    assert reopened.remove_attachment("only.txt") is True

    final_bytes = _save(reopened)
    assert b"/EmbeddedFiles" not in final_bytes
    assert Document().load_from(final_bytes).embedded_files == []


# ---------------------------------------------------------------------------
# Editing one field of an existing attachment
# ---------------------------------------------------------------------------


def _with_full_metadata() -> Document:
    doc = Document()
    doc.pages.add()
    doc.add_attachment(
        "notes.txt",
        b"hello",
        mime="text/plain",
        description="Original",
        relationship="Source",
        creation_date=datetime.datetime(2020, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
    )
    return _reload(doc)


def test_updating_one_field_keeps_the_rest():
    """``add_attachment`` replaces; this is how a single field is changed.

    Re-adding a name to change its description dropped the MIME type, the
    dates and the relationship the file already carried, because a fresh add
    supersedes what was read back — leaving no way to edit one field.
    """
    doc = _with_full_metadata()

    spec = doc.update_attachment("notes.txt", description="Reviewed")

    assert spec.description == "Reviewed"
    assert spec.mime_type == "text/plain"
    assert spec.relationship == "Source"
    assert spec.creation_date == datetime.datetime(
        2020, 1, 2, 3, 4, 5, tzinfo=datetime.UTC
    )
    assert spec.contents == b"hello"


def test_an_update_survives_a_save():
    doc = _with_full_metadata()
    doc.update_attachment("notes.txt", description="Reviewed", mime="text/markdown")

    spec = _reload(doc).get_embedded_file("notes.txt")

    assert (spec.description, spec.mime_type) == ("Reviewed", "text/markdown")
    assert spec.relationship == "Source"


def test_renaming_carries_the_metadata_across():
    doc = _with_full_metadata()

    spec = doc.update_attachment("notes.txt", new_name="notes-v2.txt")

    assert spec.name == "notes-v2.txt"
    assert spec.mime_type == "text/plain"
    assert spec.description == "Original"
    assert sorted(doc.attachments) == ["notes-v2.txt"]
    assert _reload(doc).get_embedded_file("notes.txt") is None


def test_the_payload_can_be_replaced_on_its_own():
    doc = _with_full_metadata()

    spec = doc.update_attachment("notes.txt", content=b"goodbye")

    assert spec.contents == b"goodbye"
    assert spec.mime_type == "text/plain"


def test_renaming_onto_another_attachment_is_refused():
    """Quietly replacing a different file is worse than an error."""
    doc = _with_full_metadata()
    doc.add_attachment("other.txt", b"x")

    with pytest.raises(ValueError, match="already"):
        doc.update_attachment("other.txt", new_name="notes.txt")

    assert sorted(doc.attachments) == ["notes.txt", "other.txt"]


def test_updating_an_attachment_that_is_not_there_raises():
    """A name that does not match is a typo, not a no-op."""
    doc = _with_full_metadata()

    with pytest.raises(KeyError, match="No attachment named"):
        doc.update_attachment("nope", description="x")

    # ...and it says so before touching anything.
    assert sorted(doc.attachments) == ["notes.txt"]


def test_updating_before_the_first_save_keeps_what_was_just_added():
    """Metadata a caller set is carried forward too, not only what was read.

    Before a save there is nothing to read back: the attachment's MIME type and
    dates exist only as what ``add_attachment`` was given, and an update has to
    preserve those the same way.
    """
    doc = Document()
    doc.pages.add()
    doc.add_attachment(
        "notes.txt", b"hello", mime="text/plain", relationship="Source"
    )

    spec = doc.update_attachment("notes.txt", description="Reviewed")

    assert spec.mime_type == "text/plain"
    assert spec.relationship == "Source"
    assert spec.description == "Reviewed"


def test_a_renamed_attachment_leaves_no_metadata_behind_for_its_old_name():
    """``attachments`` is a writable mapping, so the old name can come back.

    A stale read-back entry would then attach the previous file's MIME type and
    description to whatever bytes were just put under that name.
    """
    doc = _with_full_metadata()
    doc.update_attachment("notes.txt", new_name="notes-v2.txt")

    doc.attachments["notes.txt"] = b"a different file"
    spec = _reload(doc).get_embedded_file("notes.txt")

    assert spec.contents == b"a different file"
    assert spec.mime_type is None
    assert spec.description is None


def test_an_unknown_relationship_is_refused():
    doc = _with_full_metadata()

    with pytest.raises(ValueError, match="relationship"):
        doc.update_attachment("notes.txt", relationship="Nonesuch")
