"""Predefined Adobe CJK CMaps without a font-level ToUnicode map."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pytest

from aspose_pdf import (
    Document,
    PdfExtractor,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.content_stream_parser import load_cid_vertical_metrics
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.predefined_cmaps import (
    CharacterCollection,
    _resolve_cached,
    character_collection,
    resolve_predefined_cmap,
    resolve_predefined_cmap_encoding,
    supported_cmap_names,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_edit import (
    CidTextCodec,
    _string_advance,
    predefined_cmap_codec,
)
from aspose_pdf.engine.text_locate import locate_matches
from aspose_pdf.exceptions import PdfValidationException


@dataclass(frozen=True)
class CMapCase:
    name: str
    ordering: str
    source: str
    source_hex: str
    cids: tuple[int, int]
    replacement: str
    replacement_hex: str


MODERN_CASES = (
    CMapCase(
        "UniJIS-UTF16-H",
        "Japan1",
        "日本",
        "65E5672C",
        (3284, 3722),
        "本日",
        "672C65E5",
    ),
    CMapCase(
        "UniKS-UTF16-H",
        "Korea1",
        "한국",
        "D55CAD6D",
        (3296, 1204),
        "국한",
        "AD6DD55C",
    ),
    CMapCase(
        "UniGB-UTF16-H",
        "GB1",
        "中国",
        "4E2D56FD",
        (4559, 1875),
        "国中",
        "56FD4E2D",
    ),
    CMapCase(
        "UniCNS-UTF16-H",
        "CNS1",
        "中國",
        "4E2D570B",
        (661, 2615),
        "國中",
        "570B4E2D",
    ),
)

LEGACY_CASES = (
    CMapCase(
        "90ms-RKSJ-H",
        "Japan1",
        "日本",
        "93FA967B",
        (3284, 3722),
        "本日",
        "967B93FA",
    ),
    CMapCase(
        "KSCms-UHC-H",
        "Korea1",
        "한국",
        "C7D1B1B9",
        (3296, 1204),
        "국한",
        "B1B9C7D1",
    ),
    CMapCase(
        "GBK-EUC-H",
        "GB1",
        "中国",
        "D6D0B9FA",
        (4559, 1875),
        "国中",
        "B9FAD6D0",
    ),
    CMapCase(
        "ETen-B5-H",
        "CNS1",
        "中國",
        "A4A4B0EA",
        (661, 2615),
        "國中",
        "B0EAA4A4",
    ),
)

VERTICAL_CASES = (
    ("UniJIS-UTF16-V", "Japan1", "30083009", (7907, 7908)),
    ("UniKS-UTF16-V", "Korea1", "30083009", (8065, 8066)),
    ("UniGB-UTF16-V", "GB1", "30083009", (584, 585)),
    ("UniCNS-UTF16-V", "CNS1", "30083009", (150, 151)),
    ("90ms-RKSJ-V", "Japan1", "81718172", (7907, 7908)),
    ("KSCms-UHC-V", "Korea1", "A1B4A1B5", (8065, 8066)),
    ("GBK-EUC-V", "GB1", "A1B4A1B5", (584, 585)),
    ("ETen-B5-V", "CNS1", "A171A172", (150, 151)),
)

VERTICAL_REPLACEMENT_CASES = tuple(
    (case.name.removesuffix("-H") + "-V", case)
    for case in MODERN_CASES + LEGACY_CASES
)

EXPECTED_CMAP_NAMES = tuple(
    sorted(
        [case.name for case in MODERN_CASES + LEGACY_CASES]
        + [case[0] for case in VERTICAL_CASES]
    )
)


def _pdf_bytes(
    *,
    cmap_name: str,
    ordering: str,
    shown_hex: str,
    cids: tuple[int, int],
    vertical: bool = False,
    cid_subtype: str = "CIDFontType0",
    to_unicode: bytes | None = None,
) -> bytes:
    content = f"BT /F1 12 Tf 20 80 Td <{shown_hex}> Tj ET".encode("ascii")
    pdf = SimplePdf(
        pages=[(0.0, 0.0, 300.0, 120.0)],
        page_contents=[content],
    )
    pdf._ensure_cos()
    cos = pdf._cos_doc

    widths = PdfArray(
        [
            PdfNumber(cids[0]),
            PdfArray([PdfNumber(400)]),
            PdfNumber(cids[1]),
            PdfArray([PdfNumber(600)]),
        ]
    )
    cid_mapping = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName(cid_subtype),
        PdfName("BaseFont"): PdfName("FixtureCID"),
        PdfName("CIDSystemInfo"): PdfDictionary(
            {
                PdfName("Registry"): PdfString(b"Adobe"),
                PdfName("Ordering"): PdfString(ordering.encode("ascii")),
                PdfName("Supplement"): PdfNumber(99),
            }
        ),
        PdfName("DW"): PdfNumber(1000),
        PdfName("W"): widths,
    }
    if vertical:
        cid_mapping[PdfName("DW2")] = PdfArray(
            [PdfNumber(880), PdfNumber(-1000)]
        )
        cid_mapping[PdfName("W2")] = PdfArray(
            [
                PdfNumber(cids[0]),
                PdfArray(
                    [PdfNumber(-500), PdfNumber(500), PdfNumber(880)]
                ),
                PdfNumber(cids[1]),
                PdfArray(
                    [PdfNumber(-700), PdfNumber(500), PdfNumber(880)]
                ),
            ]
        )
    cid_font = PdfDictionary(cid_mapping)
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("FixtureCID"),
            PdfName("Encoding"): PdfName(cmap_name),
            PdfName("DescendantFonts"): PdfArray(
                [cos.register_object(cid_font)]
            ),
        }
    )
    if to_unicode is not None:
        type0.mapping[PdfName("ToUnicode")] = cos.register_object(
            PdfStream(content=to_unicode, mapping={})
        )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = cos.register_object(type0)
    return pdf.to_bytes()


def _load(data: bytes) -> Document:
    document = Document()
    document.load_from(data)
    return document


def _save(document: Document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _extract(data: bytes) -> str:
    with PdfExtractor() as extractor:
        extractor.bind_pdf(data)
        extractor.extract_text()
        return extractor.get_text()


def _to_unicode(*pairs: tuple[str, str]) -> bytes:
    body = "\n".join(f"<{source}> <{target}>" for source, target in pairs)
    return (
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(pairs)} beginbfchar\n{body}\nendbfchar\n"
    ).encode("ascii")


@pytest.mark.parametrize("case", MODERN_CASES + LEGACY_CASES, ids=lambda c: c.name)
def test_predefined_horizontal_cmap_extracts_without_to_unicode(
    case: CMapCase,
) -> None:
    data = _pdf_bytes(
        cmap_name=case.name,
        ordering=case.ordering,
        shown_hex=case.source_hex,
        cids=case.cids,
    )

    assert b"/ToUnicode" not in data
    assert _extract(data) == case.source


# CMaps beyond the original eight-name allowlist, one representative per
# encoding family and collection. Codes and CIDs were derived from the pinned
# Adobe sources themselves (Unicode CMaps encoded forward, legacy CMaps through
# the collection's UCS-2 table), not from this library's own bundle.
EXPANDED_CASES = (
    CMapCase("UniJIS-UCS2-H", "Japan1", "日本", "65E5672C", (3284, 3722), "本日", "672C65E5"),
    CMapCase("UniJIS-UCS2-HW-H", "Japan1", "日本", "65E5672C", (3284, 3722), "本日", "672C65E5"),
    CMapCase("UniJIS-UTF8-H", "Japan1", "日本", "E697A5E69CAC", (3284, 3722), "本日", "E69CACE697A5"),
    CMapCase("UniJIS-UTF32-H", "Japan1", "日本", "000065E50000672C", (3284, 3722), "本日", "0000672C000065E5"),
    CMapCase("90pv-RKSJ-H", "Japan1", "日本", "93FA967B", (3284, 3722), "本日", "967B93FA"),
    CMapCase("EUC-H", "Japan1", "日本", "C6FCCBDC", (3284, 3722), "本日", "CBDCC6FC"),
    CMapCase("UniKS-UCS2-H", "Korea1", "한국", "D55CAD6D", (3296, 1204), "국한", "AD6DD55C"),
    CMapCase("KSC-EUC-H", "Korea1", "한국", "C7D1B1B9", (3296, 1204), "국한", "B1B9C7D1"),
    CMapCase("UniGB-UCS2-H", "GB1", "中国", "4E2D56FD", (4559, 1875), "国中", "56FD4E2D"),
    CMapCase("GB-EUC-H", "GB1", "中国", "D6D0B9FA", (4559, 1875), "国中", "B9FAD6D0"),
    CMapCase("GBK2K-H", "GB1", "中国", "D6D0B9FA", (4559, 1875), "国中", "B9FAD6D0"),
    CMapCase("UniCNS-UCS2-H", "CNS1", "中國", "4E2D570B", (661, 2615), "國中", "570B4E2D"),
    CMapCase("UniCNS-UTF8-H", "CNS1", "中國", "E4B8ADE59C8B", (661, 2615), "國中", "E59C8BE4B8AD"),
    CMapCase("B5pc-H", "CNS1", "中國", "A4A4B0EA", (661, 2615), "國中", "B0EAA4A4"),
    CMapCase("HKscs-B5-H", "CNS1", "中國", "A4A4B0EA", (661, 2615), "國中", "B0EAA4A4"),
)


@pytest.mark.parametrize("case", EXPANDED_CASES, ids=lambda c: c.name)
def test_expanded_cmap_extracts_without_tounicode(case: CMapCase) -> None:
    """Every newly bundled name yields exact text with no /ToUnicode present."""
    data = _pdf_bytes(
        cmap_name=case.name,
        ordering=case.ordering,
        shown_hex=case.source_hex,
        cids=case.cids,
    )
    assert b"/ToUnicode" not in data
    assert _extract(data) == case.source


@pytest.mark.parametrize("case", EXPANDED_CASES, ids=lambda c: c.name)
def test_expanded_cmap_replaces_and_round_trips(case: CMapCase) -> None:
    document = _load(
        _pdf_bytes(
            cmap_name=case.name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
        )
    )
    assert document.replace_text(case.source, case.replacement) == 1
    assert case.replacement_hex.encode("ascii") in document.pages[0].content
    assert _extract(_save(document)) == case.replacement


@pytest.mark.parametrize("case", EXPANDED_CASES, ids=lambda c: c.name)
def test_expanded_cmap_vertical_form_is_bundled(case: CMapCase) -> None:
    """Each expanded name's vertical twin resolves and reports WMode 1."""
    vertical_name = case.name.removesuffix("-H") + "-V"
    assert vertical_name in supported_cmap_names()
    resolved = resolve_predefined_cmap(
        vertical_name, CharacterCollection("Adobe", case.ordering, 0)
    )
    assert resolved is not None
    assert resolved.vertical is True


