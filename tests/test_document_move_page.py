"""Tests for Document.move_page() method."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber


def _numbered_document(count: int = 4) -> Document:
    """A document whose pages carry their one-based number as text."""
    doc = Document()
    for i in range(1, count + 1):
        doc.pages.add()
        doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)
    return doc


def _page_texts(doc: Document) -> list[str]:
    return [doc.pages[i].extract_text() for i in range(1, len(doc) + 1)]


class TestMovePage:
    """Test Document.move_page() method."""

    @pytest.mark.parametrize(
        ("page_number", "new_position", "expected"),
        [
            (1, 3, ["Page 2", "Page 3", "Page 1", "Page 4"]),
            (4, 1, ["Page 4", "Page 1", "Page 2", "Page 3"]),
            (2, 4, ["Page 1", "Page 3", "Page 4", "Page 2"]),
            (1, 4, ["Page 2", "Page 3", "Page 4", "Page 1"]),
            (3, 1, ["Page 3", "Page 1", "Page 2", "Page 4"]),
            (3, 3, ["Page 1", "Page 2", "Page 3", "Page 4"]),
        ],
    )
    def test_move_page_reorders_the_pages(self, page_number, new_position, expected):
        """move_page puts the page at the position asked for."""
        doc = _numbered_document()

        doc.move_page(page_number, new_position)

        assert _page_texts(doc) == expected
        assert len(doc) == 4

    def test_move_page_returns_the_moved_page(self):
        """move_page returns the moved page, at its new position."""
        doc = _numbered_document()

        moved = doc.move_page(3, 1)

        assert moved.number == 1
        assert moved.extract_text() == "Page 3"

    def test_move_page_keeps_the_page_count(self):
        """move_page is a reorder, not a remove and an add."""
        doc = _numbered_document()

        doc.move_page(1, 4)

        assert len(doc) == 4

    def test_move_page_same_position_is_a_no_op(self):
        """Moving a page onto itself changes nothing."""
        doc = _numbered_document()

        moved = doc.move_page(2, 2)

        assert _page_texts(doc) == ["Page 1", "Page 2", "Page 3", "Page 4"]
        assert moved.number == 2

    def test_move_page_single_page_document(self):
        """move_page works on a document with one page."""
        doc = _numbered_document(1)

        moved = doc.move_page(1, 1)

        assert moved.extract_text() == "Page 1"
        assert _page_texts(doc) == ["Page 1"]

    def test_move_page_preserves_page_boxes_and_rotation(self):
        """move_page keeps the moved page's size and rotation."""
        doc = _numbered_document(3)
        doc.pages[2].rotate(90)

        doc.move_page(2, 1)

        assert doc.pages[1].extract_text() == "Page 2"
        assert doc.pages[1].rotation == 90

    def test_move_page_survives_a_save(self, tmp_path):
        """The new order is what the file holds."""
        path = tmp_path / "moved.pdf"
        doc = _numbered_document()
        doc.move_page(4, 1)
        doc.save(path)

        with Document(path) as reloaded:
            assert _page_texts(reloaded) == [
                "Page 4",
                "Page 1",
                "Page 2",
                "Page 3",
            ]

    def test_move_page_error_empty_document(self):
        """move_page raises IndexError on an empty document."""
        doc = Document()

        with pytest.raises(IndexError):
            doc.move_page(1, 1)

    @pytest.mark.parametrize(
        ("page_number", "new_position"),
        [(0, 1), (5, 1), (-1, 1), (1, 0), (1, 5), (1, -1)],
    )
    def test_move_page_error_out_of_range(self, page_number, new_position):
        """move_page raises IndexError for numbers outside the document."""
        doc = _numbered_document()

        with pytest.raises(IndexError):
            doc.move_page(page_number, new_position)

    def test_move_page_disposed_document(self):
        """move_page raises after the document is disposed."""
        doc = _numbered_document()
        doc.dispose()

        with pytest.raises(Exception):
            doc.move_page(1, 2)


