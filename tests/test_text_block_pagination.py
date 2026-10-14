"""Page-breaking controls preserve text and honour feasible layout constraints."""

import re
from io import BytesIO

import pytest

from aspose_pdf import Document, Paragraph, TextBlock
from aspose_pdf.exceptions import PdfValidationException


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(100, 100))
        yield doc


def _lines(prefix, count):
    return [f"{prefix}{index}" for index in range(count)]


def _paragraph(block, lines, **options):
    block.add_paragraph("\n".join(lines), **options)


def _block():
    return TextBlock(width=80, font_size=6, line_height=10)


def _assert_flow(document, result, expected, *, bottom=20, top=80):
    pages = [page.extract_text().splitlines() for page in document.pages]
    assert [line for page in pages for line in page] == expected
    assert result["lines"] == len(expected)
    assert result["pages"] == [i for i, page in enumerate(pages) if page]
    for page in document.pages:
        baselines = [
            float(match.group(1))
            for match in re.finditer(rb"1 0 0 1 [-\d.]+ ([-\d.]+) Tm", page.content)
        ]
        assert all(bottom <= baseline <= top for baseline in baselines)
    return pages


def test_widow_constraint_moves_a_small_fragment_to_the_next_page(document):
    block = _block()
    filler, body = _lines("F", 5), _lines("L", 5)
    _paragraph(block, filler)
    _paragraph(block, body, widow_lines=2)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, filler + body) == [filler, body]


def test_widow_constraint_never_duplicates_a_paragraph(document):
    block = _block()
    filler, body = _lines("F", 3), _lines("L", 5)
    _paragraph(block, filler)
    _paragraph(block, body, widow_lines=3)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, filler + body) == [
        filler + body[:3],
        body[3:],
    ]


@pytest.mark.parametrize(
    "count,sizes", [(7, [5, 2]), (13, [6, 5, 2]), (19, [6, 6, 5, 2])]
)
def test_orphan_constraint_rebalances_every_page_break(document, count, sizes):
    block = _block()
    body = _lines("L", count)
    _paragraph(block, body, orphan_lines=2)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, body)
    assert [len(page) for page in pages] == sizes


def test_orphan_constraint_reserves_a_full_last_fragment(document):
    block = _block()
    body = _lines("L", 10)
    _paragraph(block, ["F"])
    _paragraph(block, body, orphan_lines=6)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, ["F", *body]) == [["F", *body[:4]], body[4:]]


def test_combined_line_minimums_move_an_unsplittable_paragraph(document):
    block = _block()
    filler, body = _lines("F", 3), _lines("L", 5)
    _paragraph(block, filler)
    _paragraph(block, body, widow_lines=3, orphan_lines=3)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, filler + body) == [filler, body]


@pytest.mark.parametrize("before", [1, 2, 3])
@pytest.mark.parametrize("after", [1, 2, 3])
@pytest.mark.parametrize("count", [7, 13, 19])
@pytest.mark.parametrize("filler_count", [0, 4])
def test_all_feasible_fragment_minimums_are_respected(
    document, before, after, count, filler_count
):
    block = _block()
    filler, body = _lines("F", filler_count), _lines("L", count)
    if filler:
        _paragraph(block, filler)
    _paragraph(block, body, widow_lines=before, orphan_lines=after)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, filler + body)
    fragments = [sum(line.startswith("L") for line in page) for page in pages]
    fragments = [count for count in fragments if count]
    assert all(count >= before for count in fragments[:-1])
    assert all(count >= after for count in fragments[1:])


@pytest.mark.parametrize("keep_together", [False, True])
def test_impossible_minimums_relax_without_overflow_or_losing_text(
    document, keep_together
):
    block = _block()
    body = _lines("L", 10)
    _paragraph(block, ["F"])
    _paragraph(
        block, body, widow_lines=100, orphan_lines=100, keep_together=keep_together
    )
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, ["F", *body])
    assert all(len(page) <= 6 for page in pages)


