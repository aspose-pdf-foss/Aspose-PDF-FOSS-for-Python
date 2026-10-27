"""Offline HTML comparison reports preserve metrics, text and source documents."""

from __future__ import annotations

import base64
import gc
import io
import weakref
from dataclasses import replace
from html.parser import HTMLParser
from itertools import repeat

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


class Report(HTMLParser):
    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self.tags, self.images, self.rows, self.pairs, self.diffs, self.text = (
            [],
            [],
            [],
            [],
            [],
            [],
        )
        self.row = self.cell = self.diff = None
        self.feed(data.decode("utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "img":
            self.images.append(attrs)
        elif tag == "tr" and "data-pair" in attrs:
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = []
        elif tag == "details" and attrs.get("class") == "pair":
            self.pairs.append(attrs)
        elif tag == "pre" and attrs.get("class") == "diff":
            self.diff = []

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "pre" and self.diff is not None:
            self.diffs.append("".join(self.diff))
            self.diff = None

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.diff is not None:
            self.diff.append(data)


def _document(*texts, limits=None, size=(120, 100)):
    document = Document(limits=limits)
    for text in texts:
        page = document.pages.add(size=size)
        if text:
            page.add_text(text, 10, 50, font_size=12)
    return document


def test_combined_report_contains_summary_exact_diffs_and_embedded_pngs(tmp_path):
    with (
        _document("Invoice 10", "Same") as left,
        _document("Invoice 20", "Same", "Added") as right,
    ):
        visuals = left.compare_visual(right, include_diff_images=True)
        texts = left.compare_text(right)
        source_contents = [
            [page.content for page in document.pages] for document in (left, right)
        ]
        source_metadata = [dict(document.info) for document in (left, right)]
        data = left.export_comparison_html(right)
        report = Report(data)
        assert [row[:3] for row in report.rows] == [
            ["1", "1", "Changed"],
            ["2", "2", "Match"],
            ["Missing", "3", "Missing page"],
        ]
        assert [row[3] for row in report.rows] == [
            f"{item.difference_ratio * 100:.6g}%" for item in visuals
        ]
        assert report.diffs == [item.unified_diff for item in texts if not item.matches]
        assert [pair["id"] for pair in report.pairs] == ["pair-1", "pair-2", "pair-3"]
        assert ["open" in pair for pair in report.pairs] == [True, False, True]
        assert len(report.images) == 3
        for image, expected in zip(report.images, visuals, strict=True):
            assert image["src"].startswith("data:image/png;base64,")
            assert image["loading"] == "lazy" and image["alt"]
            png = base64.b64decode(image["src"].split(",", 1)[1], validate=True)
            assert png == expected.diff_image.to_png()
        assert not any(
            tag in ("script", "link", "iframe", "object") for tag, _ in report.tags
        )
        assert any(
            attrs.get("http-equiv") == "Content-Security-Policy"
            for _, attrs in report.tags
        )
        assert [
            [page.content for page in document.pages] for document in (left, right)
        ] == source_contents
        assert [dict(document.info) for document in (left, right)] == source_metadata
    destination = tmp_path / "report.html"
    destination.write_bytes(data)
    assert Report(destination.read_bytes()).rows == report.rows


def test_text_only_report_never_renders_pages(monkeypatch):
    with _document("Before") as left, _document("After") as right:

        def no_render(*args, **kwargs):
            raise AssertionError("Text-only reports must not render pages")

        monkeypatch.setattr(Page, "render", no_render)
        data = left.export_comparison_html(right, mode="text")
        report = Report(data)
        assert report.rows == [["1", "1", "Changed", "—", "Changed"]]
        assert len(report.diffs) == 1 and report.images == []
        assert " DPI" not in "".join(report.text)


def test_visual_only_report_never_extracts_text(monkeypatch):
    with _document("Before") as left, _document("After") as right:

        def no_text(*args, **kwargs):
            raise AssertionError("Visual-only reports must not extract text")

        for document in (left, right):
            monkeypatch.setattr(document._engine_pdf, "extract_page_text", no_text)
        report = Report(
            left.export_comparison_html(right, mode="visual", include_diff_images=False)
        )
        assert report.rows[0][2] == "Changed" and report.rows[0][4] == "—"
        assert report.images == [] and report.diffs == []
        assert "Text: " not in "".join(report.text)


def test_source_lines_resembling_diff_headers_keep_added_and_removed_colors():
    with _document("--- old") as left, _document("+++ new") as right:
        report = Report(left.export_comparison_html(right, mode="text"))
        classes = [attrs.get("class") for tag, attrs in report.tags if tag == "span"]
        assert "diff-add" in classes and "diff-del" in classes
        assert report.diffs == [left.compare_text(right)[0].unified_diff]


def test_completed_difference_rasters_are_released_before_next_pair(monkeypatch):
    from aspose_pdf.engine import visual_comparison

    images = []
    original = visual_comparison._compare

    def compare(*args, **kwargs):
        gc.collect()
        assert all(reference() is None for reference in images)
        result = original(*args, **kwargs)
        images.append(weakref.ref(result.diff_image))
        return result

    monkeypatch.setattr(visual_comparison, "_compare", compare)
    with _document("A", "B", "C") as left, _document("D", "E", "F") as right:
        report = Report(left.export_comparison_html(right))
        assert len(report.images) == len(images) == 3
    gc.collect()
    assert all(reference() is None for reference in images)


def test_report_escapes_source_text_titles_and_labels_without_creating_active_html():
    attack = '</title><script>alert("x")</script><img src="https://example.invalid/" onerror="alert(1)">'
    with _document("Before") as left, _document(attack) as right:
        report = Report(
            left.export_comparison_html(
                right,
                mode="text",
                title="Review Привет 🌍 " + attack,
                left_label=attack,
                right_label="Revised & verified",
            )
        )
        assert not any(tag in ("script", "img") for tag, _ in report.tags)
        assert attack in "".join(report.text)
        assert attack in report.diffs[0]


def test_selection_generators_keep_order_repetitions_and_missing_pages():
    with _document("A", "B", "C") as left, _document("A", "B") as right:
        report = Report(
            left.export_comparison_html(
                right,
                mode="text",
                pages=(index for index in (2, 0, 2)),
                other_pages=(index for index in (1, 0)),
            )
        )
        assert [row[:2] for row in report.rows] == [
            ["3", "2"],
            ["1", "1"],
            ["3", "Missing"],
        ]
        assert [pair["id"] for pair in report.pairs] == ["pair-1", "pair-2", "pair-3"]


def test_tolerated_visual_differences_are_reported_without_claiming_exact_equality():
    with _document("", size=(10, 10)) as left, _document("", size=(10, 10)) as right:
        right.pages[0].draw_rectangle(
            0, 0, 10, 10, fill_color="#010000", stroke_color=None
        )
        report = Report(
            left.export_comparison_html(right, mode="visual", max_diff_ratio=1)
        )
        assert report.rows[0][2:4] == ["Within tolerance", "100%"]
        assert "open" in report.pairs[0]
        assert len(report.images) == 1


def test_normalized_text_matches_are_identified_in_report():
    with _document("Before") as left, _document("before") as right:
        report = Report(
            left.export_comparison_html(right, mode="text", case_sensitive=False)
        )
        assert report.rows[0][2] == "Normalized text match"
        assert report.rows[0][4] == "Match" and report.diffs == []


def test_ignored_visual_regions_do_not_hide_text_differences():
    with _document("Before") as left, _document("After") as right:
        regions = ((0, 0, 120, 100),)
        visual = Report(
            left.export_comparison_html(
                right, mode="visual", ignore_regions=iter(regions)
            )
        )
        combined = Report(
            left.export_comparison_html(right, ignore_regions=iter(regions))
        )
        assert visual.rows[0][2:4] == ["Match", "0%"]
        assert combined.rows[0][2:5] == ["Changed", "0%", "Changed"]


def test_atomic_file_output_and_short_binary_stream_writes(tmp_path):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with _document("Before") as left, _document("After") as right:
        path = tmp_path / "nested" / "comparison.html"
        data = left.export_comparison_html(right, path, mode="text")
        assert path.read_bytes() == data
        with ShortWriter(b"HEAD") as stream:
            stream.seek(4)
            assert left.export_comparison_html(right, stream, mode="text") == data
            assert not stream.closed and stream.getvalue() == b"HEAD" + data
        with pytest.raises(FileExistsError):
            left.export_comparison_html(right, path, overwrite=False)
        assert path.read_bytes() == data


def test_late_comparison_failure_preserves_existing_file_and_stream(
    tmp_path, monkeypatch
):
    with _document("A", "B") as left, _document("A", "C") as right:
        original = left.compare_text

        def fail_late(other, **options):
            if options["pages"] == (1,):
                raise PdfParseException("Broken second page")
            return original(other, **options)

        monkeypatch.setattr(left, "compare_text", fail_late)
        path = tmp_path / "report.html"
        path.write_bytes(b"Keep file")
        with io.BytesIO(b"Keep stream") as stream:
            for destination in (path, stream):
                with pytest.raises(PdfParseException, match="Broken second"):
                    left.export_comparison_html(right, destination, mode="text")
            assert stream.getvalue() == b"Keep stream" and not stream.closed
        assert path.read_bytes() == b"Keep file"


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "unknown"},
        {"mode": None},
        {"title": ""},
        {"title": 1},
        {"title": "\ud800"},
        {"left_label": ""},
        {"right_label": None},
        {"dpi": float("nan")},
        {"dpi": 0},
        {"tolerance": True},
        {"tolerance": 256},
        {"max_diff_ratio": 1.1},
        {"include_diff_images": 1},
        {"include_annotations": 1},
        {"case_sensitive": 1},
        {"ignore_whitespace": 1},
        {"skip_artifacts": 1},
        {"pages": [0, 99]},
        {"other_pages": [0, 99]},
        {"pages": [True]},
        {"ignore_regions": [(0, 0, 0, 1)]},
        {"overwrite": 1},
    ],
)
def test_invalid_options_are_rejected_before_page_processing(options, monkeypatch):
    with _document("A") as left, _document("B") as right:

        def no_work(*args, **kwargs):
            raise AssertionError("Arguments must be validated before page work")

        monkeypatch.setattr(Page, "render", no_work)
        for document in (left, right):
            monkeypatch.setattr(document._engine_pdf, "extract_page_text", no_work)
        with io.BytesIO(b"Untouched") as output:
            with pytest.raises(PdfValidationException):
                left.export_comparison_html(right, output, **options)
            assert output.getvalue() == b"Untouched"


