"""Copying pages into another document copies them, not what they link to.

A link annotation names its page by reference. Importing a page whose link
pointed at a page that was *not* being copied followed that reference and copied
the target page too -- content, resources and all -- as an orphan no page tree
lists. Extracting a table-of-contents page that links every page of a document
wrote a file larger than the whole document. Such a reference now becomes
``null``, which is how qpdf writes it; pikepdf and PyMuPDF put no orphan page in
the file either.

A link naming its destination was copied as the name, but names live in the
source catalog, which is not copied: the link went nowhere in an extract, and in
a merge it resolved against the *other* document's names -- a second copy's
links jumped into the first copy. It is now carried as the destination it names,
as bookmarks are.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfNull
from aspose_pdf.interactive import FitDestination

_LINKS = [
    b"/Dest [12 0 R /Fit]",
    b"/Dest (sec.two)",
    b"/Dest /old.four",
    b"/A << /S /GoTo /D (sec.two) >>",
    b"/A << /S /GoTo /D /old.four >>",
    b"/Dest (nowhere)",
    b"/A << /S /GoToR /F (other.pdf) /D (sec.two) >>",
]


def _file(objects: dict[int, bytes]) -> bytes:
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    size = max(objects) + 1
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return bytes(raw)


def _stream(body: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"


def _linked() -> Document:
    """Four pages; the first carries a link of each kind in :data:`_LINKS`."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /Names << /Dests 4 0 R >> /Dests 5 0 R >>",
        2: b"<< /Type /Pages /Count 4 /Kids [10 0 R 11 0 R 12 0 R 13 0 R] >>",
        4: b"<< /Names [(sec.two) [11 0 R /Fit]] >>",
        5: b"<< /old.four [13 0 R /Fit] >>",
    }
    annots = b" ".join(b"%d 0 R" % (40 + i) for i in range(len(_LINKS)))
    for index in range(4):
        extra = b" /Annots [" + annots + b"]" if index == 0 else b""
        objects[10 + index] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents %d 0 R%s >>" % (20 + index, extra)
        )
        objects[20 + index] = _stream(b"0 0 1 rg %d 0 20 20 re f" % (index * 30))
    for index, target in enumerate(_LINKS):
        objects[40 + index] = b"<< /Type /Annot /Subtype /Link /Rect [0 %d 100 %d] %s >>" % (
            index * 30,
            index * 30 + 20,
            target,
        )
    return Document(io.BytesIO(_file(objects)))


def _saved(document) -> Document:
    if isinstance(document, Document):
        buffer = io.BytesIO()
        document.save(buffer)
        return Document(io.BytesIO(buffer.getvalue()))
    return Document(io.BytesIO(document.to_bytes()))


def _targets(document: Document, page: int) -> list:
    out = []
    for annotation in document.pages[page].annotations:
        properties = annotation.properties
        out.append(properties.get("Dest", properties.get("A")))
    return out


def _page_objects(document: Document) -> int:
    engine = document._engine_pdf
    return sum(
        1
        for obj in engine._cos_doc.objects.values()
        if isinstance(obj, PdfDictionary) and engine._get_name(obj.mapping.get(PdfName("Type"))) == "Page"
    )


def test_extracting_a_contents_page_does_not_copy_the_pages_it_links_to():
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    count = 12
    objects[2] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        count,
        b" ".join(b"%d 0 R" % (100 + i) for i in range(count)),
    )
    links = b" ".join(b"%d 0 R" % (300 + i) for i in range(1, count))
    for index in range(count):
        extra = b" /Annots [" + links + b"]" if index == 0 else b""
        objects[100 + index] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents %d 0 R%s >>" % (200 + index, extra)
        )
        objects[200 + index] = _stream(b"%% page %d " % index + b"0 0 m 300 300 l S " * 200)
    for index in range(1, count):
        objects[300 + index] = b"<< /Type /Annot /Subtype /Link /Rect [0 %d 50 %d] /Dest [%d 0 R /XYZ 0 300 0] >>" % (
            index * 20,
            index * 20 + 10,
            100 + index,
        )
    source = Document(io.BytesIO(_file(objects)))

    extract = _saved(source._engine_pdf.extract_pages([0]))

    assert len(extract.pages) == 1
    assert _page_objects(extract) == 1
    engine = extract._engine_pdf
    for annotation in engine._resolve(engine._get_page_dict(0).mapping[PdfName("Annots")]).items:
        dest = engine._resolve(engine._resolve(annotation).mapping[PdfName("Dest")])
        assert isinstance(dest.items[0], PdfNull)


def test_extracted_links_by_name_land_on_the_pages_they_named():
    extract = _saved(_linked()._engine_pdf.extract_pages([0, 1, 3]))

    fit_second, fit_last = FitDestination(page=1), FitDestination(page=2)
    targets = _targets(extract, 0)
    assert targets[0] == [None, "Fit"]  # it named page 3, which was not taken
    assert targets[1:5] == [
        fit_second,
        fit_last,
        {"S": "GoTo", "D": fit_second},
        {"S": "GoTo", "D": fit_last},
    ]
    assert "Dest" not in extract.pages[0].annotations[5].properties  # an undefined name
    assert targets[6] == {"S": "GoToR", "F": "other.pdf", "D": "sec.two"}  # a name in the other file
    assert _page_objects(extract) == 3


def test_a_merged_copy_s_links_land_in_that_copy():
    document = _linked()
    document.merge(_linked())
    merged = _saved(document)

    first, second = _targets(merged, 0), _targets(merged, 4)
    # This document's own names stay names; they still mean this document.
    assert first[1:5] == ["sec.two", "old.four", {"S": "GoTo", "D": "sec.two"}, {"S": "GoTo", "D": "old.four"}]
    assert second[:5] == [
        FitDestination(page=6),
        FitDestination(page=5),
        FitDestination(page=7),
        {"S": "GoTo", "D": FitDestination(page=5)},
        {"S": "GoTo", "D": FitDestination(page=7)},
    ]
    assert _page_objects(merged) == 8
