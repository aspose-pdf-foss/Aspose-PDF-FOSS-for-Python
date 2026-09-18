"""Page labels (ISO 32000-1 12.4.2): read, written, and kept through page edits.

``/PageLabels`` was neither read nor written: a book whose front matter a
viewer shows as i, ii, iii came back with no way to ask for it, and there was
no way to give a document labels. ``Page.label`` and ``Document.page_labels``
now do both.

Every expectation here is a reference's. Reading a malformed tree follows the
majority of pdfium, qpdf, pdf.js and MuPDF (each case names who agrees), and the
specification where they split evenly -- which they do on letters, where pdfium
and pdf.js repeat (``AA, BB``) as the specification says and qpdf and MuPDF
count (``AA, AB``). Inserting and deleting pages moves the ranges as MuPDF does
(``new_page``/``delete_page``); pages assembled from several documents keep
their labels as in qpdf's ``--pages``. Each table below was produced by those
tools on the same inputs.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, NumberingStyle, PageLabel
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

ROMAN = NumberingStyle.NUMERALS_ROMAN_LOWERCASE


def _reloaded(document: Document, **kwargs) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()), **kwargs)


def _labels(document: Document) -> list[str | None]:
    return [page.label for page in document.pages]


def _blank(count: int) -> Document:
    document = Document()
    for _ in range(count):
        document.pages.add()
    return document


def _label(style: str | None = None, prefix: str | None = None, start: object = None) -> PdfDictionary:
    entry: dict = {}
    if style is not None:
        entry[PdfName("S")] = PdfName(style)
    if prefix is not None:
        entry[PdfName("P")] = PdfString(prefix)
    if start is not None:
        entry[PdfName("St")] = PdfNumber(start)
    return PdfDictionary(entry)


def _with_tree(tree: PdfDictionary | None, count: int = 6) -> Document:
    """A saved and reopened document whose catalog carries *tree* as written."""
    document = _blank(count)
    engine = document._engine_pdf
    engine._ensure_cos()
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    if tree is not None:
        catalog.mapping[PdfName("PageLabels")] = engine._cos_doc.register_object(tree)
    return _reloaded(document)


def _with_text(tree: PdfDictionary, old: bytes, new: bytes) -> Document:
    """As :func:`_with_tree`, with *old* in the saved bytes spelled *new*.

    The writer spells a whole-number real as an integer, so a ``3.0`` has to
    be put into the file by hand.
    """
    buffer = io.BytesIO()
    _with_tree(tree).save(buffer)
    data = buffer.getvalue()
    assert data.count(old) == 1 and len(old) == len(new)
    return Document(io.BytesIO(data.replace(old, new)))


def _nums(*items) -> PdfDictionary:
    return PdfDictionary({PdfName("Nums"): PdfArray([PdfNumber(i) if isinstance(i, (int, float)) else i for i in items])})


# --- reading -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tree", "expected"),
    [
        # pdfium and pdf.js: no tree, no labels (qpdf says 1, 2 ...; MuPDF "").
        (None, [None] * 6),
        # pdfium, qpdf: pages before the first range count from 1.
        (_nums(), ["1", "2", "3", "4", "5", "6"]),
        (_nums(2, _label("r")), ["1", "2", "i", "ii", "iii", "iv"]),
        # All four: a negative number is written in decimal.
        (_nums(0, _label("D", start=-2)), ["-2", "-1", "0", "1", "2", "3"]),
        # pdfium, MuPDF: no Roman numeral, and (pdfium) no letters, below 1.
        (_nums(0, _label("r", start=0)), ["", "i", "ii", "iii", "iv", "v"]),
        (_nums(0, _label("A", start=0)), ["", "A", "B", "C", "D", "E"]),
        # All four: thousands are repeated M.
        (_nums(0, _label("R", start=3998)), ["MMMCMXCVIII", "MMMCMXCIX", "MMMM", "MMMMI", "MMMMII", "MMMMIII"]),
        # pdfium, pdf.js and ISO 32000-1 12.4.2: letters repeat.
        (_nums(0, _label("a", start=25)), ["y", "z", "aa", "bb", "cc", "dd"]),
        (_nums(0, _label("A", start=51)), ["YY", "ZZ", "AAA", "BBB", "CCC", "DDD"]),
        # All four: no style, the prefix alone.
        (_nums(0, _label(prefix="Cover")), ["Cover"] * 6),
        # qpdf, MuPDF, pdf.js: keys are sorted.
        (_nums(3, _label("D", start=10), 0, _label("r")), ["i", "ii", "iii", "10", "11", "12"]),
        # qpdf: a start that is not a whole number is ignored ...
        (_nums(0, _label("D", start=2.7)), ["1", "2", "3", "4", "5", "6"]),
        # qpdf and pdf.js skip a fractional key.
        (_nums(0, _label("r"), 2.5, _label("D")), ["i", "ii", "iii", "iv", "v", "vi"]),
        # pdfium: no Roman numeral below 1 (MuPDF writes cmxcix for -1).
        (_nums(0, _label("r", start=-1)), ["", "", "i", "ii", "iii", "iv"]),
        # pdfium, MuPDF: an unknown style leaves the prefix.
        (_nums(0, _label("X", prefix="p-")), ["p-"] * 6),
        # pdfium, qpdf, pdf.js: a repeated key's later value wins.
        (_nums(0, _label("r"), 0, _label("D")), ["1", "2", "3", "4", "5", "6"]),
        # pdfium, qpdf, MuPDF: a negative key covers the pages after it.
        (_nums(-1, _label("r"), 2, _label("D")), ["ii", "iii", "1", "2", "3", "4"]),
    ],
    ids=[
        "no-tree", "empty", "first-key-2", "negative-start", "roman-0", "letters-0",
        "roman-thousands", "letters-lower", "letters-upper", "prefix-only", "unsorted",
        "fractional-start", "fractional-key",
        "roman-negative", "unknown-style", "repeated-key", "negative-key",
    ],
)
def test_reading_a_tree(tree, expected):
    assert _labels(_with_tree(tree)) == expected


def test_whole_numbers_written_as_reals():
    # pdfium, MuPDF and pdf.js take /St 3.0 as 3; all four take a key of 2.0 as 2.
    start = _with_text(_nums(0, _label("D", start=3.5)), b"/St 3.5", b"/St 3.0")
    assert _labels(start) == ["3", "4", "5", "6", "7", "8"]
    key = _with_text(_nums(0, _label("r"), 2.5, _label("D")), b"2.5 <<", b"2.0 <<")
    assert _labels(key) == ["i", "ii", "1", "2", "3", "4"]


def test_an_unknown_style_reads_as_none():
    document = _with_tree(_nums(0, _label("X", prefix="p-")))
    assert document.page_labels[0] == PageLabel(NumberingStyle.NONE, "p-")


def test_a_start_below_one_survives_a_page_edit():
    # Rewriting the tree after an edit keeps a /St the rules would not allow.
    document = _with_tree(_nums(0, _label("r", start=0)))
    document.pages.add()
    assert _labels(_reloaded(document)) == ["", "i", "ii", "iii", "iv", "v", "vi"]


def test_a_tree_with_kids_and_a_text_prefix():
    leaf_a = _nums(0, _label("r"))
    leaf_b = _nums(3, _label("D", prefix="Chapter é-"))
    root = PdfDictionary({PdfName("Kids"): PdfArray([leaf_a, leaf_b])})
    assert _labels(_with_tree(root)) == ["i", "ii", "iii", "Chapter é-1", "Chapter é-2", "Chapter é-3"]


def test_a_tree_that_loops_is_read_once():
    document = _blank(3)
    engine = document._engine_pdf
    engine._ensure_cos()
    root = _nums(0, _label("r"))
    reference = engine._cos_doc.register_object(root)
    root.mapping[PdfName("Kids")] = PdfArray([reference])  # the root is its own kid
    engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")]).mapping[PdfName("PageLabels")] = reference
    assert _labels(_reloaded(document)) == ["i", "ii", "iii"]


# --- writing -------------------------------------------------------------------------------


def test_ranges_are_written_and_read_back():
    document = _blank(12)
    document.page_labels[0] = PageLabel(NumberingStyle.LETTERS_UPPERCASE, start=25)
    document.page_labels[4] = PageLabel(NumberingStyle.NUMERALS_ROMAN_UPPERCASE, "Vol ", 3998)
    document.page_labels[7] = PageLabel(NumberingStyle.NONE, "Cover")
    document.page_labels[8] = PageLabel(NumberingStyle.LETTERS_LOWERCASE, "Ünï-", 51)
    # pdfium and pdf.js read the saved file exactly so.
    expected = [
        "Y", "Z", "AA", "BB", "Vol MMMCMXCVIII", "Vol MMMCMXCIX", "Vol MMMM", "Cover",
        "Ünï-yy", "Ünï-zz", "Ünï-aaa", "Ünï-bbb",
    ]
    assert _labels(document) == expected
    reopened = _reloaded(document)
    assert _labels(reopened) == expected
    assert reopened.page_labels[4] == PageLabel(NumberingStyle.NUMERALS_ROMAN_UPPERCASE, "Vol ", 3998)
    assert list(reopened.page_labels) == [0, 4, 7, 8]


def test_a_range_without_a_style_writes_none():
    document = _blank(2)
    document.page_labels[0] = PageLabel(NumberingStyle.NONE, "Cover")
    buffer = io.BytesIO()
    document.save(buffer)
    assert b"/S" not in buffer.getvalue().split(b"/Nums", 1)[1].split(b"]", 1)[0]
    assert _labels(Document(io.BytesIO(buffer.getvalue()))) == ["Cover", "Cover"]


def test_a_document_without_labels_has_none():
    document = _blank(2)
    assert _labels(document) == [None, None]
    assert len(document.page_labels) == 0 and document.page_labels.label(1) is None
    assert b"/PageLabels" not in _reloaded(document)._engine_pdf.to_bytes()


def test_the_tree_always_starts_at_page_zero():
    # ISO 32000-1 table 28: "The tree shall include a value for page index 0."
    document = _blank(6)
    document.page_labels[3] = PageLabel(prefix="A-")
    assert dict(document.page_labels) == {0: PageLabel(), 3: PageLabel(prefix="A-")}
    assert _labels(document) == ["1", "2", "3", "A-1", "A-2", "A-3"]
    del document.page_labels[0]  # the pages before A- are still numbered
    assert 0 in document.page_labels and _labels(document)[0] == "1"
    del document.page_labels[3]
    del document.page_labels[0]
    assert _labels(document) == [None] * 6  # the last range gone, no labels


def test_a_range_before_page_zero_is_kept_when_another_is_set():
    # A negative key covers page 0 already (pdfium, qpdf, MuPDF), so no range
    # is added there.
    document = _with_tree(_nums(-1, _label("r"), 2, _label("D")))
    document.page_labels[4] = PageLabel(prefix="A-")
    assert _labels(document) == ["ii", "iii", "1", "2", "A-1", "A-2"]


def test_clear_removes_the_labels():
    document = _blank(3)
    document.page_labels[0] = PageLabel(ROMAN)
    document.page_labels.clear()
    assert _labels(_reloaded(document)) == [None] * 3


def test_a_label_range_is_checked():
    document = _blank(3)
    with pytest.raises(ValueError, match="1 or more"):
        PageLabel(start=0)
    with pytest.raises(TypeError):
        PageLabel(style="D")
    with pytest.raises(TypeError):
        PageLabel(start=True)
    with pytest.raises(IndexError):
        document.page_labels[3] = PageLabel()
    with pytest.raises(IndexError):
        document.page_labels[-1] = PageLabel()
    with pytest.raises(TypeError):
        document.page_labels[True] = PageLabel()
    with pytest.raises(TypeError):
        document.page_labels[0] = "i"
    with pytest.raises(KeyError):
        del document.page_labels[1]
    assert len(document.page_labels) == 0


def test_a_label_names_its_pages():
    assert PageLabel(ROMAN, "p. ", 4).text() == "p. iv"
    assert PageLabel(NumberingStyle.LETTERS_UPPERCASE).text(26) == "AA"
    # A range read from a file may break the rules a new one keeps.
    read = _with_tree(_nums(0, _label("r", start=0))).page_labels[0]
    assert read.start == 0 and read.text(1) == "i"


# --- inserting and deleting pages: MuPDF ---------------------------------------------------------


def _book() -> Document:
    document = _blank(8)
    document.page_labels[0] = PageLabel(ROMAN)
    document.page_labels[3] = PageLabel()
    document.page_labels[4] = PageLabel(prefix="A-")
    document.page_labels[6] = PageLabel(prefix="B-", start=5)
    return _reloaded(document)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda d: d.pages.insert(0), ["1", "i", "ii", "iii", "1", "A-1", "A-2", "B-5", "B-6"]),
        (lambda d: d.pages.delete(0), ["i", "ii", "1", "A-1", "A-2", "B-5", "B-6"]),
        (lambda d: d.pages.delete(3), ["i", "ii", "iii", "A-1", "A-2", "B-5", "B-6"]),
        (lambda d: d.pages.delete(7), ["i", "ii", "iii", "1", "A-1", "A-2", "B-5"]),
        (lambda d: d.pages.add(), ["i", "ii", "iii", "1", "A-1", "A-2", "B-5", "B-6", "B-7"]),
        (lambda d: d.pages.insert(6), ["i", "ii", "iii", "1", "A-1", "A-2", "A-3", "B-5", "B-6"]),
        (lambda d: d.pages.insert(5), ["i", "ii", "iii", "1", "A-1", "A-2", "A-3", "B-5", "B-6"]),
        (lambda d: [d.pages.delete(3) for _ in range(3)], ["i", "ii", "iii", "B-5", "B-6"]),
    ],
    ids=["insert-0", "delete-0", "delete-one-page-range", "delete-last", "add", "insert-at-range-start", "insert-inside", "delete-three"],
)
def test_page_edits_move_the_ranges(edit, expected):
    document = _book()
    edit(document)
    assert _labels(document) == expected
    assert _labels(_reloaded(document)) == expected
    assert 0 in document.page_labels  # the tree still starts at page 0


def test_a_copied_page_is_an_inserted_one():
    document = _book()
    document._engine_pdf.copy_page(1, 5)
    assert _labels(document) == ["i", "ii", "iii", "1", "A-1", "A-2", "A-3", "B-5", "B-6"]


def test_deleting_every_page_leaves_no_labels():
    document = _book()
    for _ in range(8):
        document.pages.delete(0)
    document.pages.add()
    assert _labels(document) == [None]


def test_inserting_into_a_document_without_labels_adds_none():
    # MuPDF labels an unlabelled document on inserting at page 0 -- 1, 1, 2,
    # 3 in every reader; it stays unlabelled here.
    document = _blank(3)
    document.pages.insert(0)
    document.pages.delete(2)
    assert _labels(_reloaded(document)) == [None] * 3


# --- pages from other documents: qpdf -------------------------------------------------------------


def _ten() -> Document:
    document = _blank(10)
    document.page_labels[0] = PageLabel(ROMAN)
    document.page_labels[3] = PageLabel()
    document.page_labels[7] = PageLabel(prefix="A-")
    return _reloaded(document)


def _assembled(*parts) -> list[str | None]:
    target = SimplePdf()
    for document, pages in parts:
        target.append(document._engine_pdf, pages)
    return [target.page_label(index) for index in range(len(target.pages))]


def test_selected_pages_keep_their_labels():
    assert _assembled((_ten(), [0, 4, 5, 8])) == ["i", "2", "3", "A-2"]
    # Pages before the first range keep the numbers they were shown with.
    assert _assembled((_with_tree(_nums(2, _label("r"))), [1, 2])) == ["2", "i"]
    # Consecutive numbers with different prefixes are two ranges, not one.
    prefixed = _blank(2)
    prefixed.page_labels[0] = PageLabel(prefix="A-")
    plain = _blank(2)
    plain.page_labels[0] = PageLabel(start=3)
    assert _assembled((prefixed, None), (plain, None)) == ["A-1", "A-2", "3", "4"]
    assert _assembled((_ten(), [8, 9]), (_ten(), [0, 1])) == ["A-2", "A-3", "i", "ii"]


def test_pages_from_a_document_without_labels_get_empty_ones():
    target = SimplePdf()
    target.append(_blank(3)._engine_pdf)
    target.append(_ten()._engine_pdf, [3, 4])
    # One range for the three empty labels, as qpdf writes: none shows a number.
    assert [key for key, _ in target.page_label_ranges()] == [0, 3]
    assert _assembled((_blank(3), None), (_ten(), [3, 4])) == ["", "", "", "1", "2"]
    assert _assembled((_ten(), [0, 1]), (_blank(3), None)) == ["i", "ii", "", "", ""]
    assert _assembled((_blank(3), None), (_blank(3), None)) == [None] * 6


@pytest.mark.parametrize("first_labelled", [True, False])
def test_merging_documents_keeps_both_sides_labels(first_labelled):
    labelled = ["i", "ii", "iii", "1", "2", "3", "4", "A-1", "A-2", "A-3"]
    if first_labelled:
        document = _ten()
        document.merge(_blank(3))
        expected = [*labelled, "", "", ""]
    else:
        document = _blank(3)
        document.merge(_ten())
        expected = ["", "", "", *labelled]
    assert _labels(_reloaded(document)) == expected


# --- saving ------------------------------------------------------------------------------------


def test_an_encrypted_document_keeps_its_prefixes():
    document = _blank(3)
    document.page_labels[0] = PageLabel(prefix="Secret-")
    document.encrypt("user", "owner")
    assert _labels(_reloaded(document, password="user")) == ["Secret-1", "Secret-2", "Secret-3"]


def test_an_incremental_save_appends_the_labels():
    original = _ten()
    buffer = io.BytesIO()
    original.save(buffer)
    source = buffer.getvalue()
    document = Document(io.BytesIO(source))
    document.page_labels[7] = PageLabel(NumberingStyle.LETTERS_UPPERCASE, "App ")
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    assert buffer.getvalue().startswith(source)
    assert _labels(Document(io.BytesIO(buffer.getvalue())))[7:] == ["App A", "App B", "App C"]
    # The tree is rewritten in its own object; the catalog is not touched.
    assert b"/Catalog" not in buffer.getvalue()[len(source):]