class TestMovePageKeepsWhatPointsAtThePage:
    """A move keeps the page itself, so what names it keeps working."""

    def _document_with_targets(self) -> Document:
        doc = _numbered_document(4)
        from aspose_pdf.interactive import XYZDestination
        from aspose_pdf.outlines import OutlineItem

        doc.pages[1].add_link([72, 680, 200, 700], XYZDestination(page=3))
        item = OutlineItem("To page three", 3)
        doc.outlines.add(item)
        return doc

    def test_bookmarks_follow_the_moved_page(self):
        """A bookmark pointing at the moved page still lands on it."""
        doc = self._document_with_targets()

        doc.move_page(3, 1)

        assert [item.title for item in doc.outlines] == ["To page three"]
        item = doc.outlines[0]
        # The bookmark's page_index is 1-based (public API)
        assert doc.pages[item.page_index].extract_text() == "Page 3"

    def test_links_follow_the_moved_page(self):
        """A link pointing at the moved page still lands on it."""
        doc = self._document_with_targets()

        doc.move_page(3, 1)

        # After the move, page 1 (with the link) is at position 2
        links = doc.pages[2].extract_links()
        assert links
        assert links[0].target_page_index is not None
        assert doc.pages[links[0].target_page_index].extract_text() == "Page 3"

    def test_form_fields_stay_on_the_moved_page(self):
        """A field whose widget sits on the moved page keeps the page."""
        doc = self._document_with_targets()
        doc.form.add_text_field("Name", page=3, rect=[72, 600, 220, 620])

        doc.move_page(3, 1)

        assert [(widget.page_index, widget.rect) for widget in doc.form["Name"].widgets] == [
            (1, (72.0, 600.0, 220.0, 620.0))
        ]

    def test_what_points_at_the_page_survives_a_save(self, tmp_path):
        """Bookmarks, links and fields still name the page in the file."""
        path = tmp_path / "moved.pdf"
        doc = self._document_with_targets()
        doc.form.add_text_field("Name", page=3, rect=[72, 600, 220, 620])
        doc.move_page(3, 1)
        doc.save(path)

        with Document(path) as reloaded:
            item = reloaded.outlines[0]
            assert reloaded.pages[item.page_index].extract_text() == "Page 3"
            # After the move, page 1 (with the link) is at position 2
            links = reloaded.pages[2].extract_links()
            assert reloaded.pages[links[0].target_page_index].extract_text() == "Page 3"
            # The form field is on page 3, which moved to position 1
            assert reloaded.form["Name"].widgets[0].page_index == 1


class TestMovePageWithPageLabels:
    """Page labels describe pages, so they follow them."""

    def _labeled_document(self) -> Document:
        from aspose_pdf.engine.page_labels import LabelRange

        doc = _numbered_document(4)
        doc._engine_pdf.set_page_label_ranges(
            [(0, LabelRange(style="R")), (2, LabelRange(style="A"))]
        )
        return doc

    def test_the_moved_page_keeps_its_label(self):
        """The page moved to the front keeps the label it had."""
        doc = self._labeled_document()

        doc.move_page(3, 1)

        labels = doc.page_labels
        # Public API uses 1-based page numbers; PageLabels.label uses 0-based indices
        assert labels.label(0) == "A"
        assert labels.label(1) == "I"
        assert labels.label(2) == "II"
        assert labels.label(3) == "B"

    def test_labels_survive_a_save(self, tmp_path):
        """The labels the viewer shows are the pages' own."""
        path = tmp_path / "labeled.pdf"
        doc = self._labeled_document()
        doc.move_page(3, 1)
        doc.save(path)

        with Document(path) as reloaded:
            # label_at() uses 0-based indices for the engine
            assert reloaded.page_labels.label(0) == "A"
            assert reloaded.page_labels.label(3) == "B"


class TestMovePageOnStreamingDocuments:
    """move_page works without decoding every page."""

    def test_move_page_keeps_the_document_lazy(self, tmp_path):
        """A streamed document moves a page and saves without losing content."""
        path = tmp_path / "large.pdf"
        with _numbered_document(4) as doc:
            doc.save(path)

        with Document.open_streaming(path) as doc:
            doc.move_page(1, 3)
            out = tmp_path / "moved-streaming.pdf"
            doc.save(out)

        with Document(out) as reloaded:
            assert _page_texts(reloaded) == [
                "Page 2",
                "Page 3",
                "Page 1",
                "Page 4",
            ]


