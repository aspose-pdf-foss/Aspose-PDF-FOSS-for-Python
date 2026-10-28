"""Fixed-size splitting preserves complete pages and independent document state."""

from __future__ import annotations

import gc
import io
import weakref
from contextlib import ExitStack, closing, contextmanager

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    NumberingStyle,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    XYZDestination,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    SplitOptions,
    Splitter,
    StreamDataSource,
)
from aspose_pdf.outlines import OutlineItem
from aspose_pdf.pages import Page

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def _document(count=6, prefix="PAGE"):
    document = Document()
    for index in range(count):
        document.pages.add(size=(240, 160)).add_text(f"{prefix} {index}", 15, 120)
    document.info["Title"] = "Batch"
    document.info["Author"] = "Test author"
    return document


@pytest.fixture
def source():
    with _document() as document:
        yield document


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


@contextmanager
def _parts(document, size=2, *, method="split_every"):
    with ExitStack() as stack:
        parts = list(getattr(document, method)(size))
        for part in parts:
            stack.enter_context(part)
        yield parts


@pytest.mark.parametrize(
    "size,counts",
    [(1, [1] * 6), (2, [2, 2, 2]), (4, [4, 2]), (6, [6]), (20, [6]), (10**100, [6])],
)
@pytest.mark.parametrize("method", ["split_every", "iter_split_every"])
def test_fixed_size_parts_keep_order_tail_metadata_and_independence(
    source, size, counts, tmp_path, method
):
    before = _bytes(source)
    model = source._engine_pdf._outlines_data
    with _parts(source, size, method=method) as parts:
        assert [part.page_count for part in parts] == counts
        assert [text for part in parts for text in _texts(part)] == _texts(source)
        assert all(part is not source for part in parts)
        for index, part in enumerate(parts):
            assert (
                part.info["Title"] == "Batch" and part.info["Author"] == "Test author"
            )
            assert part.load_limits is source.load_limits and part.file_name is None
            assert part._engine_pdf._load_budget is not source._engine_pdf._load_budget
            path = tmp_path / f"part-{index}.pdf"
            part.save(path)
            for data in (_bytes(part), path):
                with Document(data) as reopened:
                    assert _texts(reopened) == _texts(part)
                    assert reopened.validate()
        parts[0].info["Title"] = "Independent"
        parts[0].pages[0].add_text("PART ONLY", 15, 70)
        assert "PART ONLY" not in source.extract_text()
        assert all("PART ONLY" not in part.extract_text() for part in parts[1:])
    assert source._engine_pdf._outlines_data is model
    assert _bytes(source) == before


def test_empty_and_blank_documents_do_not_require_content_boundaries():
    with Document() as empty:
        assert empty.split_every(3) == []
    with Document() as blank:
        for _ in range(5):
            blank.pages.add(size=(100, 100))
        with _parts(blank, 2) as parts:
            assert [part.page_count for part in parts] == [2, 2, 1]
            assert [text for part in parts for text in _texts(part)] == [""] * 5


@pytest.mark.parametrize(
    "size,error",
    [
        (0, ValueError),
        (-2, ValueError),
        (True, TypeError),
        (False, TypeError),
        (None, TypeError),
        (1.0, TypeError),
        ("2", TypeError),
        ([], TypeError),
    ],
)
def test_invalid_sizes_fail_before_import_and_are_shared_with_lowcode(
    source, size, error, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid sizes must be refused before page import")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", forbidden)
    with pytest.raises(error, match="pages_per_part"):
        source.split_every(size)
    if size is not None:
        with pytest.raises(error, match="pages_per_part"):
            SplitOptions(pages_per_part=size)
    with Document() as empty:
        with pytest.raises(error, match="pages_per_part"):
            empty.split_every(size)


def test_disposed_documents_are_rejected(source):
    source.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        source.split_every(2)