def test_supported_cmap_names_match_the_documented_allowlist() -> None:
    names = supported_cmap_names()
    # Every case this module exercises must be bundled.
    assert set(EXPECTED_CMAP_NAMES) <= set(names)
    # The allowlist is exactly the union the bundle index declares, so a data
    # rebuild that drops or renames a file is caught here.
    from aspose_pdf.engine.predefined_cmaps import _index

    declared = {
        name
        for entry in _index()["collections"].values()
        for name in entry["cmaps"]
    }
    assert set(names) == declared
    assert names == tuple(sorted(names))
    # The Adobe-<Ordering>-<N> CMaps map codes that already are CIDs rather than
    # an encoding, and are deliberately excluded.
    assert not [name for name in names if name.startswith("Adobe-")]


@pytest.mark.parametrize("case", MODERN_CASES + LEGACY_CASES, ids=lambda c: c.name)
def test_predefined_horizontal_cmap_replaces_and_round_trips(
    case: CMapCase,
) -> None:
    document = _load(
        _pdf_bytes(
            cmap_name=case.name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
        )
    )

    assert document.replace_text(case.source, case.replacement) == 1
    assert case.replacement_hex.encode("ascii") in document.pages[0].content
    assert _extract(_save(document)) == case.replacement


@pytest.mark.parametrize("case", MODERN_CASES, ids=lambda c: c.name)
def test_predefined_cmap_redaction_uses_cid_widths(case: CMapCase) -> None:
    document = _load(
        _pdf_bytes(
            cmap_name=case.name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
        )
    )

    assert document.redact_text(case.source, overlay=True) == 1
    content = document.pages[0].content
    assert case.source_hex.encode("ascii") not in content
    match = re.search(rb"([\d.]+) [\d.]+ m ([\d.]+) [\d.]+ l", content)
    assert match is not None
    assert float(match.group(2)) - float(match.group(1)) == pytest.approx(12.0)


