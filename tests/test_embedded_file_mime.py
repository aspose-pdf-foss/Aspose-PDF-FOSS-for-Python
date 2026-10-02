"""Every embedded file declares a media type, defaulting to octet-stream.

ISO 19005-3 clause 6.8 and ISO 19005-4 clause 6.9: "The embedded file stream
dictionary shall include a valid MIME type value for the Subtype key. If the
MIME type is not known, the value ``application/octet-stream`` shall be used."
An attachment added without ``mime=`` carried no ``/Subtype`` at all, so veraPDF
failed every PDF/A-4f file that had one -- and 4f is the level that exists to
carry attachments. The same document with an explicit ``mime="text/plain"``
passed, so only the default was missing.

``/Subtype`` is written for every attachment now, PDF/A or not: it is a valid
optional key (ISO 32000-1 table 45) and harmless elsewhere, which is the reason
``/AFRelationship`` has always been written unconditionally beside it.

The read model mirrors what it already does for ``/AFRelationship``:
``Unspecified`` there, and ``application/octet-stream`` here, say nothing the
caller did not already know, so both come back as ``None`` rather than as a
value the producer never chose.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document


def _with_attachment(mime: str | None = None, level: str | None = None) -> bytes:
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    document.add_attachment(
        "data.bin", b"payload", **({"mime": mime} if mime else {})
    )
    document.info["Title"] = "A Title"
    if level:
        document.convert_to_pdfa(level)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_an_attachment_without_a_mime_type_still_declares_one():
    data = _with_attachment()
    assert b"/application#2Foctet-stream" in data


def test_an_explicit_mime_type_wins():
    data = _with_attachment(mime="text/plain")
    assert b"/text#2Fplain" in data
    assert b"octet-stream" not in data


@pytest.mark.parametrize("level", ["PDF/A-3B", "PDF/A-4F", "PDF/A-4"])
def test_the_default_survives_a_pdfa_conversion(level):
    data = _with_attachment(level=level)
    assert b"/application#2Foctet-stream" in data


def test_the_default_is_reported_as_no_declared_type():
    # It says only "unknown", which is what an absent /Subtype said, so the read
    # model does not invent a type the producer never chose -- the same rule it
    # already applies to /AFRelationship's "Unspecified".
    reopened = Document(io.BytesIO(_with_attachment()))
    spec = reopened.get_embedded_file("data.bin")
    assert spec is not None
    assert spec.mime_type is None
    assert spec.relationship is None


def test_a_declared_type_is_reported():
    reopened = Document(io.BytesIO(_with_attachment(mime="text/plain")))
    assert reopened.get_embedded_file("data.bin").mime_type == "text/plain"


def test_the_payload_is_unaffected():
    reopened = Document(io.BytesIO(_with_attachment()))
    assert reopened.get_embedded_file("data.bin").contents == b"payload"
    assert list(reopened.attachments) == ["data.bin"]


def test_a_declared_octet_stream_round_trips_as_undeclared():
    # A caller who spells out the default gets the default, and reads back the
    # same thing an undeclared attachment reads back as. Nothing is lost: the
    # two mean the same thing in PDF terms.
    data = _with_attachment(mime="application/octet-stream")
    assert b"/application#2Foctet-stream" in data
    assert Document(io.BytesIO(data)).get_embedded_file("data.bin").mime_type is None
