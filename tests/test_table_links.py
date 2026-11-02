"""Clickable table cells follow the final layout and retain their targets."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Cell,
    Document,
    FitDestination,
    GoToAction,
    GoToRAction,
    NamedAction,
    Table,
    URIAction,
    XYZDestination,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.exceptions import PdfValidationException


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("tag", [False, True])
@pytest.mark.parametrize(
    "target",
    [
        URIAction("https://example.test/catalog?q=1#product"),
        URIAction("mailto:sales@example.test"),
        URIAction("https://example.test/資料"),
        FitDestination(1),
        XYZDestination(1, top=100),
        GoToAction(FitDestination(1)),
        "appendix",
        GoToRAction("other.pdf", FitDestination(7)),
        NamedAction("NextPage"),
    ],
)
def test_targets_survive_save(tmp_path, on_disk, tag, target):
    with Document() as document:
        page = document.pages.add(size=(300, 200))
        document.pages.add(size=(300, 200))
        document.destinations["appendix"] = FitDestination(1)
        table = Table(column_widths=[120], row_height=40)
        table.add_row([Cell("Details", link=target)])
        page.add_table(table, 20, 160, tag=tag)
        expected = page.extract_links()
        assert len(expected) == 1
        assert expected[0].rect == (20, 120, 140, 160)
        assert expected[0].issues == ()
        if isinstance(target, URIAction):
            assert expected[0].uri == target.uri
        elif isinstance(target, GoToRAction):
            assert expected[0].file == "other.pdf"
            assert expected[0].destination == FitDestination(7)
        elif isinstance(target, NamedAction):
            assert expected[0].named_action == "NextPage"
        else:
            assert expected[0].target_page_index == 1
        if on_disk:
            source = tmp_path / "linked-table.pdf"
            document.save(source)
        else:
            source = _saved(document)
        with Document(source) as reopened:
            assert reopened.pages[0].extract_links() == expected
            assert "Details" in reopened.pages[0].extract_text()
            assert reopened.pages[1].extract_links() == []


def test_spanning_wrapped_and_empty_cells_use_the_entire_row_height():
    with Document() as document:
        page = document.pages.add(size=(300, 200))
        table = Table(column_widths=[70, 70, 60], border_width=0, padding=8)
        table.add_row(
            [
                Cell(
                    "Product details over several wrapped lines",
                    column_span=2,
                    link=URIAction("https://example.test/product"),
                ),
                Cell("", link=URIAction("https://example.test/empty")),
            ]
        )
        placement = page.add_table(table, 20, 160, bottom_margin=0)
        first, second = page.extract_links()
        bottom = placement["bottom"]
        assert first.rect == (20, bottom, 160, 160)
        assert second.rect == (160, bottom, 220, 160)
        assert bottom < 120


@pytest.mark.parametrize("repeat", [False, True])
def test_links_follow_body_rows_and_repeated_headers_on_later_pages(repeat):
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        table = Table(column_widths=[100], row_height=30, repeat_header=repeat)
        table.add_header_row(
            [Cell("Catalog", link=URIAction("https://example.test/header"))]
        )
        for index in range(4):
            table.add_row(
                [Cell(str(index), link=URIAction(f"https://example.test/{index}"))]
            )
        result = page.add_table(table, 20, 100, bottom_margin=10)
        assert result["pages"] == [0, 1]
        with Document(_saved(document)) as reopened:
            records = reopened.extract_links()
            expected = ["header", "0", "1", *(["header"] if repeat else []), "2", "3"]
            assert [record.uri.rsplit("/", 1)[-1] for record in records] == expected
            assert [r.rect for r in reopened.pages[0].extract_links()] == [
                (20, 70, 120, 100),
                (20, 40, 120, 70),
                (20, 10, 120, 40),
            ]
            assert all(r.page_index == 1 for r in reopened.pages[1].extract_links())
            assert reopened.pages[1].extract_links()[0].rect == (20, 70, 120, 100)


def test_links_do_not_change_cell_rendering_and_plain_cells_add_no_annotations():
    rasters = []
    for target in (None, URIAction("https://example.test")):
        with Document() as document:
            page = document.pages.add(size=(160, 120))
            table = Table(column_widths=[100], row_height=40)
            table.add_row([Cell("Product", link=target)])
            page.add_table(table, 20, 90, bottom_margin=0)
            assert len(page.annotations) == (0 if target is None else 1)
            raster = page.render(dpi=72, antialias=False)
            rasters.append(
                [raster.get_pixel(x, y) for y in range(120) for x in range(160)]
            )
    assert rasters[0] == rasters[1]


def test_named_cell_link_follows_a_repointed_destination():
    with Document() as document:
        page = document.pages.add()
        document.pages.add()
        document.destinations["appendix"] = FitDestination(1)
        table = Table(column_widths=[120])
        table.add_row([Cell("Appendix", link="appendix")])
        page.add_table(table, 20, 700)
        document.destinations["appendix"] = XYZDestination(0, top=500)
        with Document(_saved(document)) as reopened:
            (link,) = reopened.extract_links()
            assert link.destination_name == "appendix"
            assert link.destination == XYZDestination(0, top=500)


@pytest.mark.parametrize("transformed", [False, True])
def test_streaming_keeps_other_pages_lazy_and_preserves_shared_annotations(
    tmp_path, monkeypatch, transformed
):
    path = tmp_path / "shared.pdf"
    with Document() as source:
        first = source.pages.add()
        source.pages.add()
        if transformed:
            source._engine_pdf._append_content_to_page(0, b"2 0 0 2 0 0 cm\n")
        first.add_link((10, 10, 30, 30), URIAction("https://example.test/existing"))
        pdf = source._engine_pdf
        shared = pdf._cos_doc.register_object(
            pdf._get_page_dict(0).mapping[PdfName("Annots")]
        )
        for index in (0, 1):
            pdf._get_page_dict(index).mapping[PdfName("Annots")] = shared
        source.save(path)
    with Document.open_streaming(path) as document:
        pdf = document._engine_pdf
        original = pdf.get_page_content
        reads = []

        def read(index):
            reads.append(index)
            assert index == 0
            return original(index)

        table = Table(column_widths=[120], background_color="#00f")
        table.add_row([Cell("New link", link=URIAction("https://example.test/new"))])
        with monkeypatch.context() as patch:
            patch.setattr(pdf, "get_page_content", read)
            document.pages[0].add_table(table, 20, 700)
        assert reads == [0] and pdf._lazy and pdf.page_contents == []
        with Document(_saved(document)) as reopened:
            assert [r.uri for r in reopened.pages[0].extract_links()] == [
                "https://example.test/existing",
                "https://example.test/new",
            ]
            assert [r.uri for r in reopened.pages[1].extract_links()] == [
                "https://example.test/existing",
            ]
            raster = reopened.pages[0].render(dpi=72, antialias=False)
            assert raster.get_pixel(130, 100) == (0, 0, 255)


def test_streaming_linked_table_can_create_continuation_pages(tmp_path):
    path = tmp_path / "single-page.pdf"
    with Document() as source:
        source.pages.add(size=(160, 120))
        source.save(path)
    with Document.open_streaming(path) as document:
        table = Table(column_widths=[100], row_height=30)
        table.add_header_row(
            [Cell("Header", link=URIAction("https://example.test/header"))]
        )
        for index in range(4):
            table.add_row(
                [Cell(str(index), link=URIAction(f"https://example.test/{index}"))]
            )
        placement = document.pages[0].add_table(table, 20, 100, bottom_margin=10)
        assert placement["pages"] == [0, 1]
        assert document._engine_pdf._lazy
        with Document(_saved(document)) as reopened:
            assert all("Header" in page.extract_text() for page in reopened.pages)
            assert [
                record.uri.rsplit("/", 1)[-1] for record in reopened.extract_links()
            ] == [
                "header",
                "0",
                "1",
                "header",
                "2",
                "3",
            ]


def test_cell_target_can_be_changed_or_removed_before_placement():
    cell = Cell("Details", link=URIAction("https://example.test/old"))
    cell.link = URIAction("https://example.test/new")
    table = Table(column_widths=[120], row_height=30)
    table.add_row([cell])
    with Document() as document:
        page = document.pages.add()
        page.add_table(table, 20, 700)
        cell.link = None
        page.add_table(table, 20, 600)
        (link,) = page.extract_links()
        assert link.uri == "https://example.test/new"
        assert link.rect == (20, 670, 140, 700)


@pytest.mark.parametrize("value", [False, 1, 1.5, b"url", {}, object()])
def test_invalid_link_types_are_rejected(value):
    with pytest.raises(TypeError, match="link"):
        Cell("Details", link=value)


@pytest.mark.parametrize(
    "target",
    [
        object(),
        "",
        "глава",
        FitDestination(-1),
        FitDestination(2),
        FitDestination(True),
        GoToAction(FitDestination(5)),
        XYZDestination(0, top=float("nan")),
        XYZDestination(0, zoom=float("inf")),
        URIAction("https://example.test/\ud800"),
    ],
)
def test_edited_invalid_targets_fail_before_any_table_content_is_written(target):
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_text("Original", 20, 100)
        before = _saved(document)
        table = Table(column_widths=[100], row_height=30)
        for index in range(4):
            row = table.add_row([str(index)])
        row.cells[0].link = target
        with pytest.raises((TypeError, PdfValidationException)):
            page.add_table(table, 20, 100, bottom_margin=10)
        assert _saved(document) == before


def test_tagged_links_belong_to_their_cells_and_use_unique_parent_tree_keys():
    with Document() as document:
        for _ in range(2):
            document.pages.add(size=(300, 200))
        for index in (0, 0, 1):
            table = Table(column_widths=[120], row_height=40)
            table.add_row([Cell("Details", link=URIAction("https://example.test"))])
            document.pages[index].add_table(table, 20, 160)
        with Document(_saved(document)) as reopened:
            pdf = reopened._engine_pdf
            catalog = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
            root = pdf._resolve(catalog.mapping[PdfName("StructTreeRoot")])
            tree = pdf._resolve(root.mapping[PdfName("ParentTree")])
            nums = pdf._resolve(tree.mapping[PdfName("Nums")])
            entries = {
                int(nums.items[i].value): pdf._resolve(nums.items[i + 1])
                for i in range(0, len(nums.items), 2)
            }
            assert len(entries) == len(nums.items) // 2 == 5
            for page in reopened.pages:
                page_dict = pdf._get_page_dict(page.index)
                assert page_dict.mapping[PdfName("Tabs")] == PdfName("S")
                page_parents = entries[
                    int(page_dict.mapping[PdfName("StructParents")].value)
                ]
                assert isinstance(page_parents, PdfArray)
                for ref in pdf._annotation_refs(page.index):
                    annotation = pdf._resolve(ref)
                    assert pdf._resolve(annotation.mapping[PdfName("P")]) is page_dict
                    parent = entries[
                        int(annotation.mapping[PdfName("StructParent")].value)
                    ]
                    assert parent.mapping[PdfName("S")] == PdfName("Link")
                    cell = pdf._resolve(parent.mapping[PdfName("P")])
                    assert cell.mapping[PdfName("S")] == PdfName("TD")
                    kids = parent.mapping[PdfName("K")]
                    assert isinstance(kids, PdfArray)
                    assert isinstance(kids.items[0], PdfNumber)
                    assert (
                        pdf._resolve(page_parents.items[int(kids.items[0].value)])
                        is parent
                    )
                    objr = pdf._resolve(kids.items[1])
                    assert isinstance(objr, PdfDictionary)
                    assert objr.mapping[PdfName("Type")] == PdfName("OBJR")
                    assert pdf._resolve(objr.mapping[PdfName("Obj")]) is annotation


def test_linked_table_still_passes_builtin_ua_checks():
    with Document() as document:
        page = document.pages.add()
        table = Table(column_widths=[120])
        table.add_header_row([Cell("Catalog", link=URIAction("https://example.test"))])
        table.add_row(["Product"])
        page.add_table(table, 20, 700)
        document.info = {"Title": "Catalog"}
        assert document.convert_to_pdfua() == []
        report = document.validate_pdfua()
        assert report.is_valid, report.errors
        assert not any("no /StructParent" in warning for warning in report.warnings)


def test_link_parent_keys_preserve_indirect_existing_keys_without_next_key():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Original", 20, 740, tag="P")
        pdf = document._engine_pdf
        catalog = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
        root = pdf._resolve(catalog.mapping[PdfName("StructTreeRoot")])
        tree = pdf._resolve(root.mapping[PdfName("ParentTree")])
        nums = pdf._resolve(tree.mapping[PdfName("Nums")])
        nums.items[0] = pdf._cos_doc.register_object(PdfNumber(0))
        nums.items.extend(
            [
                pdf._cos_doc.register_object(PdfNumber(7)),
                nums.items[1],
            ]
        )
        root.mapping.pop(PdfName("ParentTreeNextKey"), None)
        table = Table(column_widths=[120])
        table.add_row([Cell("Details", link=URIAction("https://example.test"))])
        page.add_table(table, 20, 700)
        keys = [int(pdf._resolve(item).value) for item in nums.items[0::2]]
        assert keys == [0, 7, 8]
        annotation = pdf._resolve(pdf._annotation_refs(0)[0])
        assert annotation.mapping[PdfName("StructParent")].value == 8
