import io
import tempfile
import os
from aspose_pdf import Document

def test_edit_save_reopen_split():
    """Test that edited content is preserved through save/reopen/split."""
    doc = Document()
    page = doc.pages.add(size=(612, 792))
    page.add_text("ORIGINAL CONTENT", 15, 700)
    
    buf = io.BytesIO()
    doc.save(buf)
    original_bytes = buf.getvalue()
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(original_bytes)
        original_path = f.name
    
    try:
        with Document.open_streaming(original_path) as streamed:
            # Page index is 1-based in the API
            streamed.pages[1].add_text("ANNOTATION TEXT", 15, 650)
            
            engine = streamed._engine_pdf
            print(f"page_contents after add_text: {len(engine.page_contents) if engine.page_contents else 'empty'}")
            
            buf2 = io.BytesIO()
            streamed.save(buf2)
            edited_bytes = buf2.getvalue()
            
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f2:
                f2.write(edited_bytes)
                edited_path = f2.name
    finally:
        os.unlink(original_path)
    
    try:
        with Document.open_streaming(edited_path) as reopens:
            engine = reopens._engine_pdf
            print(f"page_contents after reopen: {engine.page_contents}")
            
            text = reopens.pages[1].extract_text()
            print(f"Text after reopen: {repr(text)}")
            
            parts = reopens.split_pages()
            part_text = parts[0].pages[1].extract_text()
            print(f"Part text: {repr(part_text)}")
            
            assert "ANNOTATION TEXT" in part_text, f"ANNOTATION TEXT should be in part: {part_text}"
            print("TEST PASSED!")
            
            parts[0].dispose()
    finally:
        os.unlink(edited_path)