def test_an_oversized_keep_together_paragraph_splits_safely(document):
    block = _block()
    body = _lines("L", 10)
    _paragraph(block, ["F"])
    _paragraph(block, body, keep_together=True)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, ["F", *body]) == [["F"], body[:6], body[6:]]


def test_an_oversized_first_paragraph_can_still_flow(document):
    block = _block()
    body = _lines("L", 10)
    _paragraph(block, body, keep_together=True)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, body) == [body[:6], body[6:]]


def test_default_spacing_is_not_repeated_on_continuation_pages(document):
    block = _block()
    body = _lines("L", 10)
    _paragraph(block, ["F"])
    _paragraph(block, body, space_before=10)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, ["F", *body]) == [["F", *body[:4]], body[4:]]
    assert _text_baselines(document.pages[1])[0] == pytest.approx(72.4)


def _text_baselines(page):
    return [
        float(match.group(1))
        for match in re.finditer(rb"1 0 0 1 [-\d.]+ ([-\d.]+) Tm", page.content)
    ]


@pytest.mark.parametrize("keep_together", [False, True])
def test_keep_with_next_includes_paragraph_spacing(document, keep_together):
    block = _block()
    filler = _lines("F", 2)
    _paragraph(block, filler)
    _paragraph(
        block,
        ["Heading"],
        keep_with_next=True,
        keep_together=keep_together,
        space_after=30,
    )
    _paragraph(block, ["Body"])
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, [*filler, "Heading", "Body"]) == [
        filler,
        ["Heading", "Body"],
    ]
    assert _text_baselines(document.pages[1]) == pytest.approx([72.4, 32.4])


def test_keep_with_next_moves_a_whole_chain(document):
    block = _block()
    filler, first, last = _lines("F", 3), _lines("A", 2), _lines("C", 2)
    _paragraph(block, filler)
    _paragraph(block, first, keep_with_next=True)
    _paragraph(block, ["B"], keep_with_next=True)
    _paragraph(block, last)
    chain = [*first, "B", *last]
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, filler + chain) == [filler, chain]


def test_keep_with_next_includes_gaps_and_spacing_before_the_next_paragraph(document):
    block = _block()
    block.paragraph_gap = 10
    filler = _lines("F", 2)
    _paragraph(block, filler)
    _paragraph(block, ["Heading"], keep_with_next=True, space_after=10)
    _paragraph(block, ["Body"], space_before=5)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, [*filler, "Heading", "Body"]) == [
        filler,
        ["Heading", "Body"],
    ]
    assert _text_baselines(document.pages[1]) == pytest.approx([72.4, 37.4])


def test_keep_with_next_works_together_with_keep_together(document):
    block = _block()
    filler = _lines("F", 5)
    _paragraph(block, filler)
    _paragraph(block, ["Heading"], keep_with_next=True, keep_together=True)
    _paragraph(block, ["Body"])
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, [*filler, "Heading", "Body"]) == [
        filler,
        ["Heading", "Body"],
    ]


def test_keep_with_next_keeps_a_heading_with_a_long_body(document):
    block = _block()
    filler, body = _lines("F", 5), _lines("L", 13)
    _paragraph(block, filler)
    _paragraph(block, ["Heading"], keep_with_next=True)
    _paragraph(block, body, orphan_lines=2)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, [*filler, "Heading", *body])
    assert pages[0] == filler
    assert pages[1][:2] == ["Heading", "L0"]
    assert len(pages[-1]) >= 2


def test_keep_with_next_binds_the_tail_of_a_long_paragraph(document):
    block = _block()
    body = _lines("L", 12)
    _paragraph(block, body, keep_with_next=True)
    _paragraph(block, ["Body"])
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, [*body, "Body"])
    assert pages[-1] == ["L11", "Body"]


def test_an_oversized_chain_can_still_keep_its_headings_with_body_text(document):
    block = _block()
    filler, body = _lines("F", 4), _lines("L", 13)
    _paragraph(block, filler)
    _paragraph(block, ["A"], keep_with_next=True)
    _paragraph(block, ["B0", "B1"], keep_with_next=True)
    _paragraph(block, body)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = _assert_flow(document, result, [*filler, "A", "B0", "B1", *body])
    assert pages[0] == filler
    assert pages[1][:4] == ["A", "B0", "B1", "L0"]


