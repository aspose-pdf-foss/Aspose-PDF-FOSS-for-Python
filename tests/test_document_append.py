import io
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import AsposePdfException


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