@pytest.mark.parametrize(
    "name,ordering,shown_hex,cids",
    VERTICAL_CASES,
    ids=[case[0] for case in VERTICAL_CASES],
)
def test_predefined_vertical_cmap_inherits_base_and_uses_w2(
    name: str,
    ordering: str,
    shown_hex: str,
    cids: tuple[int, int],
) -> None:
    data = _pdf_bytes(
        cmap_name=name,
        ordering=ordering,
        shown_hex=shown_hex,
        cids=cids,
        vertical=True,
    )
    assert _extract(data) == "〈〉"
    resolved = resolve_predefined_cmap(
        name,
        CharacterCollection("Adobe", ordering, 99),
    )
    assert resolved is not None and resolved.vertical
    assert resolved.code_to_cid[bytes.fromhex(shown_hex[:4])] == cids[0]

    document = _load(data)
    assert document.redact_text("〈〉", overlay=True) == 1
    assert shown_hex.encode("ascii") not in document.pages[0].content
    assert b" h f\n" in document.pages[0].content


@pytest.mark.parametrize(
    "name,case",
    VERTICAL_REPLACEMENT_CASES,
    ids=[item[0] for item in VERTICAL_REPLACEMENT_CASES],
)
def test_predefined_vertical_cmap_replaces_and_round_trips(
    name: str,
    case: CMapCase,
) -> None:
    document = _load(
        _pdf_bytes(
            cmap_name=name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
            vertical=True,
        )
    )

    assert document.replace_text(case.source, case.replacement) == 1
    assert case.replacement_hex.encode("ascii") in document.pages[0].content
    assert _extract(_save(document)) == case.replacement


