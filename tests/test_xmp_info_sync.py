"""Tests for /Info <-> XMP metadata synchronisation and PDF/ISO-8601 dates."""

from __future__ import annotations

import io

import pytest

from aspose_pdf.document import Document
from aspose_pdf.xmp import (
    XmpArray,
    XmpField,
    XmpPacket,
    info_to_xmp,
    iso8601_to_pdf_date,
    parse,
    pdf_date_to_iso8601,
    serialize,
    xmp_to_info,
)

DC = "http://purl.org/dc/elements/1.1/"
PDF_NS = "http://ns.adobe.com/pdf/1.3/"
XMP_NS = "http://ns.adobe.com/xap/1.0/"


def _minimal_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n"
        b"210\n"
        b"%%EOF"
    )


# ---------------------------------------------------------------------------
# Date conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pdf_date, iso",
    [
        ("D:20240101120000Z", "2024-01-01T12:00:00Z"),
        ("D:20240101120000+05'00'", "2024-01-01T12:00:00+05:00"),
        ("D:20231231235959-08'00'", "2023-12-31T23:59:59-08:00"),
        ("D:20240101", "2024-01-01"),
    ],
)
def test_pdf_date_iso_round_trip(pdf_date, iso):
    assert pdf_date_to_iso8601(pdf_date) == iso
    assert iso8601_to_pdf_date(iso) == pdf_date


def test_pdf_date_without_d_prefix():
    assert pdf_date_to_iso8601("20240101120000Z") == "2024-01-01T12:00:00Z"


def test_date_converters_reject_garbage():
    assert pdf_date_to_iso8601("") == ""
    assert pdf_date_to_iso8601("not-a-date") == ""
    assert iso8601_to_pdf_date("") == ""
    assert iso8601_to_pdf_date("nonsense") == ""


# ---------------------------------------------------------------------------
# info_to_xmp
# ---------------------------------------------------------------------------


def _full_info() -> dict[str, str]:
    return {
        "Title": "My Doc",
        "Author": "Ada Lovelace",
        "Subject": "Notes",
        "Keywords": "alpha, beta",
        "Creator": "MyApp 1.0",
        "Producer": "foss",
        "CreationDate": "D:20240101120000Z",
        "ModDate": "D:20240102000000Z",
    }


def test_info_to_xmp_maps_all_standard_keys():
    packet = info_to_xmp(_full_info())

    title = packet.get("dc", "title")
    assert isinstance(title.value, XmpArray)
    assert title.value.kind == "Alt"
    assert title.value.items[0].value == "My Doc"
    assert title.value.items[0].language == "x-default"

    creator = packet.get("dc", "creator")
    assert creator.value.kind == "Seq"
    assert creator.value.items[0].value == "Ada Lovelace"

    assert packet.get("dc", "description").value.items[0].value == "Notes"
    assert packet.get("pdf", "Keywords").value == "alpha, beta"
    assert packet.get("xmp", "CreatorTool").value == "MyApp 1.0"
    assert packet.get("pdf", "Producer").value == "foss"
    assert packet.get("xmp", "CreateDate").value == "2024-01-01T12:00:00Z"
    assert packet.get("xmp", "ModifyDate").value == "2024-01-02T00:00:00Z"


def test_info_to_xmp_skips_empty_and_missing():
    packet = info_to_xmp({"Title": "Only", "Author": "", "Producer": None})  # type: ignore[dict-item]
    assert packet.get("dc", "title") is not None
    assert packet.get("dc", "creator") is None
    assert packet.get("pdf", "Producer") is None


def test_info_to_xmp_overwrites_existing_property():
    packet = XmpPacket()
    packet.set_value("pdf", "Producer", "old", uri=PDF_NS)
    info_to_xmp({"Producer": "new"}, packet)
    producers = [f for f in packet.fields if getattr(f, "name", "") == "Producer"]
    assert len(producers) == 1
    assert producers[0].value == "new"


def test_info_to_xmp_result_is_serialize_stable():
    packet = info_to_xmp(_full_info())
    assert serialize(parse(serialize(packet))) == serialize(packet)


# ---------------------------------------------------------------------------
# xmp_to_info
# ---------------------------------------------------------------------------


def test_xmp_to_info_is_inverse_of_info_to_xmp():
    info = _full_info()
    assert xmp_to_info(info_to_xmp(info)) == info


