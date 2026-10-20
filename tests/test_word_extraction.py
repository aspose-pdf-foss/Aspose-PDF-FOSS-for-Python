"""Positioned words retain font metrics, transforms and source resource scopes."""

from __future__ import annotations

import io
import math
import weakref
import zlib
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException, TextWord
from aspose_pdf.engine import word_extraction
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@contextmanager
def _content(content):
    with Document() as authored:
        authored.pages.add().add_text("Seed", 72, 700, font_size=10)
        with Document(_bytes(authored)) as document:
            engine = document._engine_pdf
            resources = engine._resolve_resources_cos(engine._get_page_dict(0))
            resources.mapping[PdfName("Font")] = PdfDictionary(
                {
                    PdfName("F1"): PdfDictionary(
                        {
                            PdfName("Type"): PdfName("Font"),
                            PdfName("Subtype"): PdfName("Type1"),
                            PdfName("BaseFont"): PdfName("Courier"),
                            PdfName("FirstChar"): PdfNumber(0),
                            PdfName("Widths"): PdfArray(
                                [PdfNumber(600) for _ in range(256)]
                            ),
                        }
                    )
                }
            )
            engine._set_page_content(0, content)
            yield document


def _texts(words):
    return [word.text for word in words]


def _form(document, name, content, *, matrix=None, resources=None):
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    scope = engine._resolve_resources_cos(page)
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(value) for value in (0, 0, 600, 800)]),
        PdfName("Resources"): scope if resources is None else resources,
    }
    if matrix is not None:
        mapping[PdfName("Matrix")] = PdfArray([PdfNumber(value) for value in matrix])
    form = PdfStream(content, mapping)
    reference = engine._cos_doc.register_object(form)
    xobjects = scope.mapping.setdefault(PdfName("XObject"), PdfDictionary({}))
    xobjects.mapping[PdfName(name)] = reference
    return form, reference


@pytest.mark.parametrize("mode", ["live", "bytes", "file", "streaming"])
def test_public_api_gives_words_boxes_and_detached_snapshots(mode, tmp_path):
    with _content(b"BT /F1 10 Tf 72 700 Td (Hello world!) Tj ET") as source:
        before = _bytes(source)
        if mode == "live":
            document = source
        elif mode == "bytes":
            document = Document(before)
        else:
            path = tmp_path / "words.pdf"
            path.write_bytes(before)
            document = (Document.open_streaming if mode == "streaming" else Document)(
                path
            )
        try:
            words = document.extract_words()
            assert _texts(words) == ["Hello", "world!"]
            assert all(
                isinstance(word, TextWord) and word.page_index == 0 for word in words
            )
            assert words[0].rect == pytest.approx((72, 698, 102, 708))
            assert words[1].bbox == pytest.approx((108, 698, 144, 708))
            assert words[0].quad == ((72, 698), (102, 698), (102, 708), (72, 708))
            assert document.pages[0].extract_words() == words
            assert _bytes(document) == before
        finally:
            if document is not source:
                document.dispose()
    assert words[0].text == "Hello"
    with pytest.raises(FrozenInstanceError):
        words[0].text = "Changed"
    with pytest.raises(TypeError):
        words[0].quad[0][0] = 0


@pytest.mark.parametrize(
    "content,expected",
    [
        (
            b"BT /F1 10 Tf 10 100 Td [(Hel) 20 (lo) -300 (world)] TJ ET",
            ["Hello", "world"],
        ),
        (b"BT /F1 10 Tf 10 100 Td (Hel) Tj (lo) Tj ET", ["Hello"]),
        (b"BT /F1 10 Tf 10 100 Td (Big) Tj /F1 5 Tf (small) Tj ET", ["Bigsmall"]),
        (b"BT /F1 10 Tf 10 100 Td (Left) Tj 100 0 Td (Right) Tj ET", ["Left", "Right"]),
        (
            b"BT /F1 10 Tf 10 100 Td 12 TL (One) Tj (Two) ' 1 0 (Three) \" ET",
            ["One", "Two", "Three"],
        ),
        (b"BT /F1 10 Tf 10 100 Td (a  b\t c!) Tj ET", ["a", "b", "c!"]),
        (b"BT /Missing 10 Tf 10 100 Td (Unmeasurable) Tj ET", []),
        (b"BT /F1 10 Tf 10 100 Td (Before) Tj /Missing 10 Tf (After) Tj ET", []),
        (b"BT /F1 10 Tf 10 100 Td <feff00410042> Tj ET", []),
    ],
)
def test_word_boundaries_follow_whitespace_and_measured_show_runs(content, expected):
    with _content(content) as document:
        assert _texts(document.extract_words()) == expected


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_page_rotation_and_crop_do_not_change_unrotated_word_coordinates(rotation):
    with _content(b"BT /F1 10 Tf 72 700 Td (Word) Tj ET") as document:
        page = document.pages[0]
        page.rotation = rotation
        page.crop_box = (0, 0, 200, 200)
        (word,) = page.extract_words()
        assert word.rect == (72, 698, 96, 708)
        assert page.rotation == rotation and page.crop_box == (0, 0, 200, 200)