def test_keep_with_next_on_the_last_paragraph_has_no_effect(document):
    block = _block()
    body = _lines("L", 7)
    _paragraph(block, body, keep_with_next=True)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert _assert_flow(document, result, body) == [body[:6], body[6:]]


def test_different_existing_page_heights_are_respected(document):
    document.pages.add(size=(100, 70))
    document.pages.add(size=(100, 130))
    block = _block()
    body = _lines("L", 13)
    _paragraph(block, body, orphan_lines=2)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    pages = [page.extract_text().splitlines() for page in document.pages]
    assert pages == [body[:6], body[6:9], body[9:]]
    assert result["lines"] == 13
    for page in document.pages:
        assert all(
            20 <= baseline <= page.media_box[3] - 20
            for baseline in _text_baselines(page)
        )


def test_a_line_taller_than_the_available_page_is_refused(document):
    block = TextBlock("Too tall", width=80, font_size=6, line_height=70)
    with pytest.raises(PdfValidationException, match="does not fit"):
        document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)


def test_an_impossible_line_on_a_short_existing_page_is_refused(document):
    document.pages.add(size=(100, 45))
    block = _block()
    _paragraph(block, _lines("L", 7))
    with pytest.raises(PdfValidationException, match="does not fit"):
        document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)


def test_nonzero_media_box_origin_and_bottom_margin(document):
    page = document.pages[0]
    page.media_box = (-10, 10, 90, 110)
    block = _block()
    body = _lines("L", 7)
    _paragraph(block, body, orphan_lines=2)
    result = page.add_text_block(block, 0, 90, bottom_margin=30)
    assert _assert_flow(document, result, body, bottom=30, top=90) == [
        body[:5],
        body[5:],
    ]


def test_empty_hard_break_lines_still_count_toward_page_geometry(document):
    block = _block()
    _paragraph(block, _lines("F", 4))
    block.add_paragraph(Paragraph("L0\n\nL1", widow_lines=2))
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    assert result["lines"] == 7
    assert document.pages[0].extract_text().splitlines() == [*_lines("F", 4), "L0"]
    assert document.pages[1].extract_text() == "L1"
    assert all(20 <= y <= 80 for page in document.pages for y in _text_baselines(page))


@pytest.mark.parametrize("source_kind", ["bytes", "file"])
def test_controlled_tagged_paragraphs_survive_pdf_round_trips(
    document, source_kind, tmp_path
):
    block = _block()
    body = _lines("L", 13)
    _paragraph(block, ["Heading"], keep_with_next=True, tag="H2")
    _paragraph(block, body, widow_lines=2, orphan_lines=2)
    result = document.pages[0].add_text_block(block, 10, 80, bottom_margin=20)
    expected = _assert_flow(document, result, ["Heading", *body])
    if source_kind == "bytes":
        buffer = BytesIO()
        document.save(buffer)
        source = buffer.getvalue()
    else:
        source = tmp_path / "controlled.pdf"
        document.save(source)
    with Document(source) as loaded:
        assert [page.extract_text().splitlines() for page in loaded.pages] == expected


@pytest.mark.parametrize("name", ["widow_lines", "orphan_lines"])
@pytest.mark.parametrize(
    "value", [0, -1, 2.5, True, False, "bad", float("nan"), float("inf"), 10**400]
)
def test_invalid_line_counts_raise_a_clear_exception(name, value):
    with pytest.raises(PdfValidationException, match=name):
        Paragraph("Text", **{name: value})


@pytest.mark.parametrize("name", ["widow_lines", "orphan_lines"])
def test_line_count_defaults_and_normalization(name):
    assert getattr(Paragraph("Text"), name) is None
    assert getattr(Paragraph("Text", **{name: 2.0}), name) == 2
