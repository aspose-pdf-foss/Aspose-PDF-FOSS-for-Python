"""An imported page must not keep its index into a parent tree it left behind.

ISO 32000-1 14.7.4.4: a page's ``/StructParents`` and an annotation's
``/StructParent`` are keys into **their own document's** ``/ParentTree`` -- the
number that says which structure elements the page's marked content belongs to.

Merging does not carry the structure tree across; that is a reasoned boundary
(the tree describes a whole document, not a page). But the key came anyway, and
a key without the tree it indexes is worse than no key: merged into a tagged
document, an imported page arrived holding ``/StructParents 0`` and so claimed
the *target's* first page's headings as its own content, and two pages then
answered to the same entry. Extracted or concatenated into a fresh document, it
pointed at a ``/StructTreeRoot`` that was not there at all.
"""

from __future__ import annotations

import io
from pathlib import Path

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName


def _tagged(pages: int = 2, label: str = "A") -> Document:
    document = Document()
    for index in range(pages):
        document.pages.add()
        document.pages[index].add_text(f"{label}{index} heading", x=40, y=700)
        document.pages[index].add_text(f"{label}{index} body", x=40, y=660)
    content = document.tagged_content
    root = content.add_element("Document")
    for index in range(pages):
        content.add_element("H1", parent=root, page_number=index + 1, mcids=[0])
        content.add_element("P", parent=root, page_number=index + 1, mcids=[1])
    return document


def _reopened(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _struct_parents(document: Document) -> list[int | None]:
    engine = document._engine_pdf
    keys: list[int | None] = []
    for index in range(len(document.pages)):
        page = engine._get_page_dict(index)
        value = engine._resolve(page.mapping.get(PdfName("StructParents")))
        keys.append(None if value is None else int(value.value))
    return keys


def _annotation_keys(document: Document, page_index: int) -> list[int | None]:
    engine = document._engine_pdf
    page = engine._get_page_dict(page_index)
    annots = engine._resolve(page.mapping.get(PdfName("Annots")))
    if annots is None:
        return []
    out = []
    for ref in annots.items:
        annot = engine._resolve(ref)
        value = engine._resolve(annot.mapping.get(PdfName("StructParent")))
        out.append(None if value is None else int(value.value))
    return out


def test_a_tagged_document_keys_its_own_pages():
    document = _reopened(_tagged(pages=2))
    assert _struct_parents(document) == [0, 1]


def test_a_merged_in_page_does_not_claim_a_key():
    target = _tagged(pages=2, label="A")
    target.merge(_tagged(pages=1, label="B"))
    keys = _struct_parents(_reopened(target))
    assert keys[:2] == [0, 1]  # the target's own pages are untouched
    assert keys[2] is None  # and the newcomer indexes nothing


def test_no_two_pages_answer_to_the_same_key():
    target = _tagged(pages=2, label="A")
    target.merge(_tagged(pages=2, label="B"))
    keys = [key for key in _struct_parents(_reopened(target)) if key is not None]
    assert len(keys) == len(set(keys))


def test_an_extracted_page_carries_no_key_into_a_tree_it_left():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    buffer = io.BytesIO()
    _tagged(pages=2).save(buffer)
    source = SimplePdf.from_bytes(buffer.getvalue())
    extracted = source.extract_pages([0])
    document = Document()
    document._engine_pdf = extracted
    assert _struct_parents(_reopened(document)) == [None]


def test_an_imported_annotation_drops_its_own_key_too():
    from aspose_pdf.interactive import FitDestination

    other = _tagged(pages=1, label="B")
    other.pages[0].add_link((40, 400, 200, 430), FitDestination(page=0))
    other = _reopened(other)
    # Give the link a structure key, as a tagged document would.
    engine = other._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Annots")])
    engine._resolve(annots.items[0]).mapping[PdfName("StructParent")] = __import__(
        "aspose_pdf.engine.cos", fromlist=["PdfNumber"]
    ).PdfNumber(7)
    assert _annotation_keys(other, 0) == [7]

    target = _tagged(pages=1, label="A")
    target.merge(other)
    assert _annotation_keys(_reopened(target), 1) == [None]


def test_the_target_keeps_its_own_structure_tree():
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=1, label="B"))
    engine = _reopened(target)._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    assert isinstance(
        engine._resolve(root.mapping.get(PdfName("StructTreeRoot"))), PdfDictionary
    )
    marked = engine._resolve(root.mapping.get(PdfName("MarkInfo")))
    assert marked is not None


def test_the_facade_paths_go_the_same_way(tmp_path: Path):
    from aspose_pdf.facades import PdfFileEditor

    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _tagged(pages=2, label="A").save(str(first))
    _tagged(pages=1, label="B").save(str(second))

    concatenated, extracted, inserted = (
        tmp_path / "cat.pdf",
        tmp_path / "ext.pdf",
        tmp_path / "ins.pdf",
    )
    editor = PdfFileEditor()
    assert editor.concatenate([str(first), str(second)], str(concatenated))
    assert editor.extract(str(first), str(extracted), 1, 1)
    assert editor.insert(str(first), str(second), str(inserted), 1)

    # Concatenate and extract build a fresh document, so nothing in the result
    # indexes a parent tree.
    for path in (concatenated, extracted):
        assert set(_struct_parents(Document(str(path)))) == {None}, path
    # Insert keeps the *base* document, whose own pages keep their own valid
    # keys; only the page that came from elsewhere gives its up.
    keys = _struct_parents(Document(str(inserted)))
    assert keys[0] is None  # the inserted page, at position 1
    assert sorted(k for k in keys if k is not None) == [0, 1]


def test_an_imported_page_gets_one_content_stream_and_not_two():
    # `/Contents` is rebuilt for the copy, so importing the source's reference
    # as well would attach a second, identical stream: the same drawing in a
    # bigger file, on every page of every merge.
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=1, label="B"))
    merged = _reopened(target)
    engine = merged._engine_pdf
    before = len(engine._cos_doc.objects)

    plain = _tagged(pages=1, label="A")
    plain_objects = len(_reopened(plain)._engine_pdf._cos_doc.objects)
    # One page brings a page dictionary and one content stream. A second copy
    # of the content would make it three.
    assert before - plain_objects == 2


def test_a_page_that_was_never_tagged_is_unaffected():
    plain = Document()
    plain.pages.add()
    plain.pages[0].add_text("plain", x=40, y=700)
    target = _tagged(pages=1, label="A")
    target.merge(plain)
    assert _struct_parents(_reopened(target)) == [0, None]
