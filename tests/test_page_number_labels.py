"""Printed page numbers can follow the logical labels stored in a PDF."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PageNumberStamp, PdfFileStamp, TextStamp
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.lowcode import ByteArrayDataSource, Stamper, StampOptions
from aspose_pdf.page_labels import NumberingStyle, PageLabel


def _document(count=1):
    document = Document()
    for _ in range(count):
        document.pages.add(size=(240, 160))
    return document


def _saved(document, tmp_path=None):
    if tmp_path is not None:
        path = tmp_path / "stamped.pdf"
        document.save(path)
        return path
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


@pytest.mark.parametrize("on_disk", [False, True])
def test_mixed_label_ranges_are_printed_and_preserved_after_saving(tmp_path, on_disk):
    with _document(7) as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        document.page_labels[2] = PageLabel(start=8)
        document.page_labels[4] = PageLabel(
            NumberingStyle.LETTERS_UPPERCASE, "App-", 26
        )
        document.page_labels[6] = PageLabel(NumberingStyle.NONE, "Index")
        labels = ["i", "ii", "8", "9", "App-Z", "App-AA", "Index"]
        original = dict(document.page_labels)
        document.add_stamp(
            PageNumberStamp("{label} / {page} of {total}", starting_number=10)
        )
        expected = [
            f"{label} / {index + 10} of 7" for index, label in enumerate(labels)
        ]
        assert _texts(document) == expected
        assert dict(document.page_labels) == original
        with Document(_saved(document, tmp_path if on_disk else None)) as reopened:
            assert _texts(reopened) == expected
            assert [page.label for page in reopened.pages] == labels
            assert dict(reopened.page_labels) == original


def test_absent_labels_use_page_positions_independently_of_starting_number():
    with _document(3) as document:
        document.add_stamp(PageNumberStamp("{label}:{page}", starting_number=7))
        assert _texts(document) == ["1:7", "2:8", "3:9"]
        assert len(document.page_labels) == 0
        with Document(_saved(document)) as reopened:
            assert _texts(reopened) == ["1:7", "2:8", "3:9"]
            assert len(reopened.page_labels) == 0


def test_prefix_only_empty_labels_remain_empty():
    with _document(2) as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE)
        document.add_stamp(PageNumberStamp("[{label}] - {page}"))
        assert _texts(document) == ["[] - 1", "[] - 2"]
        assert [page.label for page in document.pages] == ["", ""]


def test_selections_use_destination_labels_and_keep_order_and_duplicates():
    with _document(4) as document:
        document.page_labels[0] = PageLabel(prefix="A-", start=10)
        document.add_stamp(PageNumberStamp("{label}/{page}"), pages=[3, 1, 3])
        document.pages[2].add_stamp(PageNumberStamp("{label}/{total}"))
        assert _texts(document) == ["", "A-11/2", "A-12/4", "A-13/4\nA-13/4"]


def test_reordered_extracted_pages_use_preserved_labels_with_new_positions():
    with _document(4) as source:
        source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_UPPERCASE)
        with source.extract_pages([3, 0]) as selected:
            selected.add_stamp(PageNumberStamp("{label} ({page}/{total})"))
            assert _texts(selected) == ["IV (1/2)", "I (2/2)"]
        assert _texts(source) == [""] * 4


def test_reusing_a_stamp_reads_current_labels_from_each_document():
    stamp = PageNumberStamp("{label}-{page}", starting_number=8)
    with _document(2) as first, _document(1) as second:
        first.page_labels[0] = PageLabel(prefix="A")
        first.pages[0].add_stamp(stamp)
        first.page_labels[0] = PageLabel(prefix="B")
        first.pages[1].add_stamp(stamp)
        second.add_stamp(stamp)
        assert _texts(first) == ["A1-8", "B2-9"]
        assert _texts(second) == ["1-8"]
        assert stamp.value == "{label}-{page}" and stamp.starting_number == 8


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{label}", "iv"),
        ("{label!s}/{label!r}", "iv/'iv'"),
        ("[{label:*>6}]", "[****iv]"),
        ("{page:03d}/{total:02d}: {label}", "007/01: iv"),
        ("{{label}} {label} {{page}}", "{label} iv {page}"),
        ("{label:{total}}", "iv"),
    ],
)
def test_label_string_formatting_and_existing_numeric_formats(template, expected):
    with _document() as document:
        document.page_labels[0] = PageLabel(
            NumberingStyle.NUMERALS_ROMAN_LOWERCASE, start=4
        )
        document.add_stamp(PageNumberStamp(template, starting_number=7))
        assert _texts(document) == [expected]


def test_braces_parentheses_and_backslashes_in_labels_are_literal_data():
    label = r"A({page})\{label}"
    with _document() as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE, label)
        document.add_stamp(PageNumberStamp("Label: {label}; page={page}"))
        with Document(_saved(document)) as reopened:
            assert _texts(reopened) == [f"Label: {label}; page=1"]


@pytest.mark.parametrize(
    "stamp,expected",
    [
        (TextStamp("{label}"), "{label}"),
        (PageNumberStamp("{page:02d}/{total}"), "01/1"),
        (PageNumberStamp("{{label}} {page}"), "{label} 1"),
    ],
)
def test_unneeded_label_metadata_is_never_read(stamp, expected, monkeypatch):
    with _document() as document:

        def no_labels(*args, **kwargs):
            raise AssertionError("This template must not read page labels")

        monkeypatch.setattr(document._engine_pdf, "page_label", no_labels)
        document.add_stamp(stamp)
        assert _texts(document) == [expected]


def test_repeated_label_fields_read_metadata_once_per_destination_page(monkeypatch):
    with _document(2) as document:
        reads = []
        original = document._engine_pdf.page_label

        def read(index):
            reads.append(index)
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "page_label", read)
        document.add_stamp(PageNumberStamp("{label}:{label!r}:{label:>2}"))
        assert reads == [0, 1]


@pytest.mark.parametrize(
    "template", ["{chapter}", "{label:03d}", "{label", "{}", "{0}", "{label:{unknown}}"]
)
def test_invalid_formats_raise_before_changing_page_content(template):
    with _document() as document:
        document.page_labels[0] = PageLabel(prefix="A-")
        before = document.pages[0].content
        with pytest.raises(PdfValidationException, match=r"page.*total.*label"):
            document.add_stamp(PageNumberStamp(template))
        assert document.pages[0].content == before
        assert document.pages[0].label == "A-1"


@pytest.mark.parametrize("embedded", [False, True])
def test_resolved_labels_use_text_stamp_encoding_measurement_and_placement(embedded):
    font = None
    label = "Appendix-III"
    if embedded:
        from aspose_pdf.engine.std_font_data import load_substitute_sfnt

        font = load_substitute_sfnt("symbol")
        assert font is not None
        label = "Ω"
    options = dict(
        font=font,
        font_size=12,
        opacity=0.6,
        rotate=15,
        color="#215bb5",
        vertical_alignment="Bottom",
        y_indent=8,
    )
    with _document() as actual, _document() as expected:
        for document in (actual, expected):
            document.pages[0].rotation = 90
            document.pages[0].crop_box = (10, 10, 230, 150)
        actual.page_labels[0] = PageLabel(NumberingStyle.NONE, label)
        actual.add_stamp(PageNumberStamp("{label}", **options))
        expected.add_stamp(TextStamp(label, **options))
        with Document(_saved(actual)) as saved:
            assert _texts(saved) == [label]
            assert (
                saved.pages[0].render(antialias=False).pixels
                == expected.pages[0].render(antialias=False).pixels
            )


def test_unencodable_label_is_refused_without_replacing_characters():
    with _document() as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE, "Раздел")
        before = document.pages[0].content
        with pytest.raises(PdfValidationException, match="cannot write"):
            document.add_stamp(PageNumberStamp("{label}"))
        assert document.pages[0].content == before
        assert document.pages[0].label == "Раздел"


def test_labels_remain_pagination_artifacts_and_can_be_excluded_from_extraction():
    with _document() as document:
        document.pages[0].add_text("Body", 20, 80)
        document.page_labels[0] = PageLabel(prefix="A-")
        document.add_stamp(PageNumberStamp("Page {label}", vertical_alignment="Bottom"))
        assert "Page A-1" in document.pages[0].extract_text()
        assert document.pages[0].extract_text(skip_artifacts=True).strip() == "Body"
        assert b"/Subtype /Footer" in document.pages[0].content


def test_facade_and_lowcode_stamper_accept_label_templates():
    with _document(2) as document, PdfFileStamp() as facade:
        document.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        facade.bind_pdf(document)
        assert facade.add_page_number("Page {label}", page_numbers=[2]) is True
        assert _texts(document) == ["", "Page ii"]
    with _document(2) as document:
        document.page_labels[0] = PageLabel(prefix="A-")
        options = StampOptions(PageNumberStamp("{label}"), pages=[1]).add_input(
            ByteArrayDataSource(bytearray(_saved(document)))
        )
        results = Stamper().process(options)
        with Document(results[0].to_array()) as reopened:
            assert _texts(reopened) == ["", "A-2"]


def test_labels_are_read_from_streaming_encrypted_pdfs(tmp_path):
    with _document(2) as document:
        document.page_labels[0] = PageLabel(prefix="A-", start=5)
        document.encrypt("user", "owner")
        source = _saved(document, tmp_path)
    original = source.read_bytes()
    with Document.open_streaming(source, password="user") as opened:
        opened.pages[1].add_stamp(PageNumberStamp("{label}/{total}"))
        with io.BytesIO() as output:
            opened.save(output)
            with Document(output.getvalue(), password="user") as reopened:
                assert _texts(reopened) == ["", "A-6/2"]
                assert [page.label for page in reopened.pages] == ["A-5", "A-6"]
    assert source.read_bytes() == original