def test_xmp_to_info_alt_prefers_x_default():
    packet = XmpPacket()
    packet.add(
        XmpField(
            prefix="dc",
            name="title",
            namespace_uri=DC,
            value=XmpArray(
                kind="Alt",
                items=[
                    XmpField(value="Bonjour", language="fr"),
                    XmpField(value="Hello", language="x-default"),
                ],
            ),
        )
    )
    assert xmp_to_info(packet)["Title"] == "Hello"


def test_xmp_to_info_joins_creator_sequence():
    packet = XmpPacket()
    packet.add(
        XmpField(
            prefix="dc",
            name="creator",
            namespace_uri=DC,
            value=XmpArray(
                kind="Seq",
                items=[XmpField(value="Ada"), XmpField(value="Grace")],
            ),
        )
    )
    assert xmp_to_info(packet)["Author"] == "Ada, Grace"


def test_xmp_to_info_converts_date_back_to_pdf():
    packet = XmpPacket()
    packet.set_value("xmp", "CreateDate", "2024-01-01T12:00:00Z", uri=XMP_NS)
    assert xmp_to_info(packet)["CreationDate"] == "D:20240101120000Z"


# ---------------------------------------------------------------------------
# Document.sync_metadata integration
# ---------------------------------------------------------------------------


def _loaded_doc() -> Document:
    doc = Document()
    doc.load_from(io.BytesIO(_minimal_pdf_bytes()))
    return doc


def test_sync_info_to_xmp_persists_through_save():
    doc = _loaded_doc()
    doc.info = {"Title": "Hello", "Author": "Ada", "Producer": "foss",
                "CreationDate": "D:20240101120000Z"}
    assert doc.sync_metadata(direction="info_to_xmp") is doc

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    xmp = reloaded.xmp_metadata
    assert xmp.get("dc", "title").value.items[0].value == "Hello"
    assert xmp.get("dc", "creator").value.items[0].value == "Ada"
    assert xmp.get("pdf", "Producer").value == "foss"
    assert xmp.get("xmp", "CreateDate").value == "2024-01-01T12:00:00Z"


def test_sync_xmp_to_info_persists_through_save():
    doc = _loaded_doc()
    packet = doc.xmp_metadata
    packet.add(
        XmpField(
            prefix="dc",
            name="title",
            namespace_uri=DC,
            value=XmpArray(kind="Alt", items=[XmpField(value="FromXMP", language="x-default")]),
        )
    )
    packet.set_value("xmp", "CreateDate", "2022-06-07T08:09:10Z", uri=XMP_NS)
    doc.xmp_metadata = packet
    doc.sync_metadata(direction="xmp_to_info")

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    assert reloaded.info.get("Title") == "FromXMP"
    assert reloaded.info.get("CreationDate") == "D:20220607080910Z"


def test_sync_metadata_rejects_unknown_direction():
    doc = _loaded_doc()
    with pytest.raises(ValueError):
        doc.sync_metadata(direction="sideways")


# ---------------------------------------------------------------------------
# Date precision: neither format may gain components it was never given
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pdf_date", "iso"),
    [
        ("D:2026", "2026"),
        ("D:202609", "2026-09"),
        ("D:20260910", "2026-09-10"),
        ("D:202609100830", "2026-09-10T08:30"),
        ("D:20260910083015", "2026-09-10T08:30:15"),
        ("D:20260910083015Z", "2026-09-10T08:30:15Z"),
        ("D:20260910083015+02'00'", "2026-09-10T08:30:15+02:00"),
    ],
)
def test_a_truncated_date_keeps_its_precision_both_ways(pdf_date, iso):
    assert pdf_date_to_iso8601(pdf_date) == iso
    assert iso8601_to_pdf_date(iso) == pdf_date


def test_a_year_only_pdf_date_does_not_gain_a_month_and_a_day():
    """``D:2026`` says 2026, not the first of January."""
    assert pdf_date_to_iso8601("D:2026") == "2026"
    assert iso8601_to_pdf_date("2026") == "D:2026"


def test_an_hour_without_minutes_gains_them_because_xmp_has_no_shorter_time():
    """The only precision XMP cannot express, so the padding is the format's."""
    assert pdf_date_to_iso8601("D:2026091008") == "2026-09-10T08:00"


# ---------------------------------------------------------------------------
# What is not a date is refused, not read as far as it parses
# ---------------------------------------------------------------------------


def test_an_iso_date_in_info_is_refused_not_read_as_its_year():
    """The corruption this replaces: ``2026-09-10`` used to become 2026-01-01."""
    got = pdf_date_to_iso8601("2026-09-10")
    assert got != "2026-01-01"
    assert got == ""


