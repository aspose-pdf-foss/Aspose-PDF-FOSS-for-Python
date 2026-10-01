"""Converting a document twice replaces its apparatus instead of appending one.

``convert_to_pdfa`` registered a *new* XMP stream, a new ICC profile, a new
``/OutputIntent`` and a new ``/OutputIntents`` array every time it ran, then
pointed the catalog at them -- so the previous four stayed in the file with
nothing referring to them. Converting twice left 4 unreachable objects and
1055 bytes of dead streams, a third conversion 8 and 2110, without bound.
``convert_to_pdfua`` leaked its predecessor's XMP packet the same way, and so did
an ordinary edit of ``xmp_metadata``.

Nothing was *invalid* about the result -- the catalog kept exactly one
``/Metadata`` and one ``/OutputIntents`` with one entry, libqpdf's checker was
clean, and ``optimize()`` swept the dead objects up afterwards -- it was a
kilobyte of waste per conversion, in files written to be archived.

The entries the catalog owns are replaced where they stand now. The ICC profile
is not: a ``/DestOutputProfile`` stream can also be a page's ``/ICCBased``
colour space, so one already holding the right bytes is kept, and one holding
different bytes is left alone with a new stream written beside it.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

# Reached through ``startxref`` rather than the trailer, so never orphans.
_INFRASTRUCTURE = {"ObjStm", "XRef"}


def _unreachable(data: bytes) -> list[int]:
    """Object numbers no reader can arrive at by any route from the trailer."""
    pdf = SimplePdf.from_bytes(data)
    cos = pdf._cos_doc
    seen: set[int] = set()

    def walk(obj: object, depth: int = 0) -> None:
        if depth > 40:
            return
        if isinstance(obj, PdfIndirectReference):
            number = obj.object_number
            if number in seen:
                return
            seen.add(number)
            walk(pdf._resolve(obj), depth + 1)
            return
        if isinstance(obj, (PdfStream, PdfDictionary)):
            for value in obj.mapping.values():
                walk(value, depth + 1)
        elif isinstance(obj, PdfArray):
            for value in obj.items:
                walk(value, depth + 1)

    walk(cos.trailer)

    size = cos.trailer.mapping.get(PdfName("Size"))
    highest = int(size.value) if size is not None else 0
    dead = []
    for number in range(1, highest):
        if number in seen:
            continue
        try:
            obj = cos.objects[number]
        except Exception:
            continue
        if obj is None:
            continue
        kind = None
        if isinstance(obj, (PdfStream, PdfDictionary)):
            type_name = obj.mapping.get(PdfName("Type"))
            if isinstance(type_name, PdfName):
                kind = type_name.name.lstrip("/")
        if kind in _INFRASTRUCTURE:
            continue
        dead.append(number)
    return dead


def _two_pages() -> Document:
    document = Document()
    for index in range(2):
        document.pages.add()
        document.pages[index].add_text(f"Page {index}", 50, 700)
    return document


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _catalog(data: bytes):
    pdf = SimplePdf.from_bytes(data)
    return pdf, pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))


@pytest.mark.parametrize(
    ("label", "convert"),
    [
        ("pdfa", lambda doc: doc.convert_to_pdfa("PDF/A-2B")),
        ("pdfua", lambda doc: doc.convert_to_pdfua()),
    ],
)
def test_converting_three_times_costs_nothing(label, convert):
    document = _two_pages()
    sizes = []
    for _ in range(3):
        convert(document)
        data = _saved(document)
        sizes.append(len(data))
        assert _unreachable(data) == []
    assert sizes[0] == sizes[1] == sizes[2]


def test_the_apparatus_keeps_its_object_numbers():
    # Replaced where it stands, which is what leaves nothing behind.
    document = _two_pages()
    document.convert_to_pdfa("PDF/A-2B")
    first = _saved(document)
    document.convert_to_pdfa("PDF/A-2B")
    second = _saved(document)

    def numbers(data: bytes):
        pdf, root = _catalog(data)
        intents = pdf._resolve(root.mapping.get(PdfName("OutputIntents")))
        intent = pdf._resolve(intents.items[0])
        return (
            root.mapping.get(PdfName("Metadata")).object_number,
            intents.items[0].object_number,
            intent.mapping.get(PdfName("DestOutputProfile")).object_number,
        )

    assert numbers(first) == numbers(second)


def test_a_level_change_still_rewrites_the_packet():
    # Replacing in place must not mean leaving stale content behind.
    document = _two_pages()
    document.convert_to_pdfa("PDF/A-1B")
    one = _saved(document)
    document.convert_to_pdfa("PDF/A-2B")
    two = _saved(document)

    assert re.findall(rb"pdfaid:part>(\d)", one) == [b"1"]
    assert re.findall(rb"pdfaid:part>(\d)", two) == [b"2"]
    assert len(one) == len(two)
    assert _unreachable(two) == []


def test_editing_xmp_repeatedly_does_not_pile_up_packets():
    document = _two_pages()
    document.xmp_metadata.set_value("dc", "title", "first")
    data = _saved(document)

    for value in ("second", "third", "fourth"):
        reopened = Document(io.BytesIO(data))
        reopened.xmp_metadata.set_value("dc", "title", value)
        data = _saved(reopened)
        assert _unreachable(data) == []

    final = Document(io.BytesIO(data)).xmp_metadata.get("dc", "title")
    assert final is not None and final.value == "fourth"


def test_an_output_intent_holding_another_profile_is_left_alone():
    # A /DestOutputProfile may be shared with a page's /ICCBased colour space, so
    # its object is never overwritten with different bytes.
    from aspose_pdf.engine.cos import PdfNumber

    document = _two_pages()
    engine = document._engine_pdf
    engine._ensure_cos()
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    foreign = PdfStream(
        content=b"NOT-AN-SRGB-PROFILE", mapping={PdfName("N"): PdfNumber(3)}
    )
    intent = PdfDictionary(
        {
            PdfName("Type"): PdfName("OutputIntent"),
            PdfName("S"): PdfName("GTS_PDFA1"),
            PdfName("DestOutputProfile"): engine._cos_doc.register_object(foreign),
        }
    )
    array = PdfArray([engine._cos_doc.register_object(intent)])
    root.mapping[PdfName("OutputIntents")] = engine._cos_doc.register_object(array)

    document.convert_to_pdfa("PDF/A-2B")
    data = _saved(document)

    assert b"NOT-AN-SRGB-PROFILE" in data               # not overwritten
    assert data.count(b"/Alternate /DeviceRGB") == 1    # a real profile beside it


def test_the_converted_file_is_still_what_pdfa_needs():
    document = _two_pages()
    document.convert_to_pdfa("PDF/A-2B")
    document.convert_to_pdfa("PDF/A-2B")
    data = _saved(document)

    pdf, root = _catalog(data)
    assert isinstance(pdf._resolve(root.mapping.get(PdfName("Metadata"))), PdfStream)
    intents = pdf._resolve(root.mapping.get(PdfName("OutputIntents")))
    assert isinstance(intents, PdfArray) and len(intents.items) == 1
    assert document.validate_pdfa("PDF/A-2B").is_valid
