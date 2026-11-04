"""Make existing phrases navigate to local PDF pages and named destinations."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    AutoLinkResult,
    Document,
    FitBDestination,
    FitBHDestination,
    FitBVDestination,
    FitDestination,
    FitHDestination,
    FitRDestination,
    FitVDestination,
    PdfLoadLimits,
    URIAction,
    XYZDestination,
)
from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("named", [False, True])
def test_internal_links_survive_save_and_keep_text_and_rendering(
    tmp_path, on_disk, named
):
    with Document() as document:
        page = document.pages.add(size=(300, 200))
        page.add_text("See chapter 2. See chapter 2.", 20, 150)
        document.pages.add(size=(300, 200))
        destination = XYZDestination(2, left=20, top=150, zoom=1.5)
        target = destination
        if named:
            document.destinations["chapter-2"] = destination
            target = "chapter-2"
        content, text = page.content, page.extract_text()
        pixels = page.render(dpi=24, antialias=False).pixels

        result = document.link_text("See chapter 2", target=target)
        assert (result.linked_count, result.skipped_count) == (2, 0)
        for annotation in result.annotations:
            assert annotation.get_property("Dest") == target
            assert annotation.get_property("A") is None
            assert annotation.get_property("Border") == [0, 0, 0]
        result.annotations[0].contents = "Chapter navigation"
        assert page.annotations[0].contents == "Chapter navigation"
        assert (page.content, page.extract_text()) == (content, text)
        assert page.render(dpi=24, antialias=False).pixels == pixels

        if on_disk:
            source = tmp_path / "internal-links.pdf"
            document.save(source)
        else:
            source = _bytes(document)
        with Document(source) as reopened:
            assert reopened.pages[1].annotations[0].get_property("Dest") == target
            assert reopened.pages[1].annotations[0].contents == "Chapter navigation"
            links = reopened.extract_links()
            assert len(links) == 2
            assert all(
                link.destination == destination and not link.issues for link in links
            )
            assert all(
                link.destination_name == (target if named else None) for link in links
            )
            assert reopened.pages[1].extract_text() == text
            assert reopened.pages[1].render(dpi=24, antialias=False).pixels == pixels


@pytest.mark.parametrize(
    "target",
    [
        FitDestination(2),
        FitBDestination(2),
        FitHDestination(2, 700),
        FitBHDestination(2, 700),
        FitVDestination(2, 20),
        FitBVDestination(2, 20),
        FitRDestination(2, 20, 30, 200, 300),
        XYZDestination(2),
    ],
)
def test_page_api_preserves_every_supported_destination_view(target):
    with Document() as document:
        for _ in range(2):
            document.pages.add().add_text("Details", 20, 700)
        result = document.pages[1].link_text("Details", target=target)
        assert result.linked_count == 1
        assert len(document.pages[2].annotations) == 0
        with Document(_bytes(document)) as reopened:
            assert reopened.pages[1].annotations[0].get_property("Dest") == target


def test_named_links_follow_reassigned_destinations_and_skip_existing_links():
    with Document() as document:
        for _ in range(3):
            document.pages.add().add_text("Details", 20, 700)
        document.destinations["détails"] = FitDestination(2)
        result = document.link_text(
            "Details",
            target="détails",
            pages=(page for page in [2, 1, 2, 3]),
            max_count=3,
        )
        assert (result.linked_count, result.existing_count, result.matched_count) == (
            2,
            1,
            3,
        )
        assert result.existing_fragments[0].page_index == 2
        document.destinations["détails"] = XYZDestination(3, top=650)
        links = document.extract_links()
        assert [link.destination.page for link in links] == [3, 3]
        assert all(
            annotation.get_property("Dest") == "détails"
            for annotation in result.annotations
        )
        again = document.link_text("Details", "https://example.org", pages=[1, 2])
        assert (again.linked_count, again.existing_count) == (0, 2)
        with Document(_bytes(document)) as reopened:
            assert [link.destination.page for link in reopened.extract_links()] == [
                3,
                3,
            ]


@pytest.mark.parametrize(
    "options",
    [
        {"target": "missing"},
        {"target": ""},
        {"target": "\ud800"},
        {"target": "раздел"},
        {"target": b"chapter"},
        {"target": 2},
        {"target": URIAction("https://example.org")},
        {"uri": "https://example.org", "target": FitDestination(2)},
        {"target": FitDestination(0)},
        {"target": FitDestination(3)},
        {"target": FitDestination(True)},
        {"target": FitDestination(1.5)},
        {"target": XYZDestination(2, top=float("nan"))},
        {"target": FitHDestination(2, top=float("inf"))},
        {"target": XYZDestination(2, left=True)},
        {"target": FitRDestination(2, 20, None, 200, 300)},
    ],
)
def test_invalid_targets_fail_before_content_reads_and_edits(monkeypatch, options):
    with Document() as document:
        document.pages.add().add_text("Details", 20, 700)
        document.pages.add()
        before = _bytes(document)

        def unexpected_read(*args, **kwargs):
            raise AssertionError("Invalid targets must not read page content")

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "get_page_content", unexpected_read)
            with pytest.raises(PdfValidationException):
                document.link_text("Details", **options)
        assert _bytes(document) == before


def test_missing_named_destination_is_rejected_even_without_matches():
    with Document() as document:
        document.pages.add()
        with pytest.raises(PdfValidationException, match="named destination"):
            document.link_text("absent", target="missing", pages=[])
        assert (
            document.link_text("absent", target=FitDestination(1), pages=[])
            == AutoLinkResult()
        )


def test_named_target_size_obeys_resource_limits():
    with Document(limits=PdfLoadLimits(max_object_bytes=64)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.link_text("Details", target="x" * 65, pages=[])


def test_encrypted_streaming_does_not_decode_destination_or_unselected_pages(
    tmp_path, monkeypatch
):
    path = tmp_path / "encrypted.pdf"
    with Document() as original:
        for _ in range(3):
            original.pages.add().add_text("Details", 20, 700)
        original.destinations["chapter"] = XYZDestination(3, top=700)
        original.encrypt(
            user_password="secret", owner_password="owner", algorithm="AES-256"
        )
        original.save(path)
    with Document.open_streaming(path, password="secret") as document:
        engine = document._engine_pdf
        read = engine.get_page_content

        def selected_only(index):
            assert index == 1
            return read(index)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", selected_only)
            assert (
                document.link_text("Details", target="chapter", pages=[2]).linked_count
                == 1
            )
            assert engine.page_contents == []
        document.save(path, incremental=True, overwrite=True)
    with Document(path, password="secret") as reopened:
        links = reopened.extract_links()
        assert len(links) == 1
        assert links[0].page_index == 2 and links[0].destination.page == 3
        assert links[0].destination_name == "chapter" and not links[0].issues