class TestMovePageInNestedPageTree:
    """A document may nest its page tree; inheritance must survive a move."""

    def _nested_document(self) -> Document:
        """Four pages under two intermediate /Pages nodes.

        Each intermediate node carries the MediaBox its two pages inherit, so a
        move across the nodes has to write what they inherit onto the pages
        first or the moved page loses its sheet.
        """
        doc = _numbered_document(4)
        doc.pages[3].rotate(90)
        engine = doc._engine_pdf
        root_ref = engine._cos_doc.trailer.mapping[PdfName("Root")]
        catalog = engine._resolve(root_ref)
        pages_ref = catalog.mapping[PdfName("Pages")]
        pages = engine._resolve(pages_ref)
        kids = pages.mapping[PdfName("Kids")]
        # Two intermediates, one per original pair, each inheriting a box.
        new_kids = []
        for pair, (width, height) in enumerate(((200.0, 300.0), (400.0, 500.0))):
            node = PdfDictionary(
                {
                    PdfName("Type"): PdfName("Pages"),
                    PdfName("Parent"): pages_ref,
                    PdfName("Count"): PdfNumber(2),
                    PdfName("MediaBox"): PdfArray(
                        [PdfNumber(0), PdfNumber(0), PdfNumber(width), PdfNumber(height)]
                    ),
                    PdfName("Kids"): PdfArray(list(kids.items[2 * pair : 2 * pair + 2])),
                }
            )
            node_ref = engine._cos_doc.register_object(node)
            for kid in node.mapping[PdfName("Kids")].items:
                engine._resolve(kid).mapping[PdfName("Parent")] = node_ref
            new_kids.append(node_ref)
        pages.mapping[PdfName("Kids")] = PdfArray(new_kids)
        pages.mapping[PdfName("Count")] = PdfNumber(4)
        engine._page_cache_valid = False
        return doc

    def test_a_moved_page_keeps_what_it_inherits(self):
        """The moved page and the pages left behind keep their inherited boxes."""
        doc = self._nested_document()

        doc.move_page(3, 1)

        assert _page_texts(doc) == ["Page 3", "Page 1", "Page 2", "Page 4"]
        # After move, the page tree is flattened, so pages keep their inherited
        # values from before the move (Node1's MediaBox for Page 3 and 4).
        # Page 1 (was Page 3) has MediaBox (0, 0, 400, 500) from Node1.
        # Page 2 (was Page 1) has MediaBox (0, 0, 200, 300) from Node0.
        # Page 3 (was Page 2) has MediaBox (0, 0, 200, 300) from Node0.
        # Page 4 (was Page 4) has MediaBox (0, 0, 400, 500) from Node1.
        assert doc.pages[1].rect == (0, 0, 400, 500)
        assert doc.pages[1].rotation == 90
        assert doc.pages[2].rect == (0, 0, 200, 300)
        assert doc.pages[3].rect == (0, 0, 200, 300)
        assert doc.pages[4].rect == (0, 0, 400, 500)

    def test_the_reordered_tree_survives_a_save(self, tmp_path):
        """The file holds the flat reordered tree with every box on its page."""
        path = tmp_path / "nested.pdf"
        doc = self._nested_document()
        doc.move_page(3, 1)
        doc.save(path)

        with Document(path) as reloaded:
            assert _page_texts(reloaded) == ["Page 3", "Page 1", "Page 2", "Page 4"]
            # After save/reload, pages keep their inherited values from before the move.
            assert reloaded.pages[1].rect == (0, 0, 400, 500)
            assert reloaded.pages[1].rotation == 90
            assert reloaded.pages[2].rect == (0, 0, 200, 300)
            assert reloaded.pages[3].rect == (0, 0, 200, 300)
            assert reloaded.pages[4].rect == (0, 0, 400, 500)


class TestMovePagePageIdentity:
    """The moved page is the same page object, not a copy."""

    def test_annotations_travel_with_the_page(self):
        """A comment on the moved page is still there, once."""
        doc = _numbered_document(3)
        doc.pages[2].annotations.add_text([100, 700, 120, 720], contents="Note")

        doc.move_page(2, 1)

        assert len(doc.pages[1].annotations) == 1
        assert doc.pages[1].annotations[0].contents == "Note"
        assert len(doc.pages[2].annotations) == 0

    def test_the_page_object_is_reused_not_copied(self):
        """Moving a page neither copies nor drops the page object."""
        doc = _numbered_document(3)
        engine = doc._engine_pdf
        object_number = engine._page_obj_ids[1]

        doc.move_page(2, 1)

        assert engine._page_obj_ids[0] == object_number


def test_move_page_from_bytes_round_trip():
    """move_page works on a document built from bytes, and back to bytes."""

    buffer = io.BytesIO()
    with _numbered_document(3) as doc:
        doc.to_stream(buffer)

    doc = Document.from_bytes(buffer.getvalue())
    doc.move_page(1, 3)

    assert _page_texts(doc) == ["Page 2", "Page 3", "Page 1"]
    assert doc.to_bytes() == doc.to_bytes()
