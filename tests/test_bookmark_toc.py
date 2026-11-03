"""Printed contents follow bookmark order, hierarchy and current page identities."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToAction,
    GoToRAction,
    NumberingStyle,
    OutlineItem,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
    XYZDestination,
)
from aspose_pdf.exceptions import PdfParseException, PdfValidationException


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _lines(document, pages):
    return [
        line
        for number in pages
        for line in document.pages[number].extract_text().splitlines()
        if line
    ]


def test_bookmarks_build_contents_for_pages_without_heading_text(tmp_path):
    with Document() as document:
        for _ in range(3):
            document.pages.add()
        later = document.outlines.add(OutlineItem("Later first", 3))
        parent = document.outlines.add(OutlineItem("First chapter", 1))
        child = parent.add(
            OutlineItem(
                "Detail", destination=GoToAction(XYZDestination(2, 20, 500, 1.5))
            )
        )
        assert document.add_table_of_contents() == []
        made = document.add_table_of_contents(source="bookmarks", leader="", indent=20)
        assert made == [1]
        assert _lines(document, made) == [
            "Contents",
            "Later first 4",
            "First chapter 2",
            "Detail 3",
        ]
        links = document.extract_links(pages=made)
        assert [link.target_page_index for link in links] == [4, 2, 3]
        words = document.pages[1].extract_words()
        assert next(
            word.rect[0] for word in words if word.text == "Detail"
        ) == pytest.approx(92)
        assert document.outlines[0] is later and parent.children[0] is child
        assert later.page_index == 4 and parent.page_index == 2
        assert child.destination == GoToAction(XYZDestination(3, 20, 500, 1.5))
        later.title = "Edited after insertion"
        output = tmp_path / "contents.pdf"
        document.save(output)
        for source in (output, _bytes(document)):
            with Document(source) as reopened:
                assert _lines(reopened, made)[1:] == [
                    "Later first 4",
                    "First chapter 2",
                    "Detail 3",
                ]
                assert [
                    link.target_page_index
                    for link in reopened.extract_links(pages=made)
                ] == [4, 2, 3]
                assert reopened.outlines[0].title == "Edited after insertion"
                assert reopened.outlines[0].page_index == 4
                assert reopened.outlines[1].children[0].destination == GoToAction(
                    XYZDestination(3, 20, 500, 1.5)
                )


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_loaded_named_destinations_follow_unsaved_changes_and_insertion(
    tmp_path, monkeypatch, cached, streaming, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as original:
        for _ in range(3):
            original.pages.add()
        original.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        original.destinations["section"] = XYZDestination(3, 10, 400)
        original.outlines.add(OutlineItem("Cover", 1))
        original.outlines.add(OutlineItem("Named section", destination="section"))
        if encrypted:
            original.encrypt("secret", "owner")
        original.save(path)
    original_bytes = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    password = "secret" if encrypted else None
    with opener(path, password=password) as document:
        if cached:
            document.outlines[1].title = "Live section"
        document.destinations["section"] = XYZDestination(2, 10, 450)

        def forbidden(*args, **kwargs):
            pytest.fail("Bookmark contents must not extract source page text")

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "extract_page_text", forbidden)
            made = document.add_table_of_contents(source="bookmarks", at=2, leader="")
        assert made == [2]
        title = "Live section" if cached else "Named section"
        assert _lines(document, made) == [
            "Contents",
            f"Cover {document.pages[1].label}",
            f"{title} {document.pages[3].label}",
        ]
        assert [
            link.target_page_index for link in document.extract_links(pages=made)
        ] == [1, 3]
        with Document(_bytes(document), password=password) as reopened:
            assert reopened.outlines[1].page_index == 3
            assert reopened.outlines[1].destination_name == "section"
            assert reopened.outlines[1].title == title
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "max_level,expected",
    [(1, ["First 2"]), (2, ["First 2", "Second 2", "Local child 2"])],
)
def test_bookmark_levels_filter_nested_entries_and_skip_nonlocal_targets(
    max_level, expected
):
    with Document() as document:
        document.pages.add()
        root = document.outlines.add(OutlineItem("First", 1))
        second = root.add(OutlineItem("Second", 1))
        second.add(OutlineItem("Third", 1))
        external = document.outlines.add(
            OutlineItem("Website", destination=URIAction("https://example.com"))
        )
        external.add(OutlineItem("Local child", 1))
        document.outlines.add(
            OutlineItem(
                "Remote", destination=GoToRAction("other.pdf", FitDestination(1))
            )
        )
        document.outlines.add(OutlineItem("Undefined", destination="missing"))
        made = document.add_table_of_contents(
            source="bookmarks", max_level=max_level, leader=""
        )
        assert _lines(document, made)[1:] == expected
        assert document.outlines[1].destination == URIAction("https://example.com")


def test_no_local_bookmarks_inserts_nothing_even_when_headings_exist():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Heading", 60, 700, tag="H1")
        document.outlines.add(
            OutlineItem("Website", destination=URIAction("https://example.com"))
        )
        before = _bytes(document)
        assert document.add_table_of_contents(source="bookmarks") == []
        assert _bytes(document) == before


def test_long_bookmark_contents_and_repeated_insertions_keep_targets_current():
    with Document() as document:
        document.pages.add()
        document.pages.add()
        for index in range(18):
            document.outlines.add(OutlineItem(f"Section {index:02d}", 1 + index % 2))
        offset = 0
        for _ in range(2):
            made = document.add_table_of_contents(
                source="bookmarks", size=(220, 200), margin=20, leader=""
            )
            assert len(made) > 1
            offset += len(made)
            assert _lines(document, made)[1:] == [
                f"Section {index:02d} {1 + index % 2 + offset}" for index in range(18)
            ]
            assert [
                link.target_page_index for link in document.extract_links(pages=made)
            ] == [1 + index % 2 + offset for index in range(18)]
            assert document.outlines[0].page_index == 1 + offset


def test_bookmark_contents_support_tagging_and_optional_title_links_and_numbers():
    with Document() as document:
        document.pages.add()
        document.outlines.add(OutlineItem("Chapter", 1))
        made = document.add_table_of_contents(
            source="bookmarks", title=None, links=False, page_numbers=False, tag=True
        )
        assert _lines(document, made) == ["Chapter"]
        assert document.extract_links(pages=made) == []
        with Document(_bytes(document)) as reopened:
            containers = [
                root
                for root in reopened.tagged_content.root_elements
                if root.structure_type == "TOC"
            ]
            assert len(containers) == 1
            assert [child.structure_type for child in containers[0].children] == [
                "TOCI"
            ]


@pytest.mark.parametrize(
    "limits",
    [
        PdfLoadLimits(max_nesting_depth=1),
        PdfLoadLimits(max_container_items=1),
        PdfLoadLimits(max_total_decoded_bytes=8),
    ],
)
def test_bookmark_tree_and_title_limits_are_checked_before_page_insertion(limits):
    with Document(limits=limits) as document:
        document.pages.add()
        document.outlines.add(OutlineItem("First", 1)).add(OutlineItem("Second", 1))
        with pytest.raises(PdfResourceLimitException):
            document.add_table_of_contents(source="bookmarks")
        assert document.page_count == 1


@pytest.mark.parametrize("source", [None, "unknown", 1, []])
def test_invalid_source_is_rejected_before_insertion(source):
    with Document() as document:
        document.pages.add()
        before = _bytes(document)
        with pytest.raises(PdfValidationException, match="source"):
            document.add_table_of_contents(source=source)
        assert _bytes(document) == before


def test_cycle_and_limits_fail_before_any_contents_page_is_inserted():
    with Document() as document:
        document.pages.add()
        root = document.outlines.add(OutlineItem("Cycle", 1))
        root.children.append(root)
        with pytest.raises(PdfParseException, match="cycle"):
            document.add_table_of_contents(source="bookmarks")
        assert document.page_count == 1
    with Document(limits=PdfLoadLimits(max_pages=1)) as document:
        document.pages.add()
        document.outlines.add(OutlineItem("Chapter", 1))
        with pytest.raises(PdfResourceLimitException, match="max_pages"):
            document.add_table_of_contents(source="bookmarks")
        assert document.page_count == 1