@pytest.mark.parametrize(
    "matrix,expected",
    [
        ((1, 0, 0, 1, 0, 0), ((10, 98), (22, 98), (22, 108), (10, 108))),
        ((0, 1, -1, 0, 200, 0), ((102, 10), (102, 22), (92, 22), (92, 10))),
        ((-1, 0, 0, 1, 100, 0), ((90, 98), (78, 98), (78, 108), (90, 108))),
        ((2, 0.5, 0.25, 1, 5, 7), ((49.5, 110), (73.5, 116), (76, 126), (52, 120))),
    ],
)
def test_text_and_graphics_transforms_preserve_word_quads(matrix, expected):
    prefix = " ".join(str(value) for value in matrix).encode() + b" cm "
    with _content(prefix + b"BT /F1 10 Tf 10 100 Td (AB) Tj ET") as document:
        (word,) = document.extract_words()
        assert word.quad == expected
        xs, ys = zip(*expected)
        assert word.rect == (min(xs), min(ys), max(xs), max(ys))


def test_word_clip_selects_complete_words_and_excludes_boundary_only_touches():
    with _content(b"BT /F1 10 Tf 72 700 Td (Hello world!) Tj ET") as document:
        (word,) = document.extract_words(clip=(90, 699, 95, 701))
        assert word.text == "Hello" and word.rect == (72, 698, 102, 708)
        assert document.pages[0].extract_words(clip=(102, 698, 108, 708)) == []
        assert _texts(document.extract_words(clip=(102, 699, 109, 701))) == ["world!"]


def test_form_invocations_have_separate_transformed_word_positions():
    with _content(
        b"q 1 0 0 1 10 20 cm /Fm Do Q q 1 0 0 1 100 200 cm /Fm Do Q"
    ) as document:
        _form(
            document,
            "Fm",
            b"BT /F1 10 Tf 10 100 Td (Same) Tj ET",
            matrix=(1, 0, 0, 1, 5, 7),
        )
        words = document.extract_words()
        assert _texts(words) == ["Same", "Same"]
        assert words[0].rect == (25, 125, 49, 135)
        assert words[1].rect == (115, 305, 139, 315)


def test_selected_pages_keep_order_repeats_and_original_page_indices():
    with Document() as document:
        for index in range(3):
            document.pages.add().add_text(f"Page{index}", 20, 100)
        words = document.extract_words(pages=(index for index in (2, 0, 2)))
        assert [(word.page_index, word.text) for word in words] == [
            (2, "Page2"),
            (0, "Page0"),
            (2, "Page2"),
        ]
        assert document.pages[1].extract_words()[0].page_index == 1
        assert document.extract_words(pages=[]) == []


@pytest.mark.parametrize(
    "selection", [True, 1, "0", b"0", [True], [0.0], [None], [-1], [0, 5]]
)
def test_invalid_selection_is_checked_before_reading_any_page(selection, monkeypatch):
    with Document() as document:
        document.pages.add()

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid selections must not decode content")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", forbidden)
        with pytest.raises(PdfValidationException):
            document.extract_words(pages=selection)


@pytest.mark.parametrize(
    "clip",
    [
        True,
        [],
        "box",
        [0, 0, 1],
        [0, 0, True, 1],
        [0, 0, 0, 1],
        [1, 0, 0, 1],
        [0, 0, math.inf, 1],
    ],
)
def test_invalid_clip_is_checked_even_for_empty_input(clip):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="clip"):
            document.extract_words(clip=clip)


