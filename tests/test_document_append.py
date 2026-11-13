import io
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    with Document() as doc:
        page = doc.pages.add()
        page.add_text(text, 72, 700, font_size=12)
        doc.save(buf)
    return buf.getvalue()


class TestDocumentAppend:
    def test_append_basic_concatenation(self, tmp_path: Path):
        source_a = tmp_path / "a.pdf"
        source_b = tmp_path / "b.pdf"
        target = tmp_path / "combined.pdf"
        
        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page from A", 72, 700)
            doc.save(source_a)
        
        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page from B", 72, 700)
            doc.save(source_b)
        
        with Document(source_a) as doc1:
            with Document(source_b) as doc2:
                doc1.append(doc2)
                doc1.save(target)
        
        with Document(target) as combined:
            assert combined.page_count == 2
            assert "Page from A" in combined.pages[1].extract_text()
            assert "Page from B" in combined.pages[2].extract_text()

    def test_append_with_bytes(self):
        pdf_a = _pdf_with_text("First document")
        pdf_b = _pdf_with_text("Second document")
        
        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                doc1.append(doc2)
                result_buf = io.BytesIO()
                doc1.save(result_buf)
                result = result_buf.getvalue()
        
        with Document(io.BytesIO(result)) as combined:
            assert combined.page_count == 2
            assert "First document" in combined.pages[1].extract_text()
            assert "Second document" in combined.pages[2].extract_text()

    def test_append_returns_self_for_chaining(self):
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")
        pdf_c = _pdf_with_text("C")
        
        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                with Document(pdf_c) as doc3:
                    result = doc1.append(doc2)
                    assert result is doc1
                    doc1.append(doc3)
                    assert doc1.page_count == 3

    def test_append_type_error(self):
        with Document() as doc:
            with pytest.raises(TypeError, match="must be a Document instance"):
                doc.append("not a document")

    def test_append_disposed_source_raises(self):
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc1:
            doc2 = Document(pdf)
            doc2.dispose()
            
            with pytest.raises(AsposePdfException, match="disposed"):
                doc1.append(doc2)


