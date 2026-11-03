"""One-based public page numbers compose across the split workflows."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    OutlineItem,
    Page,
    PageLabel,
    parse_page_range,
)
from aspose_pdf.exceptions import PdfValidationException
from examples import split_documents as examples


def _document(count):
    document = Document()
    for number in range(1, count + 1):
        document.pages.add().add_text(f"PAGE-{number}", 20, 50)
    return document


def _expected(count):
    numbers = list(range(1, count + 1))
    middle = count // 2
    size = max(1, (count + 2) // 3)
    return {
        "split_documents": {f"Page_{n}.pdf": [n] for n in numbers},
        "split_documents_into_two_parts": {
            "Part_1.pdf": numbers[:middle],
            "Part_2.pdf": numbers[middle:],
        },
        "split_documents_every_n_pages": {
            f"Every_3_Part_{i // 3 + 1}.pdf": numbers[i : i + 3]
            for i in range(0, count, 3)
        },
        "split_documents_by_page_ranges": {
            f"Range_{i + 1}_{start}_to_{min(start + 2, count) if i < 2 else count}.pdf": numbers[
                start - 1 : min(start + 2, count) if i < 2 else count
            ]
            for i, start in enumerate((1, 4, 7))
            if start <= count
        },
        "split_documents_first_page_and_rest": {}
        if not count
        else {
            "First_Page.pdf": [1],
            **({"Remaining_Pages.pdf": numbers[1:]} if count > 1 else {}),
        },
        "split_documents_last_page_and_rest": {}
        if not count
        else {
            "Last_Page.pdf": [count],
            **({"Previous_Pages.pdf": numbers[:-1]} if count > 1 else {}),
        },
        "split_documents_into_three_parts": {
            f"Three_Parts_{i + 1}.pdf": numbers[i * size : (i + 1) * size]
            for i in range(3)
            if i * size < count
        },
        "split_documents_custom_page_groups": {
            f"Custom_Group_{i}.pdf": [n for n in group if n <= count]
            for i, group in enumerate(([1, 2, 5], [3, 4, 6, 7]), 1)
            if any(n <= count for n in group)
        },
        "split_documents_with_stable_filenames": {
            f"Page_{n:03d}.pdf": [n] for n in numbers
        },
        "split_documents_odd_even_pages": {
            "Odd_Pages.pdf": numbers[::2],
            "Even_Pages.pdf": numbers[1::2],
        },
    }


@pytest.mark.parametrize("count", [0, 1, 2, 7, 8])
@pytest.mark.parametrize("name", sorted(_expected(0)))
def test_all_ten_split_examples(tmp_path, count, name):
    source = tmp_path / "input.pdf"
    with _document(count) as document:
        document.save(source)
    output = tmp_path / "output"
    output.mkdir()
    getattr(examples, name)(source, output)
    expected = _expected(count)[name]
    assert {file.name for file in output.iterdir()} == set(expected)
    for filename, numbers in expected.items():
        with Document(output / filename) as document:
            assert [page.number for page in document.pages] == list(
                range(1, len(numbers) + 1)
            )
            assert [page.extract_text().strip() for page in document.pages] == [
                f"PAGE-{n}" for n in numbers
            ]
    with Document(source) as document:
        assert document.page_count == count


def test_collection_numbers_and_strict_boundaries():
    with _document(3) as document:
        assert [page.index for page in document.pages] == [1, 2, 3]
        assert document.pages.item(1).number == 1
        assert document.pages.index_of(document.pages[3]) == 3
        for number in [0, -1, 4]:
            with pytest.raises(IndexError):
                document.pages[number]
        for number in [True, 1.0, slice(1, 3)]:
            with pytest.raises(TypeError):
                document.pages[number]
        for number in [0, -1, 5]:
            with pytest.raises(IndexError):
                document.pages.insert(number)
        document.pages.insert(1).add_text("FIRST", 20, 50)
        document.pages.insert(5).add_text("LAST", 20, 50)
        assert document.pages[1].extract_text() == "FIRST"
        assert document.pages[5].extract_text() == "LAST"
    with _document(1) as other, _document(1) as document:
        assert document.pages.index_of(other.pages[1]) == 0
        document.pages.Delete()
        assert document.page_count == 0


def test_batch_deletion_validates_before_editing():
    with _document(5) as document:
        for selection in [[1, 6], [2, 0], [1, True]]:
            with pytest.raises(PdfValidationException):
                document.pages.delete(iter(selection))
            assert document.page_count == 5
        document.pages.delete(number for number in [4, 2, 4])
        assert [page.extract_text() for page in document.pages] == [
            "PAGE-1",
            "PAGE-3",
            "PAGE-5",
        ]
        document.pages.delete([])
        assert document.page_count == 3


def test_selections_results_and_labels_keep_their_contracts():
    with _document(3) as document:
        assert Page(document, 1).number == 1
        assert parse_page_range("3,1-2", 3) == (3, 1, 2)
        assert parse_page_range("0-1", 3, one_based=False) == (1, 2)
        assert document.extract_text(pages=[3, 1]) == "PAGE-3\nPAGE-1"
        assert [word.page_index for word in document.extract_words(pages=[3])] == [3]
        with document.extract_pages("3,1") as subset:
            assert [page.extract_text() for page in subset.pages] == [
                "PAGE-3",
                "PAGE-1",
            ]
        for selection in [[0], [-1], slice(1, 3)]:
            with pytest.raises(PdfValidationException):
                document.extract_pages(selection)
        document.page_labels[0] = PageLabel(prefix="C")
        assert document.pages[1].label == "C1"
        assert list(document.page_labels) == [0]
        field = document.form.add_text_field("name", 2, (10, 10, 70, 30))
        assert field.page_index == 2
        document.outlines.add(OutlineItem("Third", 3, destination=FitDestination(3)))
        buffer = io.BytesIO()
        document.save(buffer)
        with Document(buffer.getvalue()) as saved:
            assert saved.outlines[0].page_index == 3
            assert saved.outlines[0].destination.page == 3


@pytest.mark.parametrize("version,source_page", [(1, 0), (2, 1)])
def test_annotation_exchange_reads_both_schema_versions(version, source_page):
    with _document(2) as document:
        document.pages[1].annotations.add_text((10, 10, 30, 30), "note")
        payload = json.loads(document.export_annotations_json())
        assert payload["schema_version"] == 2
        assert payload["annotations"][0]["page_index"] == 1
        payload["schema_version"] = version
        payload["annotations"][0]["page_index"] = source_page
        assert (
            document.import_annotations_json(
                json.dumps(payload).encode(), page_map={1: 2}
            )
            == 1
        )
        assert len(document.pages[2].annotations) == 1


@pytest.mark.parametrize("version,source_page", [(1, 0), (2, 1)])
def test_bookmark_exchange_reads_both_schema_versions(version, source_page):
    with _document(2) as document:
        document.outlines.add(OutlineItem("First", 1, destination=FitDestination(1)))
        payload = json.loads(document.export_bookmarks_json())
        assert payload["schema_version"] == 2
        assert payload["bookmarks"][0]["target"]["page_index"] == 1
        payload["schema_version"] = version
        payload["bookmarks"][0]["target"]["page_index"] = source_page
        assert (
            document.import_bookmarks_json(
                json.dumps(payload).encode(), replace=True, page_map={1: 2}
            )
            == 1
        )
        assert document.outlines[0].page_index == 2
        assert document.outlines[0].destination.page == 2


def test_rendered_page_manifest_uses_schema_two_and_public_page_numbers():
    import zipfile

    with _document(2) as document:
        data = document.export_pages_zip(pages=[2, 1], include_manifest=True, dpi=12)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 2
        assert [page["page_index"] for page in manifest["pages"]] == [2, 1]


def test_xfdf_retains_standard_zero_based_pages_and_accepts_public_page_maps():
    import xml.etree.ElementTree as ET

    with _document(2) as document:
        document.pages[1].annotations.add_text((10, 10, 30, 30), "note")
        data = document.export_annotations_xfdf(pages=[1])
        root = ET.fromstring(data)
        note = root.find("{http://ns.adobe.com/xfdf/}annots")[0]
        assert note.attrib["page"] == "0"
        assert document.import_annotations_xfdf(data, page_map={1: 2}) == 1
        assert document.pages[2].annotations.to_list()[0]["page_index"] == 2


def test_failing_deletion_generator_leaves_every_page_intact():
    def broken():
        yield 1
        raise RuntimeError("selection failed")

    with _document(2) as document:
        with pytest.raises(RuntimeError, match="selection failed"):
            document.pages.delete(broken())
        assert document.extract_text() == "PAGE-1\nPAGE-2"
