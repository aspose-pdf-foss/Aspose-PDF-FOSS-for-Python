from pathlib import Path
import tempfile
import shutil
from aspose_pdf import Document, PageLabel, NumberingStyle

# Create a 6-page document
with Document() as doc:
    for i in range(6):
        doc.pages.add(size=(240, 160)).add_text(f"PAGE {i}", 15, 120)
    
    print(f'Total pages: {len(doc.pages)}')
    
    # Set label on page 2 (index 2, the third page - using 0-based index for page_labels)
    doc.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, 'page-', 1)
    
    # Check labels - PageCollection uses 1-based indexing
    print('\nLabels on original document:')
    for i in range(1, 7):
        print(f'  Page {i}: {doc.pages[i].label}')
    
    # Save and reload
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "test.pdf"
    
    doc.save(path)
    
    # Load and check split pages
    loaded = Document()
    loaded.load_from(path)
    try:
        print('\nLabels on loaded document:')
        for i in range(1, 7):
            print(f'  Page {i}: {loaded.pages[i].label}')
        
        # Now split
        parts = loaded.split_pages()
        print(f'\nSplit into {len(parts)} parts')
        for i, part in enumerate(parts):
            with part:
                print(f'  Part {i} has {part.page_count} page(s)')
                if part.page_count > 0:
                    print(f'    Page 1 label: {part.pages[1].label}')
        
        # Cleanup
        for part in parts:
            part.dispose()
    finally:
        loaded.dispose()
    
    shutil.rmtree(tmpdir)
