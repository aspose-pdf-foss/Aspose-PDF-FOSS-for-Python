"""An annotation that names another annotation is not a property of it.

A sticky note's ``/Popup`` is an annotation whose ``/Parent`` is the note, and a
reply's ``/IRT`` is the note it answers (ISO 32000-1 12.5.6.14, 12.5.6.2). The
property channel inlined whatever an entry referenced, so reading a note walked
note -> popup -> note and raised ``Annotation property graph contains a cycle``
-- for every comment Acrobat or MuPDF writes. ``page.annotations``,
``Document.flatten()`` and ``annotations.delete()`` all failed on such a page.
The earlier fix for the same symptom excluded pages; this excludes annotations,
which are read from the page's own ``/Annots``.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfIndirectReference, PdfName


def _pdf(popup_type: bytes = b"/Type /Annot ") -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Annots [10 0 R 11 0 R 12 0 R 13 0 R] >>",
        10: b"<< /Type /Annot /Subtype /Text /Rect [20 300 40 320] /Contents (note) /P 3 0 R /Popup 11 0 R "
        b"/C [1 1 0] /Name /Comment /BS << /Type /Border /W 2 >> >>",
        11: b"<< " + popup_type + b"/Subtype /Popup /Rect [60 250 260 350] /Parent 10 0 R >>",
        12: b"<< /Type /Annot /Subtype /Text /Rect [50 300 70 320] /Contents (reply) /P 3 0 R /IRT 10 0 R "
        b"/Popup 13 0 R /RT /R /Measure << /Subtype /RL /R (1 in = 1 in) >> >>",
        13: b"<< " + popup_type + b"/Subtype /Popup /Rect [60 150 260 250] /Parent 12 0 R >>",
    }
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


def test_notes_popups_and_replies_can_be_read():
    for popup_type in (b"/Type /Annot ", b""):  # /Type is optional on an annotation
        annotations = list(Document(io.BytesIO(_pdf(popup_type))).pages[0].annotations)
        assert [(a.subtype, a.contents) for a in annotations] == [
            ("Text", "note"),
            ("Popup", ""),
            ("Text", "reply"),
            ("Popup", ""),
        ], popup_type
        note, _popup, reply, _ = annotations
        assert set(note.properties) == {"C", "Name", "BS"}  # a typed dictionary that is no annotation stays
        assert set(reply.properties) == {"RT", "Measure"}  # untyped, with /Subtype but no /Rect


def test_editing_a_note_keeps_its_popup_and_reply_linked():
    document = Document(io.BytesIO(_pdf()))
    note, _popup, reply, _ = document.pages[0].annotations
    note.contents = "edited"
    reply.set_property("C", [0, 0, 1])
    reopened = _saved(document)

    engine = reopened._engine_pdf
    page = engine._get_page_dict(0)
    refs = engine._resolve(page.mapping[PdfName("Annots")]).items
    assert all(isinstance(ref, PdfIndirectReference) for ref in refs)
    note_ref, popup_ref, reply_ref, reply_popup_ref = refs
    note_dict, popup, reply_dict, reply_popup = (engine._resolve(ref) for ref in refs)

    def points_at(value, ref):
        return isinstance(value, PdfIndirectReference) and value.object_number == ref.object_number

    assert points_at(note_dict.mapping[PdfName("Popup")], popup_ref)
    assert points_at(popup.mapping[PdfName("Parent")], note_ref)
    assert points_at(reply_dict.mapping[PdfName("IRT")], note_ref)
    assert points_at(reply_dict.mapping[PdfName("Popup")], reply_popup_ref)
    assert points_at(reply_popup.mapping[PdfName("Parent")], reply_ref)
    reread = list(reopened.pages[0].annotations)
    assert (reread[0].subtype, reread[0].contents) == ("Text", "edited")
    assert reread[2].properties["C"] == [0, 0, 1]


def test_a_page_with_comments_can_be_flattened_and_edited():
    document = Document(io.BytesIO(_pdf()))
    document.flatten()
    _saved(document)

    document = Document(io.BytesIO(_pdf()))
    document.pages[0].annotations.delete(3)
    assert [a.subtype for a in _saved(document).pages[0].annotations] == ["Text", "Popup", "Text"]
