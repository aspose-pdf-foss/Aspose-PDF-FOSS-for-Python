"""An imported page must not keep its index into a parent tree it left behind.

ISO 32000-1 14.7.4.4: a page's ``/StructParents`` and an annotation's
``/StructParent`` are keys into **their own document's** ``/ParentTree`` -- the
number that says which structure elements the page's marked content belongs to.

The key used to come without the tree it indexes, which is worse than no key:
merged into a tagged document, an imported page arrived holding
``/StructParents 0`` and so claimed the *target's* first page's headings as its
own content, and two pages then answered to the same entry. Extracted or
concatenated into a fresh document, it pointed at a ``/StructTreeRoot`` that was
not there at all.

That was first fixed by dropping the key. The tree travels now instead -- the
elements describing the pages taken are imported, re-keyed and re-parented, so
an imported page keeps both its key *and* what the key points at. The invariant
these tests hold to is the same one throughout: **no page answers to a key that
does not describe it.**
"""

from __future__ import annotations

import io
from pathlib import Path

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName


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


def _parent_tree_entry(document: Document, key: int):
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    struct_root = engine._resolve(root.mapping.get(PdfName("StructTreeRoot")))
    assert isinstance(struct_root, PdfDictionary), "no structure tree"
    pairs = dict(
        engine._tagged_number_tree_pairs(
            engine._resolve(struct_root.mapping.get(PdfName("ParentTree")))
        )
    )
    return engine, pairs.get(key)


def _elements_for_key(document: Document, key: int) -> list[str]:
    """The structure tags the parent tree lists for *key*, in MCID order."""
    engine, entry = _parent_tree_entry(document, key)
    assert entry is not None, f"no parent-tree entry for key {key}"
    return [
        engine._get_name(engine._resolve(item).mapping.get(PdfName("S")))
        for item in entry.items
    ]


def _pages_of_elements_for_key(document: Document, key: int) -> list[int | None]:
    """The page index each of those elements says it is on."""
    engine, entry = _parent_tree_entry(document, key)
    out: list[int | None] = []
    for item in entry.items:
        page_ref = engine._resolve(item).mapping.get(PdfName("Pg"))
        number = getattr(page_ref, "object_number", None)
        out.append(
            next(
                (i for i in range(len(document.pages)) if engine._page_refs[i] == number),
                None,
            )
        )
    return out


def test_a_tagged_document_keys_its_own_pages():
    document = _reopened(_tagged(pages=2))
    assert _struct_parents(document) == [0, 1]


def test_a_merged_in_page_gets_a_key_of_its_own():
    target = _tagged(pages=2, label="A")
    target.merge(_tagged(pages=1, label="B"))
    merged = _reopened(target)
    keys = _struct_parents(merged)
    assert keys[:2] == [0, 1]  # the target's own pages are untouched
    assert keys[2] is not None and keys[2] not in keys[:2]
    # and the key leads to the newcomer's own elements, on the newcomer's page
    assert _elements_for_key(merged, keys[2]) == ["H1", "P"]
    assert _pages_of_elements_for_key(merged, keys[2]) == [2, 2]


def test_no_two_pages_answer_to_the_same_key():
    target = _tagged(pages=2, label="A")
    target.merge(_tagged(pages=2, label="B"))
    keys = [key for key in _struct_parents(_reopened(target)) if key is not None]
    assert len(keys) == len(set(keys))


def test_an_extracted_page_brings_the_part_of_the_tree_that_describes_it():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    buffer = io.BytesIO()
    _tagged(pages=2).save(buffer)
    source = SimplePdf.from_bytes(buffer.getvalue())
    extracted = source.extract_pages([0])
    document = Document()
    document._engine_pdf = extracted
    reopened = _reopened(document)
    keys = _struct_parents(reopened)
    assert keys == [0]
    # The page it describes, and nothing about the page left behind.
    assert _elements_for_key(reopened, 0) == ["H1", "P"]
    assert _pages_of_elements_for_key(reopened, 0) == [0, 0]


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

    # Every path keeps the pages' own structure with them, and no two pages
    # answer to one key.
    for path in (concatenated, extracted, inserted):
        document = Document(str(path))
        keys = _struct_parents(document)
        assert None not in keys, (path, keys)
        assert len(set(keys)) == len(keys), (path, keys)
        for index, key in enumerate(keys):
            assert _elements_for_key(document, key) == ["H1", "P"], (path, index)
            assert _pages_of_elements_for_key(document, key) == [index, index], (
                path,
                index,
            )


def test_an_imported_page_gets_one_content_stream_and_not_two():
    # `/Contents` is rebuilt for the copy, so importing the source's reference
    # as well would attach a second, identical stream: the same drawing in a
    # bigger file, on every page of every merge. Counted as streams rather than
    # as objects, because the structure elements travel with the page now and a
    # total would move every time the tree does.
    def stream_count(document: Document, index: int) -> int:
        engine = document._engine_pdf
        contents = engine._resolve(
            engine._get_page_dict(index).mapping.get(PdfName("Contents"))
        )
        return len(contents.items) if isinstance(contents, PdfArray) else 1

    source = _reopened(_tagged(pages=1, label="B"))
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=1, label="B"))
    merged = _reopened(target)
    # One rebuilt stream for the copy -- importing the source's reference as
    # well would make it two -- and it draws what the source drew, once.
    assert stream_count(merged, 1) == 1
    assert merged.pages[1].extract_text() == source.pages[0].extract_text()


def test_a_page_that_was_never_tagged_is_unaffected():
    plain = Document()
    plain.pages.add()
    plain.pages[0].add_text("plain", x=40, y=700)
    target = _tagged(pages=1, label="A")
    target.merge(plain)
    assert _struct_parents(_reopened(target)) == [0, None]