class TestDocumentAppendPages:
    def test_append_pages_by_list(self, tmp_path: Path):
        """Append specific pages by number from another document."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 5 pages
        with Document() as doc:
            for i in range(1, 6):
                page = doc.pages.add()
                page.add_text(f"Source Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document with 2 pages
        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Target Page 1", 72, 700)
            page = doc.pages.add()
            page.add_text("Target Page 2", 72, 700)
            doc.save(target)
        
        # Append pages 1, 3, 5 from source to target
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, [1, 3, 5])
                target_doc.save(result)
        
        # Verify the result
        with Document(result) as combined:
            assert combined.page_count == 5  # 2 original + 3 appended
            assert "Target Page 1" in combined.pages[1].extract_text()
            assert "Target Page 2" in combined.pages[2].extract_text()
            assert "Source Page 1" in combined.pages[3].extract_text()
            assert "Source Page 3" in combined.pages[4].extract_text()
            assert "Source Page 5" in combined.pages[5].extract_text()

    def test_append_pages_by_string_range(self, tmp_path: Path):
        """Append pages using a range string from another document."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 10 pages
        with Document() as doc:
            for i in range(1, 11):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append pages 1-5 and page 10 from source
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "1-5,10")
                target_doc.save(result)
        
        # Verify the result - pages 1, 2, 3, 4, 5, 10 = 6 pages appended
        with Document(result) as combined:
            assert combined.page_count == 7  # 1 original + 6 appended
            assert "Target" in combined.pages[1].extract_text()
            assert "Page 1" in combined.pages[2].extract_text()
            assert "Page 2" in combined.pages[3].extract_text()
            assert "Page 5" in combined.pages[6].extract_text()
            assert "Page 10" in combined.pages[7].extract_text()

    def test_append_pages_returns_self_for_chaining(self):
        """append_pages returns self for method chaining."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")
        pdf_c = _pdf_with_text("C")
        
        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                with Document(pdf_c) as doc3:
                    result = doc1.append_pages(doc2, [1])
                    assert result is doc1
                    doc1.append_pages(doc3, [1])
                    assert doc1.page_count == 3

    def test_append_pages_with_odd(self, tmp_path: Path):
        """Append odd pages using 'odd' keyword."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 6 pages
        with Document() as doc:
            for i in range(1, 7):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append odd pages from source
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "odd")
                target_doc.save(result)
        
        # Verify the result - pages 1, 3, 5 should be appended
        with Document(result) as combined:
            assert combined.page_count == 4  # 1 original + 3 odd pages
            assert "Page 1" in combined.pages[2].extract_text()
            assert "Page 3" in combined.pages[3].extract_text()
            assert "Page 5" in combined.pages[4].extract_text()

    def test_append_pages_type_error(self):
        """append_pages raises TypeError for invalid source."""
        with Document() as doc:
            with pytest.raises(TypeError, match="other must be a Document instance"):
                doc.append_pages("not a document", [1])

    def test_append_pages_disposed_source_raises(self):
        """append_pages raises error when source document is disposed."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc1:
            doc2 = Document(pdf)
            doc2.dispose()
            
            with pytest.raises(AsposePdfException, match="disposed"):
                doc1.append_pages(doc2, [1])

    def test_append_pages_invalid_selection_raises(self):
        """append_pages raises error for out-of-range page numbers."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc1:
            # Document has only 1 page
            with pytest.raises(PdfValidationException, match="out of range"):
                doc1.append_pages(doc1, [5])

    def test_append_pages_empty_selection_raises(self):
        """append_pages raises error for empty selection."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc:
            # Empty selection raises PdfValidationException
            from aspose_pdf.exceptions import PdfValidationException
            with pytest.raises(PdfValidationException, match="Selection of pages is empty"):
                doc.append_pages(doc, [])

    def test_append_pages_with_annotations_and_resources(self, tmp_path: Path):
        """Append pages preserves annotations and resources."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with annotations
        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Source Page", 72, 700)
            page.annotations.add("Text", (10, 10, 40, 40), "Test note")
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append the page
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, [1])
                target_doc.save(result)
        
        # Verify annotations are preserved
        with Document(result) as combined:
            assert combined.page_count == 2
            annotations = combined.pages[2].annotations
            assert len(annotations) == 1
            assert "Test note" in annotations[0].contents

    def test_append_pages_with_iterable(self):
        """append_pages works with any iterable of integers."""
        pdf = _pdf_with_text("A")
        
        # Create a source with 3 pages
        source_doc = Document()
        source_doc.pages.add().add_text("A", 72, 700)
        source_doc.pages.add().add_text("B", 72, 700)
        source_doc.pages.add().add_text("C", 72, 700)
        source_bytes = source_doc.to_bytes()
        
        with Document(pdf) as target_doc:
            source_doc.dispose()
            
            # Use a generator expression as iterable
            with Document(source_bytes) as source:
                target_doc.append_pages(source, (p for p in [1, 3]))
                assert target_doc.page_count == 3

    def test_append_pages_rejects_zero_page_number(self):
        """append_pages rejects page number 0."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="Page number must be >= 1"):
                doc.append_pages(doc, [0])

    def test_append_pages_rejects_negative_page_number(self):
        """append_pages rejects negative page numbers."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="Page number must be >= 1"):
                doc.append_pages(doc, [-1])

    def test_append_pages_rejects_out_of_range_page(self):
        """append_pages rejects page numbers beyond document size."""
        pdf = _pdf_with_text("Test")
        
        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="out of range"):
                doc.append_pages(doc, [100])

    def test_append_pages_with_even(self, tmp_path: Path):
        """Append even pages using 'even' keyword."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 6 pages
        with Document() as doc:
            for i in range(1, 7):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append even pages from source
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "even")
                target_doc.save(result)
        
        # Verify the result - pages 2, 4, 6 should be appended
        with Document(result) as combined:
            assert combined.page_count == 4  # 1 original + 3 even pages
            assert "Page 2" in combined.pages[2].extract_text()
            assert "Page 4" in combined.pages[3].extract_text()
            assert "Page 6" in combined.pages[4].extract_text()

    def test_append_pages_with_descending_range(self, tmp_path: Path):
        """Append pages in descending order using range."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 5 pages
        with Document() as doc:
            for i in range(1, 6):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append pages in descending order
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "5-1")
                target_doc.save(result)
        
        # Verify the result - pages 5, 4, 3, 2, 1 should be appended
        with Document(result) as combined:
            assert combined.page_count == 6  # 1 original + 5 reversed pages
            assert "Page 5" in combined.pages[2].extract_text()
            assert "Page 3" in combined.pages[4].extract_text()
            assert "Page 1" in combined.pages[6].extract_text()

    def test_append_pages_with_last_keyword(self, tmp_path: Path):
        """Append using 'last' keyword."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 10 pages
        with Document() as doc:
            for i in range(1, 11):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append last page from source
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "last")
                target_doc.save(result)
        
        # Verify the result
        with Document(result) as combined:
            assert combined.page_count == 2  # 1 original + 1 last page
            assert "Page 10" in combined.pages[2].extract_text()

    def test_append_pages_with_open_ended_range(self, tmp_path: Path):
        """Append using open-ended range (e.g., '5-')."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 10 pages
        with Document() as doc:
            for i in range(1, 11):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append pages from 5 onwards
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, "5-")
                target_doc.save(result)
        
        # Verify the result - pages 5, 6, 7, 8, 9, 10 should be appended
        with Document(result) as combined:
            assert combined.page_count == 7  # 1 original + 6 pages (5-10)
            assert "Page 5" in combined.pages[2].extract_text()
            assert "Page 10" in combined.pages[7].extract_text()

    def test_append_pages_with_repeated_pages(self, tmp_path: Path):
        """Append pages with repetitions (e.g., [1, 1, 2])."""
        source = tmp_path / "source.pdf"
        target = tmp_path / "target.pdf"
        result = tmp_path / "result.pdf"
        
        # Create source document with 3 pages
        with Document() as doc:
            for i in range(1, 4):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)
        
        # Create target document
        with Document() as doc:
            doc.pages.add().add_text("Target", 72, 700)
            doc.save(target)
        
        # Append pages with repetitions
        with Document(target) as target_doc:
            with Document(source) as source_doc:
                target_doc.append_pages(source_doc, [1, 1, 2, 3])
                target_doc.save(result)
        
        # Verify the result - pages 1, 1, 2, 3 should be appended
        with Document(result) as combined:
            assert combined.page_count == 5  # 1 original + 4 pages (with repetition)
            assert "Page 1" in combined.pages[2].extract_text()
            assert "Page 1" in combined.pages[3].extract_text()
            assert "Page 2" in combined.pages[4].extract_text()
            assert "Page 3" in combined.pages[5].extract_text()