@pytest.mark.parametrize("value", [0, 1, None, "yes"])
def test_skip_artifacts_requires_a_boolean(value):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="skip_artifacts"):
            document.extract_words(skip_artifacts=value)


def test_empty_and_disposed_documents_and_stale_pages():
    with Document() as document:
        assert document.extract_words() == []
        page = document.pages.add()
        assert page.extract_words() == []
        document.pages.delete(0)
        with pytest.raises(PdfValidationException, match="out of range"):
            page.extract_words()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.extract_words()


def test_unicode_composite_fonts_keep_decoded_words_and_finite_bounds():
    with Document() as document:
        page = document.pages.add()
        page.add_text(
            "café Ångström", 20, 100, font=load_substitute_sfnt("sans-regular")
        )
        with Document(_bytes(document)) as loaded:
            words = loaded.extract_words()
            assert _texts(words) == ["café", "Ångström"]
            assert all(
                word.rect[0] < word.rect[2] and word.rect[1] < word.rect[3]
                for word in words
            )


def test_one_glyph_with_multiple_unicode_characters_uses_the_whole_glyph_box():
    with _content(b"BT /F1 10 Tf 10 100 Td (A B) Tj ET") as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        font = engine._resolve(resources.mapping[PdfName("Font")]).mapping[
            PdfName("F1")
        ]
        font.mapping[PdfName("ToUnicode")] = engine._cos_doc.register_object(
            PdfStream(
                b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
                b"1 beginbfrange <20> <7e> <0020> endbfrange "
                b"1 beginbfchar <41> <006600660069> endbfchar endcmap",
                {},
            )
        )
        with Document(_bytes(document)) as loaded:
            words = loaded.extract_words()
            assert _texts(words) == ["ffi", "B"]
            assert words[0].rect == (10, 98, 16, 108)
            assert words[1].rect == (22, 98, 28, 108)


def test_form_resource_scope_overrides_same_named_page_font():
    with _content(b"/Fm Do BT /F1 10 Tf 10 100 Td (A) Tj ET") as document:
        resources = PdfDictionary(
            {
                PdfName("Font"): PdfDictionary(
                    {
                        PdfName("F1"): PdfDictionary(
                            {
                                PdfName("Type"): PdfName("Font"),
                                PdfName("Subtype"): PdfName("Type1"),
                                PdfName("BaseFont"): PdfName("Courier"),
                                PdfName("FirstChar"): PdfNumber(0),
                                PdfName("Widths"): PdfArray(
                                    [PdfNumber(1000) for _ in range(256)]
                                ),
                            }
                        )
                    }
                )
            }
        )
        _form(document, "Fm", b"BT /F1 10 Tf 10 100 Td (A) Tj ET", resources=resources)
        words = document.extract_words()
        assert _texts(words) == ["A", "A"]
        assert words[0].rect == (10, 98, 20, 108)
        assert words[1].rect == (10, 98, 16, 108)


def test_hidden_layers_artifacts_and_invisible_text_have_explicit_semantics():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Body", 20, 100)
        page.add_text("Header", 20, 150, artifact=True)
        hidden = document.layers.add("Hidden", visible=False)
        with page.layer(hidden):
            page.add_text("Secret", 20, 200)
        assert _texts(page.extract_words()) == ["Body", "Header"]
        assert _texts(page.extract_words(skip_artifacts=True)) == ["Body"]
        hidden.visible = True
        assert _texts(page.extract_words(skip_artifacts=True)) == ["Body", "Secret"]
    with _content(b"BT /F1 10 Tf 3 Tr 10 100 Td (OCR words) Tj ET") as document:
        assert _texts(document.extract_words()) == ["OCR", "words"]


def test_actual_text_replacements_are_not_given_guessed_word_positions():
    with _content(
        b"BT /F1 10 Tf 10 100 Td (Before) Tj /Span /Logical BDC "
        b"(Underlying) Tj EMC (After) Tj ET"
    ) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("Properties")] = PdfDictionary(
            {
                PdfName("Logical"): PdfDictionary(
                    {PdfName("ActualText"): PdfString(b"Logical replacement")}
                )
            }
        )
        assert "Logical replacement" in document.pages[0].extract_text()
        assert _texts(document.extract_words()) == ["Before", "After"]