def test_w2_parser_supports_array_and_range_forms() -> None:
    metrics = load_cid_vertical_metrics(
        [
            10,
            [-500, 400, 880, -700, 450, 870],
            20,
            21,
            -900,
            500,
            860,
        ]
    )

    assert metrics[10] == (-500.0, 400.0, 880.0)
    assert metrics[11] == (-700.0, 450.0, 870.0)
    assert metrics[20] == metrics[21] == (-900.0, 500.0, 860.0)


def test_w2_metrics_drive_vertical_text_advance() -> None:
    pdf = SimplePdf.from_bytes(
        _pdf_bytes(
            cmap_name="UniJIS-UTF16-V",
            ordering="Japan1",
            shown_hex="30083009",
            cids=(7907, 7908),
            vertical=True,
        )
    )
    metric = pdf._build_simple_font_metrics(0)("F1")
    codec = pdf._build_text_codecs(0)("F1")

    assert metric is not None and codec is not None
    assert _string_advance(
        bytes.fromhex("30083009"),
        codec,
        metric,
        12.0,
        0.0,
        0.0,
        1.0,
    ) == pytest.approx(14.4)
    assert _string_advance(
        bytes.fromhex("30083009"),
        codec,
        metric,
        12.0,
        1.0,
        0.0,
        1.0,
    ) == pytest.approx(12.4)
    assert metric.vertical_metrics_of(999) == (-1000.0, 500.0, 880.0)

    quads = locate_matches(
        pdf.page_contents[0],
        "〈〉",
        pdf._build_simple_font_metrics(0),
    )
    assert len(quads) == 1
    coordinates = [coordinate for point in quads[0] for coordinate in point]
    assert coordinates == pytest.approx(
        [14.0, 61.04, 21.2, 61.04, 21.2, 79.04, 14.0, 79.04]
    )

    scaled_content = pdf.page_contents[0].replace(b"Tf", b"Tf 50 Tz", 1)
    scaled_quads = locate_matches(
        scaled_content,
        "〈〉",
        pdf._build_simple_font_metrics(0),
    )
    assert len(scaled_quads) == 1
    scaled_coordinates = [
        coordinate for point in scaled_quads[0] for coordinate in point
    ]
    assert scaled_coordinates == pytest.approx(
        [17.0, 61.04, 20.6, 61.04, 20.6, 79.04, 17.0, 79.04]
    )