def test_all_visual_geometry_is_checked_before_text_or_rendering(monkeypatch):
    from aspose_pdf.engine import visual_comparison

    with _document("A", "B") as left, _document("C", "D") as right:
        original = visual_comparison._size

        def invalid_later(document, index, dpi):
            if document is right and index == 1:
                raise PdfValidationException("Invalid later geometry")
            return original(document, index, dpi)

        def no_work(*args, **kwargs):
            raise AssertionError("All geometry must be checked first")

        monkeypatch.setattr(visual_comparison, "_size", invalid_later)
        monkeypatch.setattr(Page, "render", no_work)
        monkeypatch.setattr(left._engine_pdf, "extract_page_text", no_work)
        with pytest.raises(PdfValidationException, match="later geometry"):
            left.export_comparison_html(right)


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize(
    "limits,mode",
    [
        ({"max_input_bytes": 256}, "text"),
        ({"max_total_decoded_bytes": 256}, "text"),
        ({"max_raster_pixels": 50}, "visual"),
        ({"max_object_bytes": 3}, "text"),
    ],
)
def test_report_uses_stricter_source_output_and_raster_limits(side, limits, mode):
    policy = replace(PdfLoadLimits.unlimited(), **limits)
    with (
        _document(
            "A", limits=policy if side == "left" else None, size=(10, 10)
        ) as left,
        _document(
            "B", limits=policy if side == "right" else None, size=(10, 10)
        ) as right,
        io.BytesIO(b"Untouched") as output,
    ):
        with pytest.raises(PdfResourceLimitException):
            left.export_comparison_html(right, output, mode=mode)
        assert output.getvalue() == b"Untouched"