def test_no_text_extraction_or_rendering_is_needed(source, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fixed-size splitting must not scan text or render pages")

    monkeypatch.setattr(source._engine_pdf, "extract_page_text", forbidden)
    monkeypatch.setattr(source._engine_pdf, "extract_text", forbidden)
    monkeypatch.setattr(Page, "render", forbidden)
    with _parts(source) as parts:
        assert [part.page_count for part in parts] == [2, 2, 2]


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("method", ["split_every", "iter_split_every"])
def test_streaming_parts_keep_resources_and_remain_usable_after_source_closes(
    source, tmp_path, encrypted, monkeypatch, method
):
    source.version = "2.0"
    source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
    source.page_labels[2] = PageLabel(prefix="A-")
    page = source.pages[2]
    page.rotation = 90
    page.crop_box = (5, 5, 230, 155)
    page.add_image(_PNG, 50, 50, width=20, height=20)
    page.annotations.add_square((20, 30, 40, 50), interior_color=(0, 0, 1))
    page.add_link((10, 10, 30, 25), FitDestination(3))
    page.add_link((40, 10, 60, 25), FitDestination(0))
    source.form.add_text_field("Editable", 3, (10, 60, 100, 80), value="Keep me")
    layer = source.layers.add("Hidden note", visible=False)
    with source.pages[3].layer(layer):
        source.pages[3].draw_rectangle(
            0, 0, 20, 20, fill_color="#000", stroke_color=None
        )
    source.add_attachment("batch.txt", b"Document attachment")
    if encrypted:
        source.encrypt("secret", "owner")
    path = tmp_path / "source.pdf"
    source.save(path)
    original = path.read_bytes()
    reference = source.pages[2].render(antialias=False)
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as loaded:
        engine = loaded._engine_pdf
        reads = []
        original_read = engine.get_page_content
        original_extract = engine.extract_pages

        def read(index):
            reads.append(index)
            return original_read(index)

        def extract(selection):
            indexes = tuple(selection)
            previous = len(reads)
            result = original_extract(indexes)
            assert set(reads[previous:]) <= set(indexes)
            return result

        monkeypatch.setattr(engine, "get_page_content", read)
        monkeypatch.setattr(engine, "extract_pages", extract)
        parts = list(getattr(loaded, method)(2))
        assert set(reads) == set(range(6))
        assert engine.page_contents == [] and engine._extracted_text is None
        assert loaded.is_encrypted == encrypted
    with ExitStack() as stack:
        for part in parts:
            stack.enter_context(part)
        middle = parts[1]
        assert middle.pages[0].rotation == 90
        assert middle.pages[0].crop_box == (5, 5, 230, 155)
        rendered = middle.pages[0].render(antialias=False)
        assert (rendered.width, rendered.height, rendered.pixels) == (
            reference.width,
            reference.height,
            reference.pixels,
        )
        assert [page.label for page in middle.pages] == ["A-1", "A-2"]
        assert middle.form["Editable"].widgets[0].page_index == 1
        assert middle.form["Editable"].value == "Keep me"
        assert not middle.layers["Hidden note"].visible
        assert middle.attachments == {} and middle.version == "2.0"
        links = [note for note in middle.pages[0].annotations if note.subtype == "Link"]
        assert links[0].get_property("Dest") == FitDestination(1)
        assert links[1].get_property("Dest") == [None, "Fit"]
        assert all(not part.is_encrypted for part in parts)
        middle.form["Editable"].value = "Edited"
        with Document(_bytes(middle)) as reopened:
            assert reopened.form["Editable"].value == "Edited"
    assert path.read_bytes() == original


def test_live_bookmarks_keep_views_and_promote_children_of_excluded_parents(source):
    source.outlines.add(OutlineItem("Outside", 0)).add(
        OutlineItem("Inside", destination=XYZDestination(3, 10, 120, 1.5), is_bold=True)
    )
    model = source._engine_pdf._outlines_data
    with _parts(source) as parts:
        assert parts[1].info["Title"] == "Batch"
        assert [(item.title, item.page_index) for item in parts[1].outlines] == [
            ("Inside", 1)
        ]
        assert parts[1].outlines[0].destination == XYZDestination(1, 10, 120, 1.5)
        assert parts[1].outlines[0].is_bold
        with Document(_bytes(parts[1])) as reopened:
            assert reopened.outlines[0].destination == XYZDestination(1, 10, 120, 1.5)
    assert source._engine_pdf._outlines_data is model
    assert source.outlines[0].children[0].destination == XYZDestination(3, 10, 120, 1.5)


@pytest.mark.parametrize("error", [PdfValidationException, KeyboardInterrupt])
def test_failed_later_import_disposes_parts_and_restores_source(
    source, monkeypatch, error
):
    source.outlines.add(OutlineItem("Live edit", 1))
    engine = source._engine_pdf
    original = engine.extract_pages
    model = engine._outlines_data
    created = []

    def extract(selection):
        if created:
            raise error("Interrupted import")
        result = original(selection)
        created.append(result)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(engine, "extract_pages", extract)
        with pytest.raises(error, match="Interrupted import"):
            source.split_every(2)
    assert engine._outlines_data is model and source.outlines[0].title == "Live edit"
    with pytest.raises(AsposePdfException, match="disposed"):
        created[0]._ensure_not_disposed()
    with _parts(source) as parts:
        assert [part.page_count for part in parts] == [2, 2, 2]


@pytest.mark.parametrize(
    "size,limits,context",
    [
        (1, PdfLoadLimits(max_container_items=2), "split parts"),
        (4, PdfLoadLimits(max_container_items=2), "split selection"),
        (2, PdfLoadLimits(max_pages=5), "PDF pages"),
    ],
)
def test_planning_limits_fail_before_any_import(
    source, monkeypatch, size, limits, context
):
    source._engine_pdf._load_budget.limits = limits

    def forbidden(*args, **kwargs):
        pytest.fail("Planning limits must fail before importing any part")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", forbidden)
    with pytest.raises(PdfResourceLimitException, match=context):
        source.split_every(size)


def test_malformed_live_outline_tree_is_rejected_before_import(source, monkeypatch):
    root = source.outlines.add(OutlineItem("Loop", 0))
    root.children.append(root)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid bookmark trees must be rejected before importing")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", forbidden)
    with pytest.raises(PdfParseException, match="cycle"):
        source.split_every(2)


