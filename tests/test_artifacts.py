"""Marking decoration as ``/Artifact``, and checking that content says which it is.

ISO 14289-1 7.1 has every mark on a page of a tagged document be one of two
things: real content inside a tagged marked-content sequence, or decoration
inside an ``/Artifact`` one. Nothing this package authored used to say the
second, so a perfectly tagged document stopped being conformant the moment it
was stamped -- and nothing reported it. These tests cover both halves: the
marking, and the check that finds what is still unmarked.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import (
    Document,
    ImageStamp,
    PageNumberStamp,
    PageSize,
    TextStamp,
    VerticalAlignment,
)
from aspose_pdf.engine.content_authoring import wrap_artifact
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.exceptions import PdfValidationException


def _page(size: PageSize = PageSize.A4):
    document = Document()
    return document, document.pages.add(size)


def _artifacts(content: bytes) -> list[str]:
    """The property list of each ``/Artifact`` sequence, in order."""
    return [
        match.group(1).decode("latin-1")
        for match in re.finditer(rb"/Artifact(.*?) BDC", content)
    ]


def _bbox(properties: str) -> list[float]:
    match = re.search(r"/BBox \[([-\d. e]+)\]", properties)
    assert match, f"no /BBox in {properties!r}"
    return [float(value) for value in match.group(1).split()]


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------
class TestWrapArtifact:
    def test_bare(self):
        assert wrap_artifact(b"x") == b"/Artifact BDC\nx\nEMC\n"

    def test_a_trailing_newline_is_not_doubled(self):
        assert wrap_artifact(b"x\n") == b"/Artifact BDC\nx\nEMC\n"

    def test_empty_content(self):
        assert wrap_artifact(b"") == b"/Artifact BDC\nEMC\n"

    def test_every_property(self):
        out = wrap_artifact(
            b"x",
            artifact_type="Pagination",
            subtype="Footer",
            bbox=(0, 1, 2, 3),
            attached=["bottom"],
        )
        assert out.startswith(
            b"/Artifact << /Type /Pagination /Subtype /Footer "
            b"/BBox [0 1 2 3] /Attached [/Bottom] >> BDC\n"
        )

    def test_a_leading_slash_is_accepted(self):
        out = wrap_artifact(b"x", artifact_type="/Layout")
        assert b"/Type /Layout" in out

    def test_the_sequence_is_outside_the_graphics_state(self):
        """14.6: a sequence may contain a ``q``/``Q`` pair but not straddle one."""
        out = wrap_artifact(b"q 1 0 0 1 2 3 cm /S1 Do Q")
        assert out.index(b"BDC") < out.index(b"q ")
        assert out.index(b"Q") < out.index(b"EMC")

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"artifact_type": "Nope"}, "artifact type is one of"),
            ({"subtype": "Nope"}, "artifact subtype is one of"),
            (
                {"artifact_type": "Layout", "subtype": "Header"},
                "belongs to a Pagination artifact",
            ),
            ({"bbox": (1, 2)}, "four finite numbers"),
            ({"bbox": (1, 2, 3, float("inf"))}, "four finite numbers"),
            ({"attached": ["Middle"]}, "attached to one or more of"),
            (
                {"artifact_type": "Layout", "attached": ["Top"]},
                "not to a\nLayout one",
            ),
        ],
    )
    def test_refusals(self, kwargs, message):
        with pytest.raises(PdfValidationException, match=message.replace("\n", " ")):
            wrap_artifact(b"x", **kwargs)


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------
def _stamped(stamp, size: PageSize = PageSize.A4) -> tuple[Document, bytes]:
    document, _page_object = _page(size)
    document.add_stamp(stamp)
    return document, document.pages[0].content


class TestStampArtifacts:
    def test_a_text_stamp_is_a_watermark(self):
        document, content = _stamped(TextStamp(value="DRAFT"))
        (properties,) = _artifacts(content)
        assert "/Type /Pagination" in properties
        assert "/Subtype /Watermark" in properties
        assert "/Attached" not in properties  # it sits where it was aligned
        document.close()

    def test_an_image_stamp_too(self):
        png = _png_bytes()
        document, content = _stamped(ImageStamp(image=png, width=20, height=20))
        (properties,) = _artifacts(content)
        assert "/Subtype /Watermark" in properties
        document.close()

    @pytest.mark.parametrize(
        ("alignment", "subtype", "attached"),
        [
            (VerticalAlignment.BOTTOM, "Footer", "/Attached [/Bottom]"),
            (VerticalAlignment.TOP, "Header", "/Attached [/Top]"),
            (VerticalAlignment.CENTER, "Watermark", None),
        ],
    )
    def test_a_page_number_knows_which_edge_it_is_at(
        self, alignment, subtype, attached
    ):
        """Not a guess: the caller already said where the stamp goes."""
        document, content = _stamped(
            PageNumberStamp(value="{page}", vertical_alignment=alignment)
        )
        (properties,) = _artifacts(content)
        assert f"/Subtype /{subtype}" in properties
        if attached is None:
            assert "/Attached" not in properties
        else:
            assert attached in properties
        document.close()

    def test_an_explicit_type_and_subtype_win(self):
        document, content = _stamped(
            TextStamp(value="x", artifact_type="Page", artifact_subtype=None)
        )
        (properties,) = _artifacts(content)
        assert "/Type /Page" in properties
        assert "/Subtype" not in properties
        document.close()

    def test_an_explicit_subtype_wins(self):
        document, content = _stamped(
            TextStamp(value="x", artifact_subtype="Header",
                      vertical_alignment=VerticalAlignment.TOP)
        )
        (properties,) = _artifacts(content)
        assert "/Subtype /Header" in properties
        assert "/Attached [/Top]" in properties
        document.close()

    def test_artifact_false_puts_it_on_as_content(self):
        document, content = _stamped(TextStamp(value="x", artifact=False))
        assert _artifacts(content) == []
        assert b"Do Q" in content
        document.close()

    def test_an_impossible_artifact_is_refused_before_anything_is_drawn(self):
        document, _page_object = _page()
        with pytest.raises(PdfValidationException, match="artifact type is one of"):
            document.add_stamp(TextStamp(value="x", artifact_type="Nonsense"))
        assert b"Artifact" not in document.pages[0].content
        document.close()

    def test_the_box_is_where_the_stamp_landed(self):
        document, content = _stamped(
            TextStamp(value="DRAFT", vertical_alignment=VerticalAlignment.BOTTOM)
        )
        (properties,) = _artifacts(content)
        x0, y0, x1, y1 = _bbox(properties)
        matrix = re.search(rb"q ([-\d.e ]+) cm", content).group(1).split()
        assert float(matrix[4]) == pytest.approx(x0)
        assert float(matrix[5]) == pytest.approx(y0)
        assert x1 > x0 and y1 > y0
        document.close()

    def test_a_rotated_stamp_s_box_holds_all_four_corners(self):
        upright, content_upright = _stamped(TextStamp(value="DRAFT"))
        turned, content_turned = _stamped(TextStamp(value="DRAFT", rotate=45))
        flat = _bbox(_artifacts(content_upright)[0])
        rotated = _bbox(_artifacts(content_turned)[0])
        assert rotated[3] - rotated[1] > flat[3] - flat[1]
        upright.close()
        turned.close()

    def test_each_page_gets_its_own_box(self):
        document = Document()
        document.pages.add(PageSize.A4)
        document.pages.add(PageSize.A5)
        document.add_stamp(TextStamp(value="DRAFT"))
        first = _bbox(_artifacts(document.pages[0].content)[0])
        second = _bbox(_artifacts(document.pages[1].content)[0])
        assert first != second  # the pages are not the same size
        document.close()

    def test_a_background_stamp_is_marked_too(self):
        document, content = _stamped(TextStamp(value="x", background=True))
        assert len(_artifacts(content)) == 1
        assert content.index(b"/Artifact") < content.index(b"Do")
        document.close()

    def test_the_invocation_still_reads_as_it_did(self):
        """The marking goes around it, so what matched before still matches."""
        document, content = _stamped(TextStamp(value="x"))
        assert re.search(rb"q ([-\d.e ]+) cm /\w+ Do Q", content)
        document.close()


def _png_bytes() -> bytes:
    """A real one-pixel PNG -- the one the engine keeps for hiding an image."""
    from aspose_pdf.engine.simple_pdf import _TRANSPARENT_PIXEL_PNG

    return _TRANSPARENT_PIXEL_PNG


# ---------------------------------------------------------------------------
# Imposition, which shares the placement code and is *not* decoration
# ---------------------------------------------------------------------------
class TestImpositionStaysContent:
    def _source(self, pages: int = 4) -> Document:
        document = Document()
        for index in range(pages):
            page = document.pages.add(PageSize.A4)
            page.add_text(f"page {index + 1}", 60, 700)
        return document

    def test_n_up_marks_nothing(self):
        with self._source() as document:
            sheets = document.n_up(2, 2)
            content = sheets.pages[0].content
            assert b"/Artifact" not in content
            assert content.count(b"Do Q") == 4
            sheets.close()

    def test_a_booklet_marks_nothing(self):
        with self._source() as document:
            booklet = document.booklet()
            assert b"/Artifact" not in booklet.pages[0].content
            booklet.close()


# ---------------------------------------------------------------------------
# Redaction bars
# ---------------------------------------------------------------------------
class TestRedactionBars:
    def _redacted(self) -> tuple[Document, bytes]:
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("SSN 123-45-6789", 60, 710, tag="P")
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        reopened = Document(buffer.getvalue())
        reopened.pages[0].redact_text("123-45-6789", overlay=True)
        return reopened, reopened.pages[0].content

    def test_the_bars_are_a_layout_artifact(self):
        document, content = self._redacted()
        (properties,) = _artifacts(content)
        assert "/Type /Layout" in properties
        assert "/Attached" not in properties
        document.close()

    def test_the_box_covers_the_bars(self):
        document, content = self._redacted()
        x0, y0, x1, y1 = _bbox(_artifacts(content)[0])
        corners = [
            (float(m.group(1)), float(m.group(2)))
            for m in re.finditer(rb"([\d.]+) ([\d.]+) (?:m|l)", content)
        ]
        assert corners
        assert x0 == pytest.approx(min(x for x, _y in corners), abs=0.01)
        assert x1 == pytest.approx(max(x for x, _y in corners), abs=0.01)
        assert y0 == pytest.approx(min(y for _x, y in corners), abs=0.01)
        assert y1 == pytest.approx(max(y for _x, y in corners), abs=0.01)
        document.close()

    def test_the_bar_geometry_is_unchanged(self):
        document, content = self._redacted()
        assert b"88.008 707.600 m 156.064 707.600 l" in content
        document.close()


# ---------------------------------------------------------------------------
# A caller marking their own decoration
# ---------------------------------------------------------------------------
class TestAuthoredArtifacts:
    def test_text(self):
        document, page = _page()
        page.add_text("Chapter One", 50, 780, artifact="Pagination")
        assert _artifacts(page.content) == [" << /Type /Pagination >>"]
        document.close()

    def test_a_bare_artifact(self):
        document, page = _page()
        page.draw_line(50, 770, 545, 770, artifact=True)
        assert _artifacts(page.content) == [""]
        document.close()

    @pytest.mark.parametrize(
        "draw",
        [
            lambda page: page.draw_rectangle(
                50, 100, 100, 20, fill_color=0.9, artifact="Layout"
            ),
            lambda page: page.draw_line(10, 10, 20, 20, artifact="Layout"),
            lambda page: page.add_image(_png_bytes(), 10, 10, 5, 5, artifact="Layout"),
        ],
    )
    def test_every_authoring_method_can_mark(self, draw):
        document, page = _page()
        draw(page)
        assert _artifacts(page.content) == [" << /Type /Layout >>"]
        document.close()

    def test_a_path_object_can_mark(self):
        from aspose_pdf import GraphicsPath

        document, page = _page()
        page.draw_path(
            GraphicsPath().move_to(10, 10).line_to(50, 50), artifact=True
        )
        assert _artifacts(page.content) == [""]
        document.close()

    @pytest.mark.parametrize(
        "call",
        [
            lambda page: page.add_text("x", 10, 10, tag="P", artifact=True),
            lambda page: page.draw_line(0, 0, 1, 1, tag="Figure", artifact=True),
            lambda page: page.add_image(
                _png_bytes(), 1, 1, 2, 2, alt="a picture", artifact=True
            ),
        ],
    )
    def test_tagged_and_artifact_at_once_is_refused(self, call):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="either tagged or an artifact"):
            call(page)
        document.close()

    def test_no_marking_leaves_the_bytes_alone(self):
        plain, page_plain = _page()
        page_plain.add_text("x", 10, 10)
        marked, page_marked = _page()
        page_marked.add_text("x", 10, 10, artifact=False)
        assert page_plain.content == page_marked.content
        plain.close()
        marked.close()


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
def _warnings(document: Document) -> list[str]:
    buffer = io.BytesIO()
    document.save(buffer)
    with Document(buffer.getvalue()) as reopened:
        return reopened.validate_pdfua().warnings


def _coverage_warnings(document: Document) -> list[str]:
    return [w for w in _warnings(document) if "neither tagged nor an artifact" in w]


class TestCoverageCheck:
    def test_a_stamped_tagged_document_is_clean(self):
        """The case the audit found: it used to pass while being wrong."""
        document, page = _page()
        page.add_text("content", 50, 700, tag="P")
        document.convert_to_pdfua(title="x")
        document.add_stamp(TextStamp(value="DRAFT", opacity=0.3))
        assert _coverage_warnings(document) == []
        document.close()

    def test_a_stamp_put_on_as_content_is_reported(self):
        document, page = _page()
        page.add_text("content", 50, 700, tag="P")
        document.convert_to_pdfua(title="x")
        document.add_stamp(TextStamp(value="DRAFT", artifact=False))
        (warning,) = _coverage_warnings(document)
        assert "page 1" in warning and "Tj" in warning
        document.close()

    def test_untagged_content_under_a_shell_is_reported(self):
        document, page = _page()
        page.add_text("untagged", 50, 700)
        page.draw_rectangle(50, 600, 100, 50, fill_color=(1, 0, 0))
        document.convert_to_pdfua(title="x")
        (warning,) = _coverage_warnings(document)
        assert "B" in warning and "Tj" in warning
        document.close()

    def test_marked_decoration_is_clean(self):
        document, page = _page()
        page.add_text("real", 50, 700, tag="P")
        page.draw_rectangle(50, 600, 100, 50, fill_color=(1, 0, 0), artifact="Layout")
        document.convert_to_pdfua(title="x")
        assert _coverage_warnings(document) == []
        document.close()

    def test_the_rule_does_not_apply_to_an_untagged_document(self):
        document, page = _page()
        page.add_text("plain", 50, 700)
        assert _coverage_warnings(document) == []
        document.close()

    def test_it_is_a_warning_and_not_a_failure(self):
        """A shell-only conversion is a shell as asked for, not a failure."""
        document, page = _page()
        page.add_text("untagged", 50, 700)
        document.convert_to_pdfua(title="x")
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            result = reopened.validate_pdfua()
        assert result.is_valid
        assert any("neither tagged nor an artifact" in w for w in result.warnings)

    def test_an_auto_tagged_document_is_clean(self):
        document, page = _page()
        page.add_text("A heading", 50, 760, font_size=20)
        page.add_text("Some body text that follows it.", 50, 720)
        document.convert_to_pdfua(title="x", auto_tag=True)
        assert _coverage_warnings(document) == []
        document.close()

    def test_a_tagged_table_is_clean(self):
        from aspose_pdf import Table

        document, page = _page()
        table = Table(column_widths=[200, 200])
        table.add_header_row(["Item", "Price"])
        table.add_row(["Widget", "9.99"])
        page.add_table(table, 50, 700)
        document.convert_to_pdfua(title="x")
        assert _coverage_warnings(document) == []
        document.close()

    def test_a_tagged_text_block_is_clean(self):
        from aspose_pdf import TextBlock

        document, page = _page()
        page.add_text_block(TextBlock("Running text. " * 20, width=400), 50, 700)
        document.convert_to_pdfua(title="x")
        assert _coverage_warnings(document) == []
        document.close()


class TestCoverageScanner:
    """What the scan counts, and what it deliberately does not."""

    def _scan(self, content: bytes, resources=None) -> list[str]:
        from aspose_pdf.engine.conformance import _uncovered_marks

        document = Document()
        document.pages.add(PageSize.A4)
        engine = document._engine_pdf
        engine._ensure_cos()
        try:
            return _uncovered_marks(engine, content, resources, 0, set())
        finally:
            document.close()

    def test_a_painted_path_is_a_mark(self):
        assert self._scan(b"0 0 10 10 re f\n") == ["f"]

    def test_a_path_that_paints_nothing_is_not(self):
        assert self._scan(b"0 0 10 10 re n\n") == []

    def test_covered_content_is_not_reported(self):
        assert self._scan(b"/Artifact BDC 0 0 10 10 re f EMC\n") == []

    def test_a_tagged_sequence_covers_too(self):
        assert self._scan(b"/P << /MCID 0 >> BDC 0 0 1 1 re f EMC\n") == []

    def test_an_mcid_in_a_property_list_is_not_an_operator(self):
        assert self._scan(b"/P << /MCID 0 /Alt (f) >> BDC EMC 1 1 2 2 re S\n") == ["S"]

    def test_a_string_that_looks_like_an_operator_is_not_one(self):
        assert self._scan(b"BT /F1 12 Tf (f S Tj) Tj ET\n") == ["Tj"]

    def test_nesting_is_followed(self):
        assert self._scan(
            b"/Artifact BDC /Span BDC 0 0 1 1 re f EMC EMC 2 2 3 3 re f\n"
        ) == ["f"]

    def test_an_unbalanced_emc_does_not_upset_the_count(self):
        assert self._scan(b"EMC 0 0 1 1 re f\n") == ["f"]

    def test_an_inline_image_is_a_mark(self):
        content = b"BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI\n"
        assert self._scan(content) == ["BI"]

    def test_an_uncovered_image_is_a_mark(self):
        document, page = _page()
        page.add_image(_png_bytes(), 10, 10, 5, 5)
        engine = document._engine_pdf
        resources = engine._resolve(
            engine._get_page_dict(0).mapping.get(PdfName("Resources"))
        )
        from aspose_pdf.engine.conformance import _uncovered_marks

        found = _uncovered_marks(engine, page.content, resources, 0, set())
        document.close()
        assert found == ["Do"]

    def test_a_form_that_covers_its_own_content_is_clean(self):
        document, page = _page()
        engine = document._engine_pdf
        body = b"/Artifact BDC 0 0 10 10 re f EMC\n"
        self._invoke_form(engine, body)
        resources = engine._resolve(
            engine._get_page_dict(0).mapping.get(PdfName("Resources"))
        )
        from aspose_pdf.engine.conformance import _uncovered_marks

        found = _uncovered_marks(engine, page.content, resources, 0, set())
        document.close()
        assert found == []

    def test_a_form_that_does_not_is_reported_from_inside(self):
        document, page = _page()
        engine = document._engine_pdf
        self._invoke_form(engine, b"0 0 10 10 re f\n")
        resources = engine._resolve(
            engine._get_page_dict(0).mapping.get(PdfName("Resources"))
        )
        from aspose_pdf.engine.conformance import _uncovered_marks

        found = _uncovered_marks(engine, page.content, resources, 0, set())
        document.close()
        assert found == ["f"]

    def test_a_form_invoked_inside_a_sequence_is_covered_with_it(self):
        document, page = _page()
        engine = document._engine_pdf
        self._invoke_form(engine, b"0 0 10 10 re f\n", wrap=True)
        resources = engine._resolve(
            engine._get_page_dict(0).mapping.get(PdfName("Resources"))
        )
        from aspose_pdf.engine.conformance import _uncovered_marks

        found = _uncovered_marks(engine, page.content, resources, 0, set())
        document.close()
        assert found == []

    def test_a_form_that_invokes_itself_does_not_hang(self):
        document, page = _page()
        engine = document._engine_pdf
        engine._ensure_cos()
        body = b"q /Loop Do Q 0 0 1 1 re f\n"
        form = PdfStream(
            body,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray(
                    [PdfNumber(0), PdfNumber(0), PdfNumber(10), PdfNumber(10)]
                ),
                PdfName("Length"): PdfNumber(len(body)),
            },
        )
        reference = engine._cos_doc.register_object(form)
        xobjects = engine._ensure_resource_subdict(0, "XObject")
        xobjects.mapping[PdfName("Loop")] = reference
        form.mapping[PdfName("Resources")] = PdfDictionary(
            {PdfName("XObject"): xobjects}
        )
        engine._append_content_to_page(0, b"q /Loop Do Q\n")
        resources = engine._resolve(
            engine._get_page_dict(0).mapping.get(PdfName("Resources"))
        )
        from aspose_pdf.engine.conformance import _uncovered_marks

        found = _uncovered_marks(engine, page.content, resources, 0, set())
        document.close()
        assert found == ["f"]  # followed once, not for ever

    @staticmethod
    def _invoke_form(engine, body: bytes, *, wrap: bool = False) -> None:
        engine._ensure_cos()
        form = PdfStream(
            body,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray(
                    [PdfNumber(0), PdfNumber(0), PdfNumber(10), PdfNumber(10)]
                ),
                PdfName("Length"): PdfNumber(len(body)),
            },
        )
        xobjects = engine._ensure_resource_subdict(0, "XObject")
        xobjects.mapping[PdfName("Fm0")] = engine._cos_doc.register_object(form)
        invocation = b"q /Fm0 Do Q\n"
        if wrap:
            invocation = wrap_artifact(invocation)
        engine._append_content_to_page(0, invocation)


def test_a_stamped_tagged_document_survives_a_round_trip(tmp_path):
    target = tmp_path / "stamped.pdf"
    document, page = _page()
    page.add_text("The report", 60, 740, tag="H1")
    page.add_text("Its body.", 60, 700, tag="P")
    document.convert_to_pdfua(title="The report")
    document.add_stamp(TextStamp(value="CONFIDENTIAL", opacity=0.2))
    document.add_stamp(
        PageNumberStamp(value="{page} / {total}", vertical_alignment="Bottom")
    )
    document.save(target)
    document.close()

    with Document(target) as reopened:
        result = reopened.validate_pdfua()
        assert result.is_valid, result.errors
        assert not [
            w for w in result.warnings if "neither tagged nor an artifact" in w
        ]
        content = reopened.pages[0].content
    assert content.count(b"/Artifact") == 2
    assert b"/Subtype /Watermark" in content
    assert b"/Subtype /Footer" in content
