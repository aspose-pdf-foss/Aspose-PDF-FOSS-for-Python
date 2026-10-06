"""Merging carries the structure that describes the pages it brings.

ISO 32000-1 14.7.2: a tagged document's logical structure is a tree of elements,
each naming the page its content sits on through ``/Pg`` and reached from that
page through ``/StructParents`` -- a key into the document's own ``/ParentTree``.
Appending left the tree behind. The key was stripped too (see
``test_merged_structure_keys``), which stopped an imported page claiming someone
else's headings, but the content stream still arrived full of ``/P <</MCID 0>>
BDC`` with nothing describing it -- in a document that went on saying
``/MarkInfo /Marked true``. Measured on a merge of two one-paragraph tagged
documents: two pages, both carrying marked content, and **one** structure
element between them; merged the other way round, into an untagged document, no
``/StructTreeRoot`` at all and the marked content orphaned.

Assembling an accessible report out of parts is most of the reason to merge, so
the elements travel: each page's entry in the source parent tree is imported
under a fresh key, and the top of each imported subtree is re-parented onto this
document's ``/StructTreeRoot`` -- spliced into its ``Document`` element when both
have one, because ISO 14289-2 8.2.5.2 wants a single one at the root.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName


def _tagged(pages: int = 1, label: str = "A", *, wrap: bool = True) -> Document:
    """A tagged document of *pages* pages, each with a heading and a paragraph."""
    document = Document()
    for index in range(pages):
        document.pages.add()
        document.pages[index].add_text(f"{label}{index} heading", x=40, y=700)
        document.pages[index].add_text(f"{label}{index} body", x=40, y=660)
    content = document.tagged_content
    parent = content.add_element("Document") if wrap else None
    for index in range(pages):
        content.add_element("H1", parent=parent, page_number=index + 1, mcids=[0])
        content.add_element("P", parent=parent, page_number=index + 1, mcids=[1])
    return document


def _plain(pages: int = 1, label: str = "Z") -> Document:
    document = Document()
    for index in range(pages):
        document.pages.add()
        document.pages[index].add_text(f"{label}{index}", x=40, y=700)
    return document


def _reopened(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


# --- reading the tree back out ----------------------------------------------


def _catalog(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    return engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])


def _struct_root(document: Document):
    engine = document._engine_pdf
    return engine._resolve(_catalog(document).mapping.get(PdfName("StructTreeRoot")))


def _children(document: Document, node: PdfDictionary) -> list[PdfDictionary]:
    engine = document._engine_pdf
    kids = engine._resolve(node.mapping.get(PdfName("K")))
    items = kids.items if isinstance(kids, PdfArray) else ([kids] if kids else [])
    out = []
    for item in items:
        resolved = engine._resolve(item)
        if isinstance(resolved, PdfDictionary) and PdfName("S") in resolved.mapping:
            out.append(resolved)
    return out


def _page_numbers(document: Document) -> list[int]:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    return list(engine._page_refs)


def _shape(document: Document) -> list:
    """The tree as nested ``[tag, page index, [children]]`` lists."""
    engine = document._engine_pdf
    refs = _page_numbers(document)

    def page_of(element: PdfDictionary):
        ref = element.mapping.get(PdfName("Pg"))
        number = getattr(ref, "object_number", None)
        return next((i for i, value in enumerate(refs) if value == number), None)

    def walk(node):
        return [
            [engine._get_name(child.mapping.get(PdfName("S"))), page_of(child),
             walk(child)]
            for child in _children(document, node)
        ]

    root = _struct_root(document)
    return walk(root) if isinstance(root, PdfDictionary) else []


def _keys(document: Document) -> list[int | None]:
    engine = document._engine_pdf
    out = []
    for index in range(len(document.pages)):
        value = engine._resolve(
            engine._get_page_dict(index).mapping.get(PdfName("StructParents"))
        )
        out.append(None if value is None else int(value.value))
    return out


def _entry(document: Document, key: int) -> list[str]:
    engine = document._engine_pdf
    pairs = dict(
        engine._tagged_number_tree_pairs(
            engine._resolve(_struct_root(document).mapping.get(PdfName("ParentTree")))
        )
    )
    entry = pairs[key]
    return [
        engine._get_name(engine._resolve(item).mapping.get(PdfName("S")))
        for item in entry.items
    ]


# --- the tree arrives -------------------------------------------------------


def test_both_documents_elements_are_in_the_merged_tree():
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=1, label="B"))
    merged = _reopened(target)
    assert _shape(merged) == [
        ["Document", None, [["H1", 0, []], ["P", 0, []], ["H1", 1, []], ["P", 1, []]]]
    ]


def test_each_page_keeps_its_own_entry_in_mcid_order():
    target = _tagged(pages=2, label="A")
    target.merge(_tagged(pages=2, label="B"))
    merged = _reopened(target)
    keys = _keys(merged)
    assert None not in keys and len(set(keys)) == 4
    for key in keys:
        # The heading is MCID 0 and the paragraph MCID 1, which is the order
        # the parent-tree entry indexes them by.
        assert _entry(merged, key) == ["H1", "P"]


def test_a_single_document_element_stays_single():
    # ISO 14289-2 8.2.5.2: one Document at the root. Three merged documents
    # must not leave three of them side by side.
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=1, label="B"), _tagged(pages=1, label="C"))
    shape = _shape(_reopened(target))
    assert len(shape) == 1
    assert shape[0][0] == "Document"
    assert [child[0] for child in shape[0][2]] == ["H1", "P"] * 3
    assert [child[1] for child in shape[0][2]] == [0, 0, 1, 1, 2, 2]


def test_elements_without_a_document_wrapper_land_at_the_root():
    target = _tagged(pages=1, label="A", wrap=False)
    target.merge(_tagged(pages=1, label="B", wrap=False))
    assert [item[:2] for item in _shape(_reopened(target))] == [
        ["H1", 0], ["P", 0], ["H1", 1], ["P", 1]
    ]


def test_an_untagged_document_becomes_tagged_by_what_it_is_given():
    target = _plain(pages=2)
    target.merge(_tagged(pages=1, label="B"))
    merged = _reopened(target)
    engine = merged._engine_pdf
    mark_info = engine._resolve(_catalog(merged).mapping.get(PdfName("MarkInfo")))
    assert isinstance(mark_info, PdfDictionary)
    assert engine._resolve(mark_info.mapping.get(PdfName("Marked"))).value is True
    assert [item[:2] for item in _shape(merged)[0][2]] == [["H1", 2], ["P", 2]]
    # The pages that were never tagged are left alone.
    assert _keys(merged)[:2] == [None, None]


def test_a_merge_that_brings_no_structure_leaves_the_catalog_alone():
    target = _plain(pages=1)
    target.merge(_plain(pages=1, label="Y"))
    merged = _reopened(target)
    assert PdfName("StructTreeRoot") not in _catalog(merged).mapping
    assert PdfName("MarkInfo") not in _catalog(merged).mapping


def test_only_the_pages_taken_bring_their_elements():
    # A subset of pages is a different document: an element whose /Pg was left
    # behind describes nothing and does not come.
    from aspose_pdf.engine.simple_pdf import SimplePdf

    buffer = io.BytesIO()
    _tagged(pages=3, label="A").save(buffer)
    source = SimplePdf.from_bytes(buffer.getvalue())
    document = Document()
    document._engine_pdf = source.extract_pages([1])
    extracted = _reopened(document)
    assert len(extracted.pages) == 1
    assert [item[:2] for item in _shape(extracted)[0][2]] == [["H1", 0], ["P", 0]]
    assert _entry(extracted, _keys(extracted)[0]) == ["H1", "P"]


def test_merging_a_document_with_itself_keys_the_copies_apart():
    target = _tagged(pages=2, label="A")
    buffer = io.BytesIO()
    target.save(buffer)
    again = Document(io.BytesIO(buffer.getvalue()))
    reopened = Document(io.BytesIO(buffer.getvalue()))
    reopened.merge(again)
    merged = _reopened(reopened)
    keys = _keys(merged)
    assert len(set(keys)) == 4 and None not in keys
    assert [child[1] for child in _shape(merged)[0][2]] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_the_role_map_of_both_documents_survives():
    target = _tagged(pages=1, label="A")
    other = _tagged(pages=1, label="B")
    for document, role, mapped in ((target, "Chapter", "Sect"), (other, "Note", "P")):
        engine = document._engine_pdf
        struct_root, _ref = engine._ensure_struct_tree_root()
        role_map = engine._resolve(struct_root.mapping.get(PdfName("RoleMap")))
        if not isinstance(role_map, PdfDictionary):
            role_map = PdfDictionary({})
            struct_root.mapping[PdfName("RoleMap")] = role_map
        role_map.mapping[PdfName(role)] = PdfName(mapped)
    target.merge(other)
    merged = _reopened(target)
    engine = merged._engine_pdf
    role_map = engine._resolve(_struct_root(merged).mapping.get(PdfName("RoleMap")))
    assert engine._get_name(role_map.mapping.get(PdfName("Chapter"))) == "Sect"
    assert engine._get_name(role_map.mapping.get(PdfName("Note"))) == "P"


def test_the_pages_still_draw_what_they_drew():
    # The structure travelling must not disturb the content: this is the
    # invariant the whole merge audit rests on.
    target = _tagged(pages=2, label="A")
    other = _tagged(pages=2, label="B")
    expected = [other.pages[i].extract_text() for i in range(2)]
    target.merge(other)
    merged = _reopened(target)
    assert [merged.pages[i].extract_text() for i in (2, 3)] == expected


# --- the edges the pruning has to get right ---------------------------------


def _extract(document: Document, pages: list[int]) -> Document:
    from aspose_pdf.engine.simple_pdf import SimplePdf

    buffer = io.BytesIO()
    document.save(buffer)
    taken = Document()
    taken._engine_pdf = SimplePdf.from_bytes(buffer.getvalue()).extract_pages(pages)
    return _reopened(taken)


def test_a_tree_that_describes_no_page_taken_leaves_the_target_untagged():
    # The source *is* tagged, but nothing it describes is coming: turning the
    # target into a tagged document would advertise a tree with nothing in it.
    source = _tagged(pages=1, label="B")
    engine = source._engine_pdf
    # Take the page's key away, so its elements are unreachable from it.
    engine._get_page_dict(0).mapping.pop(PdfName("StructParents"), None)
    target = _plain(pages=1)
    target.merge(_reopened(source))
    merged = _reopened(target)
    assert PdfName("StructTreeRoot") not in _catalog(merged).mapping
    assert PdfName("MarkInfo") not in _catalog(merged).mapping


def test_tops_describing_pages_left_behind_do_not_come():
    # Without the Document wrapper each page's elements are their own top-level
    # elements, so extracting one page must bring two tops and not six.
    extracted = _extract(_tagged(pages=3, label="A", wrap=False), [1])
    assert [item[:2] for item in _shape(extracted)] == [["H1", 0], ["P", 0]]


def test_an_element_kept_for_a_descendant_drops_the_ids_of_a_page_left_behind():
    # A section on page 0 that also contains a paragraph on page 1: taking only
    # page 1 keeps the section, because of the paragraph, but its own
    # marked-content id belongs to a page that did not come.
    document = _plain(pages=2, label="S")
    engine = document._engine_pdf
    struct_root, struct_ref = engine._ensure_struct_tree_root()
    pages = [engine._page_ref_for_structure(index) for index in range(2)]
    paragraph = PdfDictionary(
        {
            PdfName("Type"): PdfName("StructElem"),
            PdfName("S"): PdfName("P"),
            PdfName("Pg"): pages[1],
            PdfName("K"): PdfArray([]),
        }
    )
    paragraph_ref = engine._cos_doc.register_object(paragraph)
    from aspose_pdf.engine.cos import PdfNumber

    paragraph.mapping[PdfName("K")] = PdfArray([PdfNumber(0)])
    section = PdfDictionary(
        {
            PdfName("Type"): PdfName("StructElem"),
            PdfName("S"): PdfName("Sect"),
            PdfName("Pg"): pages[0],
            PdfName("P"): struct_ref,
            PdfName("K"): PdfArray([PdfNumber(0), paragraph_ref]),
        }
    )
    section_ref = engine._cos_doc.register_object(section)
    paragraph.mapping[PdfName("P")] = section_ref
    engine._resolve(struct_root.mapping[PdfName("K")]).items.append(section_ref)
    for index, element in ((0, section_ref), (1, paragraph_ref)):
        page = engine._get_page_dict(index)
        entry = engine._parent_tree_array_for_page(struct_root, page)
        entry.items.append(element)

    extracted = _extract(document, [1])
    shape = _shape(extracted)
    assert [item[:2] for item in shape] == [["Sect", None]]
    assert [item[:2] for item in shape[0][2]] == [["P", 0]]
    section_kids = _children(extracted, _struct_root(extracted))[0]
    kids = extracted._engine_pdf._resolve(section_kids.mapping.get(PdfName("K")))
    # The surviving paragraph, and not the id of the content left behind.
    assert len(kids.items) == 1


def test_every_imported_element_points_back_at_its_parent():
    target = _tagged(pages=1, label="A")
    target.merge(_tagged(pages=2, label="B"))
    merged = _reopened(target)
    engine = merged._engine_pdf
    root = _struct_root(merged)
    root_number = getattr(
        _catalog(merged).mapping.get(PdfName("StructTreeRoot")), "object_number", None
    )

    def check(node, expected_number):
        for child in _children(merged, node):
            parent = child.mapping.get(PdfName("P"))
            assert getattr(parent, "object_number", None) == expected_number, (
                engine._get_name(child.mapping.get(PdfName("S")))
            )
            own = None
            for number, obj in engine._cos_doc.objects.items():
                if obj is child:
                    own = number
                    break
            check(child, own)

    check(root, root_number)


def test_a_nested_subtree_keeps_its_shape_and_its_parent_links():
    # Three levels deep, so the link from a grandchild to its own parent is
    # exercised and not just the splice at the root.
    source = _plain(pages=1, label="N")
    content = source.tagged_content
    document = content.add_element("Document")
    section = content.add_element("Sect", parent=document)
    content.add_element("H1", parent=section, page_number=1, mcids=[0])
    content.add_element("P", parent=section, page_number=1, mcids=[1])

    target = _tagged(pages=1, label="A")
    target.merge(_reopened(source))
    merged = _reopened(target)
    shape = _shape(merged)
    assert len(shape) == 1 and shape[0][0] == "Document"
    assert [child[0] for child in shape[0][2]] == ["H1", "P", "Sect"]
    section_shape = shape[0][2][2]
    assert [grandchild[:2] for grandchild in section_shape[2]] == [
        ["H1", 1], ["P", 1]
    ]

    engine = merged._engine_pdf
    root_element = _children(merged, _struct_root(merged))[0]
    imported_section = _children(merged, root_element)[2]
    for grandchild in _children(merged, imported_section):
        parent = grandchild.mapping.get(PdfName("P"))
        target_number = next(
            number
            for number, obj in engine._cos_doc.objects.items()
            if obj is imported_section
        )
        assert getattr(parent, "object_number", None) == target_number