def test_cid_reverse_mapping_excludes_ambiguous_text() -> None:
    codec = CidTextCodec({b"\x01": "A", b"\x02": "A", b"\x03": "B"})

    assert codec.encode("A") is None
    assert codec.encode("B") == b"\x03"


def test_cid_reverse_mapping_respects_codespace_and_valid_codes() -> None:
    codec = CidTextCodec(
        {b"\x00A": "A", b"A": "B", b"\x00C": "C"},
        codespaces=((b"\x00\x00", b"\xff\xff"),),
        valid_codes={b"\x00A": 1},
    )

    assert codec.encode("A") == b"\x00A"
    assert codec.encode("B") is None
    assert codec.encode("C") is None
    assert [unit[2] for unit in codec.decode_units(b"\x00A\x00C")] == ["A", "�"]


def test_compact_encoding_view_honors_vertical_overrides_and_base() -> None:
    encoding = resolve_predefined_cmap_encoding(
        "UniJIS-UTF16-V",
        CharacterCollection("Adobe", "Japan1", 7),
    )

    assert encoding is not None and encoding.vertical
    assert encoding.cid_for(bytes.fromhex("3008")) == 7907
    assert encoding.cid_for(bytes.fromhex("65E5")) == 3284


def test_to_unicode_uses_compact_predefined_encoding_under_low_limit() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="Japan1",
        shown_hex="65E5672C",
        cids=(3284, 3722),
        to_unicode=_to_unicode(("65E5", "65E5"), ("672C", "672C")),
    )
    pdf = SimplePdf.from_bytes(
        data,
        limits=PdfLoadLimits(max_container_items=100),
    )

    assert pdf.replace_text("日本", "本日") == 1
    assert b"672C65E5" in pdf.page_contents[0]


def test_invalid_predefined_code_in_to_unicode_is_opaque() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="Japan1",
        shown_hex="0378",
        cids=(1, 2),
        to_unicode=_to_unicode(("0378", "0041")),
    )
    document = _load(data)
    before = document.pages[0].content

    assert _extract(data) == "�"
    assert document.replace_text("A", "B") == 0
    assert document.redact_text("A", overlay=True) == 0
    assert document.pages[0].content == before


def test_mismatched_predefined_collection_rejects_to_unicode_editing() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="GB1",
        shown_hex="65E5",
        cids=(3284, 3722),
        to_unicode=_to_unicode(("65E5", "0041")),
    )
    document = _load(data)
    before = document.pages[0].content

    assert _extract(data) == "�"
    assert document.replace_text("A", "B") == 0
    assert document.pages[0].content == before


def test_partial_to_unicode_keeps_unmapped_predefined_code_opaque() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="Japan1",
        shown_hex="65E5672C",
        cids=(3284, 3722),
        to_unicode=_to_unicode(("65E5", "0041")),
    )

    assert _extract(data) == "A�"


def test_real_legacy_reverse_mapping_rejects_ambiguous_unicode() -> None:
    resolved = resolve_predefined_cmap(
        "90ms-RKSJ-H",
        CharacterCollection("Adobe", "Japan1", 7),
    )
    assert resolved is not None
    codec = CidTextCodec(
        resolved.code_to_text,
        codespaces=resolved.codespaces,
        opaque_unknown=True,
    )
    assert codec.encode("\uffe2") is None

    case = LEGACY_CASES[0]
    document = _load(
        _pdf_bytes(
            cmap_name=case.name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
        )
    )
    before = document.pages[0].content
    with pytest.raises(PdfValidationException, match="cannot be encoded"):
        document.replace_text(case.source, "\uffe2")
    assert document.pages[0].content == before