@pytest.mark.parametrize(
    "text",
    [
        "D:202609100",  # odd digit count: one field is half there
        "D:20260910083",
        "D:20260910083015+02'00' (approx)",  # trailing prose
        "10 September 2026",
        "D:",
        "D:abcd",
    ],
)
def test_a_pdf_date_that_is_not_one_is_refused(text):
    assert pdf_date_to_iso8601(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "D:202613",  # month 13
        "D:20260932",  # day 32
        "D:2026091024",  # hour 24
        "D:202609100860",  # minute 60
        "D:20260910083060",  # second 60
        "D:20260910083015+24'00'",  # offset hour 24
        "D:20260910083015+02'60'",  # offset minute 60
    ],
)
def test_one_component_one_past_its_range_is_enough_to_refuse(text):
    """Each bound tested on its own: a neighbour must not be doing the work."""
    assert pdf_date_to_iso8601(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "2026-13",  # month 13
        "2026-09-32",  # day 32
        "2026-09-10T24:00",  # hour 24
        "2026-09-10T08:60",  # minute 60
        "2026-09-10T08:30:60",  # second 60
        "2026-09-10T08:30:15+24:00",  # offset hour 24
        "2026-09-10T08:30:15+02:60",  # offset minute 60
        "2026-09-10 and later",
        "yesterday",
    ],
)
def test_an_iso_date_that_is_not_one_is_refused(text):
    assert iso8601_to_pdf_date(text) == ""


def test_an_iso_time_without_a_day_is_refused():
    """Flat optional groups would take the month's digits for the day."""
    assert iso8601_to_pdf_date("2026-10T08:30") == ""
    assert iso8601_to_pdf_date("2026T08:30") == ""


def test_an_impossible_calendar_date_is_carried_not_dropped():
    """A range check, not a calendar one: the file said the 29th, qpdf agrees."""
    assert pdf_date_to_iso8601("D:20260229") == "2026-02-29"


@pytest.mark.parametrize(
    "offset", ["Z", "z", "+02'00'", "+02'00", "+0200", "+02:00", "+02"]
)
def test_every_offset_spelling_real_writers_produce_is_read(offset):
    iso = pdf_date_to_iso8601(f"D:20260910083015{offset}")
    assert iso.startswith("2026-09-10T08:30:15")
    assert iso[19:] in ("Z", "+02:00")


# ---------------------------------------------------------------------------
# A value that is not a date is not written into a property that holds one
# ---------------------------------------------------------------------------


def test_info_to_xmp_skips_a_non_date_rather_than_writing_it_as_one(caplog):
    with caplog.at_level("WARNING"):
        packet = info_to_xmp({"Title": "kept", "CreationDate": "10 September 2026"})

    assert packet.get("xmp", "CreateDate") is None
    assert packet.get("dc", "title") is not None  # the rest of the sync ran
    assert "CreationDate" in caplog.text
    assert "10 September 2026" in caplog.text


def test_info_to_xmp_leaves_an_existing_xmp_date_alone_when_info_has_junk():
    packet = info_to_xmp({"CreationDate": "D:20240101120000Z"})
    info_to_xmp({"CreationDate": "not a date"}, packet)
    assert packet.get("xmp", "CreateDate").value == "2024-01-01T12:00:00Z"


def test_xmp_to_info_skips_a_non_date_rather_than_writing_it_as_one(caplog):
    packet = XmpPacket()
    packet.set_value("xmp", "CreateDate", "sometime in 2026", uri=XMP_NS)
    packet.set_value("pdf", "Producer", "kept", uri=PDF_NS)

    with caplog.at_level("WARNING"):
        info = xmp_to_info(packet)

    assert "CreationDate" not in info
    assert info["Producer"] == "kept"
    assert "sometime in 2026" in caplog.text


def test_a_junk_info_date_does_not_reach_the_saved_xmp_packet():
    doc = _loaded_doc()
    doc.info = {"Title": "Hello", "CreationDate": "sometime last year"}
    doc.sync_metadata(direction="info_to_xmp")

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    xmp = reloaded.xmp_metadata
    assert xmp.get("xmp", "CreateDate") is None
    assert xmp.get("dc", "title").value.items[0].value == "Hello"
    assert reloaded.info["CreationDate"] == "sometime last year"  # /Info untouched


def test_an_offset_without_a_time_is_dropped_not_kept():
    """An offset qualifies a time of day; with no time it says nothing."""
    assert pdf_date_to_iso8601("D:2026Z") == "2026"
    assert pdf_date_to_iso8601("D:202609+02'00'") == "2026-09"
    assert iso8601_to_pdf_date("2026Z") == "D:2026"