@pytest.mark.parametrize("key", [b"ActualText", b"#41ctualText"])
def test_inline_actual_text_does_not_expose_underlying_glyphs_as_positioned_words(key):
    with _content(
        b"BT /F1 10 Tf 10 100 Td (Before) Tj /Span << /"
        + key
        + b" (Logical replacement) >> BDC (Underlying) Tj EMC (After) Tj ET"
    ) as document:
        assert _texts(document.extract_words()) == ["Before", "After"]


def test_streaming_reads_selected_content_and_never_images_or_unused_forms(
    tmp_path, monkeypatch
):
    path = tmp_path / "lazy.pdf"
    with _content(b"BT /F1 10 Tf 10 100 Td (Words) Tj ET") as source:
        _form(source, "Unused", b" " * 2000)
        engine = source._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        image = engine._cos_doc.register_object(
            PdfStream(
                zlib.compress(b"\xff" * 20000),
                {
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Width"): PdfNumber(1),
                    PdfName("Height"): PdfNumber(1),
                    PdfName("BitsPerComponent"): PdfNumber(8),
                    PdfName("ColorSpace"): PdfName("DeviceGray"),
                    PdfName("Filter"): PdfName("FlateDecode"),
                },
            )
        )
        resources.mapping[PdfName("XObject")].mapping[PdfName("Im")] = image
        engine._set_page_content(0, source.pages[0].content + b"\n/Im Do")
        source.pages.add().add_text("Unselected", 20, 100)
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:
        engine = document._engine_pdf
        original = engine.get_page_content
        visited = []

        def content(index):
            assert index == 0
            visited.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", content)
        assert _texts(document.extract_words(pages=[0])) == ["Words"]
        assert visited == [0]
        assert engine.page_contents == [] and engine._extracted_text is None
    assert path.read_bytes() == before


def test_content_and_selection_limits_fail_without_partial_results(
    tmp_path, monkeypatch
):
    path = tmp_path / "limited.pdf"
    with _content(b"BT /F1 10 Tf 10 100 Td (A B) Tj ET") as source:
        source.pages.add(((0, 0, 100, 100), b"q Q\n" * 100))
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=100)
    ) as document:
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            document.extract_words()
        assert document._engine_pdf.page_contents == []
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        for _ in range(3):
            document.pages.add()

        def forbidden(*args):
            pytest.fail("Oversized selection must not decode any page")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", forbidden)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.extract_words()


@pytest.mark.parametrize("streaming", [False, True])
def test_encrypted_sources_keep_data_protection_and_text_caches(tmp_path, streaming):
    path = tmp_path / "encrypted.pdf"
    with Document() as source:
        for index in range(2):
            source.pages.add().add_text(f"Word{index}", 20, 100)
        source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret") as document:
        protected = document.is_encrypted
        document._engine_pdf._page_text_cursor = 1
        words = document.extract_words(pages=[1])
        assert _texts(words) == ["Word1"] and words[0].page_index == 1
        assert document.is_encrypted == protected
        assert document._engine_pdf._page_text_cursor == 1
        assert document._engine_pdf._extracted_text is None
    assert path.read_bytes() == before


def test_word_constructor_snapshots_mutable_coordinate_inputs():
    rect = [0, 0, 10, 10]
    quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
    word = TextWord(0, "Word", rect, quad)
    rect[0] = -10
    quad[0][0] = -10
    assert word.rect == (0, 0, 10, 10)
    assert word.quad == ((0, 0), (10, 0), (10, 10), (0, 10))


@pytest.mark.parametrize(
    "changes",
    [
        {"page_index": -1},
        {"page_index": True},
        {"page_index": 1.0},
        {"text": ""},
        {"text": "two words"},
        {"text": None},
        {"rect": [0, 0, 1]},
        {"rect": [0, 0, math.inf, 1]},
        {"rect": [0, 0, True, 1]},
        {"quad": [[0, 0]]},
        {"quad": [[0, 0], [1, 0], [1, 1], [0, math.nan]]},
        {"rect": [0, 0, 2, 2]},
    ],
)
def test_word_constructor_rejects_invalid_snapshots(changes):
    values = dict(
        page_index=0,
        text="Word",
        rect=(0, 0, 1, 1),
        quad=((0, 0), (1, 0), (1, 1), (0, 1)),
    )
    values.update(changes)
    with pytest.raises(PdfValidationException):
        TextWord(**values)


