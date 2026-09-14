"""A bookmark lands where pdfium and MuPDF say it lands -- or on no page.

The page index was read from a ``/Dest`` array only. A bookmark written as a
``/GoTo`` action got the typed view right but a bookmark naming its destination
-- the ``/Names /Dests`` tree LaTeX's hyperref and many other writers use, or
the older ``/Dests`` dictionary -- read as page 0. So did every bookmark that
lands on no page at all: one with no target (ISO 32000-1 Table 153 makes both
``/Dest`` and ``/A`` optional), a URI, a name nobody defined. And saving turned a
bookmark with no target into a jump to page 1.

Merging carried a named bookmark across as its *name*, which the merged
document resolves against its own names: a second copy of a document sent its
bookmarks into the first copy's pages.

Every expected page below is pdfium's, but for a remote destination. MuPDF
lands every bookmark on the same page, and reports the page-less ones its own
way.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfString
from aspose_pdf.outlines import OutlineItem

# (title, target entry, the page pdfium lands on)
_TARGETS = [
    (b"array", b"/Dest [12 0 R /Fit]", 2),
    (b"goto array", b"/A << /S /GoTo /D [12 0 R /XYZ 0 400 0] >>", 2),
    (b"tree string", b"/Dest (sec.two)", 1),
    (b"goto tree string", b"/A << /S /GoTo /D (sec.two) >>", 1),
    (b"legacy name", b"/Dest /old.four", 3),
    (b"goto legacy name", b"/A << /S /GoTo /D /old.four >>", 3),
    (b"deep tree entry", b"/Dest (zz.last)", 0),
    # Writers mix the two up; pdfium and MuPDF look each up in both places.
    (b"name only in the tree", b"/A << /S /GoTo /D /sec.two >>", 1),
    (b"string only in the dictionary", b"/Dest (old.four)", 3),
    (b"no target", b"", None),
    (b"uri", b"/A << /S /URI /URI (https://example.org) >>", None),
    (b"page not in the tree", b"/Dest [99 0 R /Fit]", None),
    (b"undefined name", b"/Dest (nowhere)", None),
    (b"goto undefined name", b"/A << /S /GoTo /D (nowhere) >>", None),
    # A remote destination's name is one in the other file (12.6.4.3), as MuPDF
    # reads it; pdfium alone looks it up in this document.
    (b"remote name", b"/A << /S /GoToR /F (other.pdf) /D (sec.two) >>", None),
]


def _pdf() -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R /Names << /Dests 4 0 R >> /Dests 5 0 R >>",
        2: b"<< /Type /Pages /Count 4 /Kids [10 0 R 11 0 R 12 0 R 13 0 R] >>",
        # An intermediate node, two leaves; the second holds a /D dictionary.
        4: b"<< /Kids [6 0 R 7 0 R] >>",
        5: b"<< /old.four [13 0 R /Fit] >>",
        6: b"<< /Limits [(a) (sec.two)] /Names [(a) [10 0 R /Fit] (sec.two) [11 0 R /Fit]] >>",
        7: b"<< /Limits [(zz.last) (zz.last)] /Names [(zz.last) << /D [10 0 R /FitH 100] >>] >>",
        99: b"<< /Type /Page /MediaBox [0 0 200 200] >>",
    }
    for index in range(4):
        objects[10 + index] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"
    first, count = 30, len(_TARGETS)
    objects[3] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (first, first + count - 1, count)
    for position, (title, target, _page) in enumerate(_TARGETS):
        links = b""
        if position:
            links += b" /Prev %d 0 R" % (first + position - 1)
        if position < count - 1:
            links += b" /Next %d 0 R" % (first + position + 1)
        objects[first + position] = b"<< /Title (" + title + b") /Parent 3 0 R" + links + b" " + target + b" >>"
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


def _saved(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _landings(document: Document) -> list[tuple[str, int | None]]:
    return [(item.title, item.page_index) for item in document.outlines]


_EXPECTED = [(title.decode(), page) for title, _target, page in _TARGETS]


def _cos_items(document: Document) -> list[PdfDictionary]:
    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    item = engine._resolve(engine._resolve(catalog.mapping[PdfName("Outlines")]).mapping[PdfName("First")])
    items = []
    while isinstance(item, PdfDictionary):
        items.append(item)
        item = engine._resolve(item.mapping.get(PdfName("Next")))
    return items


def test_every_kind_of_target_lands_where_pdfium_lands():
    assert _landings(Document(io.BytesIO(_pdf()))) == _EXPECTED


def test_saving_writes_every_target_back_as_it_was():
    again = _saved(Document(io.BytesIO(_pdf())))

    assert _landings(again) == _EXPECTED
    entries = {
        item.mapping[PdfName("Title")].value: (item.mapping.get(PdfName("Dest")), item.mapping.get(PdfName("A")))
        for item in _cos_items(again)
    }
    assert entries[b"no target"] == (None, None)
    # Dead already when read, so there is no index to fall back to: kept as is.
    assert isinstance(entries[b"page not in the tree"][0], PdfArray)
    assert isinstance(entries[b"tree string"][0], PdfString)
    assert entries[b"legacy name"][0] == PdfName("old.four")


def test_a_bookmark_can_be_given_no_target():
    document = Document()
    document.pages.add()
    document.outlines.add(OutlineItem("heading only", page_index=None))
    document.outlines.add(OutlineItem("first page"))
    again = _saved(document)

    assert _landings(again) == [("heading only", None), ("first page", 0)]
    heading, first = _cos_items(again)
    assert PdfName("Dest") not in heading.mapping and PdfName("A") not in heading.mapping
    assert isinstance(first.mapping[PdfName("Dest")], PdfArray)

    loaded = Document(io.BytesIO(_pdf()))
    next(item for item in loaded.outlines if item.title == "array").page_index = None
    saved = next(item for item in _cos_items(_saved(loaded)) if item.mapping[PdfName("Title")].value == b"array")
    assert PdfName("Dest") not in saved.mapping


def test_merged_bookmarks_land_in_their_own_copy():
    document = Document(io.BytesIO(_pdf()))
    document.merge(Document(io.BytesIO(_pdf())))
    again = _saved(document)

    shifted = [(title, None if page is None else page + 4) for title, page in _EXPECTED]
    assert _landings(again) == _EXPECTED + shifted
    second_copy = _cos_items(again)[len(_TARGETS) :]
    for item in second_copy:
        if item.mapping[PdfName("Title")].value == b"remote name":
            continue  # a name in the other file, and it stays one
        dest = again._engine_pdf._resolve(item.mapping.get(PdfName("Dest")))
        action = again._engine_pdf._resolve(item.mapping.get(PdfName("A")))
        goto = action.mapping.get(PdfName("D")) if isinstance(action, PdfDictionary) else None
        # Names were resolved on the way in; they would mean the first copy here.
        assert not isinstance(dest, (PdfString, PdfName))
        assert not isinstance(again._engine_pdf._resolve(goto), (PdfString, PdfName))
    titles = {item.mapping[PdfName("Title")].value: item for item in second_copy}
    # A link to a page the source did not have is not copied, page and all.
    assert PdfName("Dest") not in titles[b"page not in the tree"].mapping
    assert PdfName("Dest") not in titles[b"undefined name"].mapping
    assert PdfName("A") not in titles[b"goto undefined name"].mapping


def test_the_writer_without_an_object_graph_leaves_a_page_less_bookmark_without_a_target():
    from aspose_pdf.engine.simple_pdf import PdfWriterV0, SimplePdf

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200), (0, 0, 200, 200)]
    pdf.page_contents = [b"", b""]
    pdf.metadata = {}
    pdf._cos_doc = None
    pdf._outlines_data = [
        {"title": "heading only", "page_index": None, "children": []},
        {"title": "second page", "page_index": 1, "children": []},
    ]
    written = Document(io.BytesIO(PdfWriterV0(pdf).write()))

    assert _landings(written) == [("heading only", None), ("second page", 1)]