def test_imported_result_limits_dispose_the_rejected_part(monkeypatch):
    with Document(limits=PdfLoadLimits(max_object_bytes=16)) as source:
        source.pages.add(((0, 0, 100, 100), b"BT (A long page content stream) Tj ET"))
        created = []
        original = source._engine_pdf.extract_pages

        def extract(selection):
            result = original(selection)
            created.append(result)
            return result

        monkeypatch.setattr(source._engine_pdf, "extract_pages", extract)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            source.split_every(2)
        assert created
        with pytest.raises(AsposePdfException, match="disposed"):
            created[0]._ensure_not_disposed()


def test_lowcode_groups_each_input_independently_and_reuses_options():
    with _document(5, "FIRST") as first, _document(3, "SECOND") as second:
        limits = PdfLoadLimits()
        options = SplitOptions(limits, pages_per_part=2)
        assert options.limits is limits
        options.add_input(ByteArrayDataSource(_bytes(first)))
        options.add_input(ByteArrayDataSource(_bytes(second)))
    expected = [
        ["FIRST 0", "FIRST 1"],
        ["FIRST 2", "FIRST 3"],
        ["FIRST 4"],
        ["SECOND 0", "SECOND 1"],
        ["SECOND 2"],
    ]
    outputs = [ByteArrayDataSource() for _ in expected]
    for output in outputs:
        options.add_output(output)
    for _ in range(2):
        results = Splitter().process(options)
        assert len(results) == len(expected)
        for result, output, texts in zip(results, outputs, expected, strict=True):
            assert output.data == result.to_array()
            with Document(result.to_array()) as reopened:
                assert _texts(reopened) == texts


@pytest.mark.parametrize(
    "options",
    [
        {"selections": []},
        {"bookmark_level": 1},
        {"selections": [], "bookmark_level": 1},
    ],
)
def test_lowcode_split_modes_are_mutually_exclusive(options):
    with pytest.raises(ValueError, match="cannot be combined"):
        SplitOptions(pages_per_part=2, **options)


@pytest.mark.parametrize(
    "change",
    [
        {"pages_per_part": True},
        {"pages_per_part": 0},
        {"selections": []},
        {"bookmark_level": 1},
    ],
)
def test_mutated_lowcode_options_are_checked_before_reading_inputs(change):
    with io.BytesIO(b"Unread input") as stream:
        options = SplitOptions(pages_per_part=2).add_input(StreamDataSource(stream))
        for name, value in change.items():
            setattr(options, name, value)
        with pytest.raises((TypeError, ValueError)):
            Splitter().process(options)
        assert stream.tell() == 0