@pytest.mark.parametrize("vertical", [False, True])
def test_type0_unicode_words_use_cid_advances_in_both_writing_modes(vertical):
    value = "Привет Ω"
    mapping = b" ".join(
        f"<{index:04x}> <{character.encode('utf-16-be').hex()}>".encode()
        for index, character in enumerate(value, 1)
    )
    codes = b"".join(index.to_bytes(2, "big") for index in range(1, len(value) + 1))
    with _content(
        b"BT /F1 10 Tf 10 100 Td <" + codes.hex().encode() + b"> Tj ET"
    ) as document:
        engine = document._engine_pdf
        cmap = engine._cos_doc.register_object(
            PdfStream(
                b"begincmap 1 begincodespacerange <0000> <ffff> endcodespacerange "
                + str(len(value)).encode()
                + b" beginbfchar "
                + mapping
                + b" endbfchar endcmap",
                {},
            )
        )
        cidfont = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType2"),
                PdfName("BaseFont"): PdfName("Fixture"),
                PdfName("DW"): PdfNumber(600),
                PdfName("CIDSystemInfo"): PdfDictionary(
                    {
                        PdfName("Registry"): PdfString(b"Adobe"),
                        PdfName("Ordering"): PdfString(b"Identity"),
                        PdfName("Supplement"): PdfNumber(0),
                    }
                ),
            }
        )
        font = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type0"),
                PdfName("BaseFont"): PdfName("Fixture"),
                PdfName("Encoding"): PdfName(
                    "Identity-V" if vertical else "Identity-H"
                ),
                PdfName("ToUnicode"): cmap,
                PdfName("DescendantFonts"): PdfArray([cidfont]),
            }
        )
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("Font")] = PdfDictionary({PdfName("F1"): font})
        with Document(_bytes(document)) as loaded:
            words = loaded.extract_words()
            assert _texts(words) == ["Привет", "Ω"]
            assert all(
                math.isfinite(coordinate) for word in words for coordinate in word.rect
            )
            if not vertical:
                assert words[0].rect == (10, 98, 46, 108)
                assert words[1].rect == (52, 98, 58, 108)
            else:
                assert words[0].rect[1] > words[1].rect[1]


def test_recursive_forms_terminate_and_nested_artifact_filters_are_preserved():
    with _content(b"/Fm Do") as document:
        _form(
            document,
            "Fm",
            b"BT /F1 10 Tf 10 100 Td (Body) Tj ET "
            b"/Artifact BMC BT /F1 10 Tf 10 120 Td (Header) Tj ET EMC /Fm Do",
        )
        assert _texts(document.extract_words()) == ["Body", "Header"]
        assert _texts(document.extract_words(skip_artifacts=True)) == ["Body"]


def test_word_text_limit_applies_to_words_joined_across_many_operands():
    with _content(b"BT /F1 10 Tf 10 100 Td " + b"(x) Tj " * 20 + b"ET") as document:
        document._engine_pdf._load_budget.limits = PdfLoadLimits(max_object_bytes=8)
        with pytest.raises(
            PdfResourceLimitException, match=r"word text.*max_object_bytes"
        ):
            document.extract_words()


def test_form_inside_an_actual_text_replacement_is_not_decoded(tmp_path):
    path = tmp_path / "logical-form.pdf"
    with _content(
        b"/Span << /ActualText (Logical) >> BDC /Fm Do EMC "
        b"BT /F1 10 Tf 10 100 Td (Body) Tj ET"
    ) as source:
        _form(source, "Fm", b" " * 20000)
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:
        assert _texts(document.extract_words()) == ["Body"]


def test_page_parsers_are_released_before_reading_the_next_page(monkeypatch):
    references = []
    original = word_extraction._WordParser.__init__

    def initialize(parser, *args, **kwargs):
        assert all(reference() is None for reference in references)
        original(parser, *args, **kwargs)
        references.append(weakref.ref(parser))

    with Document() as document:
        for index in range(3):
            document.pages.add().add_text(f"Page{index}", 20, 100)
        monkeypatch.setattr(word_extraction._WordParser, "__init__", initialize)
        assert _texts(document.extract_words()) == ["Page0", "Page1", "Page2"]
        assert all(reference() is None for reference in references)
