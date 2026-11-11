"""Test skip_artifacts parameter for table and block extraction."""

from aspose_pdf import Document, PageSize


def test_extract_tables_with_skip_artifacts():
    """Test that skip_artifacts filters artifact content in table extraction."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Header", 72, 760, artifact="Pagination")
        page.add_text("Body text", 72, 700)
        page.add_text("Footer", 72, 50, artifact=True)

        # Without skip_artifacts: artifact text is included
        tables_all = doc.extract_tables(skip_artifacts=False)
        assert len(tables_all) == 0  # No tables in this document

        # With skip_artifacts: artifact text is filtered out
        tables_no_artifacts = doc.extract_tables(skip_artifacts=True)
        assert len(tables_no_artifacts) == 0


def test_extract_blocks_with_skip_artifacts():
    """Test that skip_artifacts filters artifact content in block extraction."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Header - Artifact", 72, 760, artifact="Pagination")
        page.add_text("Body text", 72, 700)
        page.add_text("Footer - Artifact", 72, 50, artifact=True)

        # Without skip_artifacts: artifact blocks are included
        blocks_all = doc.extract_blocks(skip_artifacts=False)
        assert len(blocks_all) == 3
        texts_all = [b.text for b in blocks_all]
        assert "Header - Artifact" in texts_all
        assert "Body text" in texts_all
        assert "Footer - Artifact" in texts_all

        # With skip_artifacts: artifact blocks are excluded
        blocks_no_artifacts = doc.extract_blocks(skip_artifacts=True)
        assert len(blocks_no_artifacts) == 1
        assert blocks_no_artifacts[0].text == "Body text"


def test_page_extract_blocks_with_skip_artifacts():
    """Test that skip_artifacts works on Page methods too."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Header", 72, 760, artifact="Pagination")
        page.add_text("Body text", 72, 700)

        # Without skip_artifacts
        blocks_all = doc.pages[1].extract_blocks(skip_artifacts=False)
        assert len(blocks_all) == 2

        # With skip_artifacts
        blocks_no_artifacts = doc.pages[1].extract_blocks(skip_artifacts=True)
        assert len(blocks_no_artifacts) == 1
        assert blocks_no_artifacts[0].text == "Body text"


def test_export_blocks_json_with_skip_artifacts():
    """Test that skip_artifacts works with export_blocks_json."""

    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Header", 72, 760, artifact="Pagination")
        page.add_text("Body text", 72, 700)

        # Export without skip_artifacts
        json_all = doc.export_blocks_json(skip_artifacts=False)
        assert b"Header" in json_all
        assert b"Body text" in json_all

        # Export with skip_artifacts
        json_no_artifacts = doc.export_blocks_json(skip_artifacts=True)
        assert b"Header" not in json_no_artifacts
        assert b"Body text" in json_no_artifacts


def test_export_tables_zip_with_skip_artifacts():
    """Test that skip_artifacts works with export_tables_zip."""

    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Header", 72, 760, artifact="Pagination")
        page.add_text("Body text", 72, 700)

        # Export without skip_artifacts
        zip_all = doc.export_tables_zip(skip_artifacts=False)
        assert len(zip_all) > 0

        # Export with skip_artifacts
        zip_no_artifacts = doc.export_tables_zip(skip_artifacts=True)
        assert len(zip_no_artifacts) > 0