def test_lowcode_later_input_failure_does_not_write_partial_outputs(source):
    output = ByteArrayDataSource(b"Untouched")
    options = SplitOptions(pages_per_part=2).add_input(
        ByteArrayDataSource(_bytes(source))
    )
    options.add_input(ByteArrayDataSource(b"not a PDF")).add_output(output)
    with pytest.raises(AsposePdfException):
        Splitter().process(options)
    assert output.data == b"Untouched"


def test_lowcode_cumulative_result_count_is_bounded_before_output():
    with _document(3) as source:
        data = _bytes(source)
    output = ByteArrayDataSource(b"Untouched")
    options = SplitOptions(PdfLoadLimits(max_container_items=5), pages_per_part=1)
    options.add_input(ByteArrayDataSource(data)).add_input(ByteArrayDataSource(data))
    options.add_output(output)
    with pytest.raises(PdfResourceLimitException, match="page-count split results"):
        Splitter().process(options)
    assert output.data == b"Untouched"


def test_lowcode_serialization_failure_disposes_parts_without_writing_outputs(
    source, monkeypatch
):
    output = ByteArrayDataSource(b"Untouched")
    options = SplitOptions(pages_per_part=2).add_input(
        ByteArrayDataSource(_bytes(source))
    )
    options.add_output(output)
    original = SimplePdf.to_bytes
    parts = []

    def serialize(part):
        parts.append(part)
        if len(parts) == 2:
            raise PdfValidationException("Serialization failed")
        return original(part)

    monkeypatch.setattr(SimplePdf, "to_bytes", serialize)
    with pytest.raises(PdfValidationException, match="Serialization failed"):
        Splitter().process(options)
    assert output.data == b"Untouched" and len(parts) == 2
    for part in parts:
        with pytest.raises(AsposePdfException, match="disposed"):
            part._ensure_not_disposed()


def test_iterator_imports_only_requested_batches_and_restores_live_outlines(
    source, monkeypatch
):
    source.outlines.add(OutlineItem("Live edit", 1))
    engine = source._engine_pdf
    model = engine._outlines_data
    original = engine.extract_pages
    selections = []

    def extract(selection):
        selections.append(tuple(selection))
        return original(selection)

    monkeypatch.setattr(engine, "extract_pages", extract)
    parts = source.iter_split_every(2)
    assert selections == []
    with closing(parts):
        with next(parts) as first:
            assert selections == [(0, 1)]
            assert _texts(first) == ["PAGE 0", "PAGE 1"]
            assert first.outlines[0].title == "Live edit"
            assert engine._outlines_data is model
        with next(parts) as second:
            assert selections == [(0, 1), (2, 3)]
            assert _texts(second) == ["PAGE 2", "PAGE 3"]
            assert engine._outlines_data is model
    assert selections == [(0, 1), (2, 3)]
    assert engine._outlines_data is model
    assert source.outlines[0].title == "Live edit"
    assert source.page_count == 6


def test_iterator_releases_consumed_results_before_importing_the_next(source, monkeypatch):
    original = source._engine_pdf.extract_pages
    references = []

    def extract(selection):
        gc.collect()
        assert all(reference() is None for reference in references)
        result = original(selection)
        references.append(weakref.ref(result))
        return result

    monkeypatch.setattr(source._engine_pdf, "extract_pages", extract)
    for part in source.iter_split_every(1):
        with part:
            assert part.page_count == 1
        del part
    gc.collect()
    assert len(references) == source.page_count
    assert all(reference() is None for reference in references)


def test_closing_iterator_keeps_yielded_parts_usable_after_source_closes(source, tmp_path):
    parts = source.iter_split_every(2)
    with next(parts) as first:
        parts.close()
        source.dispose()
        assert _texts(first) == ["PAGE 0", "PAGE 1"]
        path = tmp_path / "first.pdf"
        first.save(path)
        with Document(path) as reopened:
            assert _texts(reopened) == _texts(first)
        with pytest.raises(StopIteration):
            next(parts)


