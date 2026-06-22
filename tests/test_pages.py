import pytest
from aspose_pdf.pages import Page
from aspose_pdf.document import Document


@pytest.fixture
def document():
    """Create a Document for testing."""
    return Document()


@pytest.fixture
def page_collection(document):
    """Create a PageCollection with three pages."""
    pc = document.pages
    pc.add()
    pc.add()
    pc.add()
    return pc


def test_page_collection_is_iterable(page_collection):
    """PageCollection should be iterable."""
    pages = list(page_collection)
    assert len(pages) == 3
    assert all(isinstance(p, Page) for p in pages)


def test_page_collection_supports_indexing(page_collection):
    """PageCollection should support indexing."""
    first = page_collection[0]
    assert isinstance(first, Page)
    assert first.index == 0

    last = page_collection[-1]
    assert isinstance(last, Page)
    assert last.index == 2

    with pytest.raises(IndexError):
        _ = page_collection[10]

    with pytest.raises(IndexError):
        page_collection.item(10)


def test_insert_at_0(document):
    """Insert a page at the beginning of a non-empty collection."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(0)
    assert len(pages) == original_len + 1


def test_insert_middle(document):
    """Insert a page into the middle of a collection."""
    pages = document.pages
    pages.add()
    pages.add()
    pages.add()
    original_len = len(pages)
    pages.insert(1)
    assert len(pages) == original_len + 1


def test_insert_end(document):
    """Insert a page at the end of the collection."""
    pages = document.pages
    pages.add()
    pages.add()
    original_len = len(pages)
    pages.insert(len(pages))
    assert len(pages) == original_len + 1


def test_insert_index_out_of_range(document):
    """Inserting beyond the current length should clamp to end (no error)."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(100)
    assert len(pages) == original_len + 1


def test_insert_negative_index(document):
    """Negative indices are clamped to 0 for insertion."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(-1)
    assert len(pages) == original_len + 1


def test_delete_decreases_page_count(document):
    """Delete a page and verify count decreases."""
    pages = document.pages
    pages.add()
    pages.add()
    pages.add()
    assert len(pages) == 3
    pages.delete(1)
    assert len(pages) == 2


def test_delete_last_page(document):
    """Delete the last page."""
    pages = document.pages
    pages.add()
    pages.add()
    assert len(pages) == 2
    pages.delete(1)
    assert len(pages) == 1


def test_delete_index_out_of_range(document):
    """Deleting with out-of-range index should raise."""
    pages = document.pages
    pages.add()
    with pytest.raises(IndexError):
        pages.delete(5)


def test_add_increases_page_count(document):
    """Append a new page and verify count increases."""
    pages = document.pages
    initial_len = len(pages)
    pages.add()
    assert len(pages) == initial_len + 1


def test_adding_to_disposed_document_raises(document):
    """Adding a page to a disposed document should raise."""
    pages = document.pages
    document.dispose()
    with pytest.raises(Exception):
        pages.add()


def test_add_to_disposed_document_raises(document):
    """Adding a page to a disposed document should raise."""
    document.dispose()
    with pytest.raises(Exception):
        document.pages.add()


def test_insert_index_out_of_range_raises(document):
    """Inserting at out-of-range index should clamp, not raise."""
    pages = document.pages
    original = len(pages)
    pages.insert(len(pages) + 1)
    assert len(pages) == original + 1


def test_insert_negative_index_raises(document):
    """Inserting at negative index should clamp to 0, not raise."""
    pages = document.pages
    original = len(pages)
    pages.insert(-1)
    assert len(pages) == original + 1


# ------- Document-based tests -------


def test_add_increases_page_count_doc(document):
    """Append a new page and verify count increases."""
    pages = document.pages
    initial = len(pages)
    pages.add()
    assert len(pages) == initial + 1


def test_delete_decreases_page_count_doc(document):
    """Delete a page and verify count decreases."""
    pages = document.pages
    if len(pages) < 2:
        pages.add()
        pages.add()
    initial = len(pages)
    pages.delete(0)
    assert len(pages) == initial - 1


def test_delete_last_page_doc(document):
    """Delete the last page and verify operation succeeds."""
    pages = document.pages
    if len(pages) == 0:
        pages.add()
    initial = len(pages)
    pages.delete(initial - 1)
    assert len(pages) == initial - 1


def test_delete_index_out_of_range_raises(document):
    """Deleting with an out-of-range index should raise."""
    pages = document.pages
    count = len(pages)
    with pytest.raises(IndexError):
        pages.delete(count)


def test_page_collection_iterable(document):
    """PageCollection should be iterable."""
    pages = document.pages
    if len(pages) == 0:
        pages.add()
    iterated = list(pages)
    assert len(iterated) == len(pages)


def test_page_collection_indexing(document):
    """PageCollection should support indexing."""
    pages = document.pages
    while len(pages) < 2:
        pages.add()
    first_page = pages[0]
    assert first_page is not None
    with pytest.raises(IndexError):
        _ = pages[len(pages)]


def test_insert_at_0_doc(document):
    """Insert a page at index 0."""
    pages = document.pages
    while len(pages) < 2:
        pages.add()
    original_len = len(pages)
    pages.insert(0)
    assert len(pages) == original_len + 1


def test_insert_middle_doc(document):
    """Insert a page in the middle of the collection."""
    pages = document.pages
    while len(pages) < 3:
        pages.add()
    original_len = len(pages)
    middle_index = original_len // 2
    pages.insert(middle_index)
    assert len(pages) == original_len + 1


def test_insert_end_doc(document):
    """Insert a page at the end of the collection."""
    pages = document.pages
    original_len = len(pages)
    pages.insert(original_len)
    assert len(pages) == original_len + 1