def test_predefined_codec_is_shared_across_font_instances() -> None:
    resolved = resolve_predefined_cmap(
        "UniJIS-UTF16-H",
        CharacterCollection("Adobe", "Japan1", 7),
    )

    assert resolved is not None
    assert predefined_cmap_codec(resolved) is predefined_cmap_codec(resolved)


def test_predefined_codespaces_decode_mixed_and_four_byte_codes() -> None:
    legacy = resolve_predefined_cmap(
        "90ms-RKSJ-H",
        CharacterCollection("Adobe", "Japan1", 7),
    )
    modern = resolve_predefined_cmap(
        "UniJIS-UTF16-H",
        CharacterCollection("Adobe", "Japan1", 7),
    )
    assert legacy is not None and modern is not None

    legacy_units = legacy.decode_units(bytes.fromhex("4193FA967B"))
    assert [unit[1] for unit in legacy_units] == [1, 2, 2]
    assert "".join(unit[3] or "" for unit in legacy_units) == "A日本"

    supplementary = modern.decode_units(bytes.fromhex("D82CDD32"))
    assert supplementary == [(0, 4, 12269, "\U0001b132")]


def test_public_round_trip_supports_cidfonttype2_descendant() -> None:
    case = MODERN_CASES[0]
    document = _load(
        _pdf_bytes(
            cmap_name=case.name,
            ordering=case.ordering,
            shown_hex=case.source_hex,
            cids=case.cids,
            cid_subtype="CIDFontType2",
        )
    )

    assert document.replace_text(case.source, case.replacement) == 1
    assert _extract(_save(document)) == case.replacement


def test_predefined_cmap_resolution_honors_resource_limits() -> None:
    _resolve_cached.cache_clear()
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        resolve_predefined_cmap(
            "UniJIS-UTF16-H",
            CharacterCollection("Adobe", "Japan1", 7),
            limits=PdfLoadLimits(max_container_items=10),
        )
    assert _resolve_cached.cache_info().currsize == 0


def test_unknown_or_mismatched_composite_cmap_is_opaque_to_editor() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="GB1",
        shown_hex="8141",
        cids=(1, 2),
    )
    document = _load(data)
    before = document.pages[0].content

    assert _extract(data) == "\ufffd"
    assert document.replace_text("A", "B") == 0
    assert document.redact_text("A") == 0
    assert document.pages[0].content == before


def test_unmapped_predefined_code_is_an_opaque_match_barrier() -> None:
    data = _pdf_bytes(
        cmap_name="UniJIS-UTF16-H",
        ordering="Japan1",
        shown_hex="65E50001672C",
        cids=(3284, 3722),
    )
    document = _load(data)
    before = document.pages[0].content

    assert _extract(data) == "日\ufffd本"
    assert document.replace_text("日本", "本日") == 0
    assert document.pages[0].content == before


def test_cmap_registry_and_ordering_must_match_exactly() -> None:
    assert (
        resolve_predefined_cmap(
            "UniJIS-UTF16-H",
            CharacterCollection("Other", "Japan1", 7),
        )
        is None
    )
    assert (
        resolve_predefined_cmap(
            "Unknown-H",
            CharacterCollection("Adobe", "Japan1", 7),
        )
        is None
    )


def test_cid_system_info_normalizer_handles_untrusted_numbers() -> None:
    assert (
        character_collection(
            {"Registry": b"Adobe", "Ordering": b"Japan1", "Supplement": True}
        )
        is None
    )
    normalized = character_collection(
        {
            "Registry": b"Adobe",
            "Ordering": b"Japan1",
            "Supplement": 10**10_000,
        }
    )
    assert normalized is not None and normalized.supplement == 10**10_000