def test_cumulative_preview_pixels_are_bounded_before_processing(monkeypatch):
    policy = replace(PdfLoadLimits.unlimited(), max_raster_pixels=150)
    with (
        _document("", "", limits=policy, size=(10, 10)) as left,
        _document("", "", size=(10, 10)) as right,
    ):

        def no_render(*args, **kwargs):
            raise AssertionError("Cumulative previews must be checked before rendering")

        with monkeypatch.context() as patch:
            patch.setattr(Page, "render", no_render)
            with pytest.raises(
                PdfResourceLimitException, match="comparison report images"
            ):
                left.export_comparison_html(right, mode="visual")
        assert not Report(
            left.export_comparison_html(right, mode="visual", include_diff_images=False)
        ).images


def test_unbounded_selections_are_stopped_by_resource_policy():
    policy = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    with _document("A", limits=policy) as left, _document("B") as right:
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            left.export_comparison_html(right, pages=repeat(0))


def test_streaming_password_protected_sources_read_only_selected_pages(
    tmp_path, monkeypatch
):
    paths = []
    for number in range(2):
        with _document("Skip", "Before" if number == 0 else "After", "Skip") as source:
            source.encrypt("user", "owner")
            path = tmp_path / f"{number}.pdf"
            source.save(path)
            paths.append(path)
    source_bytes = [path.read_bytes() for path in paths]
    with (
        Document.open_streaming(paths[0], password="user") as left,
        Document.open_streaming(paths[1], password="user") as right,
    ):
        for document in (left, right):
            original = document._engine_pdf.get_page_content

            def selected_only(index, *, read=original):
                assert index == 1
                return read(index)

            monkeypatch.setattr(document._engine_pdf, "get_page_content", selected_only)
        report = Report(left.export_comparison_html(right, pages=[1], other_pages=[1]))
        assert report.rows[0][:3] == ["2", "2", "Changed"]
    assert [path.read_bytes() for path in paths] == source_bytes


def test_empty_selections_and_disposed_documents():
    with Document() as left, Document() as right:
        report = Report(left.export_comparison_html(right))
        assert report.rows == report.images == report.pairs == []
        assert "No pages selected" in "".join(report.text)
        with pytest.raises(PdfValidationException, match="Document"):
            left.export_comparison_html(None)
        right.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            left.export_comparison_html(right)
    with Document() as other:
        with pytest.raises(AsposePdfException, match="disposed"):
            left.export_comparison_html(other)