@pytest.mark.parametrize("error", [PdfValidationException, KeyboardInterrupt])
def test_iterator_failure_preserves_yielded_parts_and_restores_source(
    source, monkeypatch, error
):
    source.outlines.add(OutlineItem("Live edit", 1))
    engine = source._engine_pdf
    original = engine.extract_pages
    model = engine._outlines_data
    imports = []

    def extract(selection):
        if imports:
            raise error("Later import failed")
        imports.append(tuple(selection))
        return original(selection)

    monkeypatch.setattr(engine, "extract_pages", extract)
    parts = source.iter_split_every(2)
    with next(parts) as first:
        with pytest.raises(error, match="Later import failed"):
            next(parts)
        assert _texts(first) == ["PAGE 0", "PAGE 1"]
        assert engine._outlines_data is model
        assert source.outlines[0].title == "Live edit"
        with pytest.raises(StopIteration):
            next(parts)


def test_disposing_source_between_batches_stops_before_another_import(source, monkeypatch):
    engine = source._engine_pdf
    original = engine.extract_pages
    imports = []

    def extract(selection):
        imports.append(tuple(selection))
        return original(selection)

    monkeypatch.setattr(engine, "extract_pages", extract)
    parts = source.iter_split_every(2)
    with next(parts) as first:
        source.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            next(parts)
        assert imports == [(0, 1)]
        assert _texts(first) == ["PAGE 0", "PAGE 1"]


@pytest.mark.parametrize("size,error", [(0, ValueError), (True, TypeError), ("2", TypeError)])
def test_iterator_validates_size_on_first_next_before_import(source, monkeypatch, size, error):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid sizes must fail before importing")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", forbidden)
    parts = source.iter_split_every(size)
    with pytest.raises(error, match="pages_per_part"):
        next(parts)


def test_iterator_validates_empty_and_disposed_sources():
    with Document() as empty:
        assert list(empty.iter_split_every(2)) == []
        with pytest.raises(ValueError, match="pages_per_part"):
            next(empty.iter_split_every(0))
    with pytest.raises(AsposePdfException, match="disposed"):
        next(empty.iter_split_every(2))


def test_streaming_iterator_leaves_unrequested_content_unread(source, tmp_path, monkeypatch):
    path = tmp_path / "batch.pdf"
    source.save(path)
    with Document.open_streaming(path) as streamed:
        engine = streamed._engine_pdf
        original = engine.get_page_content
        reads = []

        def read(index):
            assert index < 2, "Future batches must not be read"
            reads.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", read)
        parts = streamed.iter_split_every(2)
        assert reads == []
        with closing(parts), next(parts) as first:
            assert set(reads) == {0, 1}
            assert _texts(first) == ["PAGE 0", "PAGE 1"]
        assert engine.page_contents == []
        assert engine._extracted_text is None


def test_iterator_rejects_and_disposes_failed_result_without_closing_previous_part(
    source, monkeypatch
):
    import aspose_pdf.engine.page_count_split as split_module

    original = split_module._check_result
    checked = []

    def check(result, **options):
        checked.append(result)
        if len(checked) == 2:
            raise PdfResourceLimitException("Result exceeds limits")
        return original(result, **options)

    monkeypatch.setattr(split_module, "_check_result", check)
    parts = source.iter_split_every(2)
    with next(parts) as first:
        with pytest.raises(PdfResourceLimitException, match="exceeds limits"):
            next(parts)
        assert _texts(first) == ["PAGE 0", "PAGE 1"]
        with pytest.raises(AsposePdfException, match="disposed"):
            checked[1]._ensure_not_disposed()


def test_iterator_disposes_unclaimed_engine_when_document_wrapping_fails(source, monkeypatch):
    import aspose_pdf.document as document_module

    original = source._engine_pdf.extract_pages
    created = []

    def extract(selection):
        result = original(selection)
        created.append(result)
        return result

    def fail(**options):
        raise RuntimeError("Cannot wrap result")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", extract)
    monkeypatch.setattr(document_module, "Document", fail)
    with pytest.raises(RuntimeError, match="Cannot wrap result"):
        next(source.iter_split_every(2))
    assert len(created) == 1
    with pytest.raises(AsposePdfException, match="disposed"):
        created[0]._ensure_not_disposed()
