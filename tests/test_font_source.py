"""Tests for the font source / repository subsystem and SFNT parser."""

from __future__ import annotations

import struct

import pytest

from aspose_pdf.engine.sfnt import is_sfnt, parse_faces
from aspose_pdf.exceptions import FontEmbeddingException
from aspose_pdf.font_registry import FontDescriptor
from aspose_pdf.font_repository import (
    FileFontSource,
    FolderFontSource,
    FontRepository,
    FontSource,
    MemoryFontSource,
    SystemFontSource,
)


# ---------------------------------------------------------------------------
# Minimal SFNT builders (no external font files required).
# ---------------------------------------------------------------------------

_TRUETYPE = 0x00010000
_OTTO = 0x4F54544F


def _name_table(
    family: str,
    subfamily: str = "Regular",
    full: str | None = None,
    postscript: str | None = None,
) -> bytes:
    full = full if full is not None else f"{family} {subfamily}"
    postscript = postscript if postscript is not None else family.replace(" ", "")
    records = [
        (3, 1, 0x0409, 1, family),
        (3, 1, 0x0409, 2, subfamily),
        (3, 1, 0x0409, 4, full),
        (3, 1, 0x0409, 6, postscript),
    ]
    body = b""
    storage = b""
    pos = 0
    for platform, encoding, language, name_id, value in records:
        encoded = value.encode("utf-16-be")
        body += struct.pack(
            ">HHHHHH", platform, encoding, language, name_id, len(encoded), pos
        )
        storage += encoded
        pos += len(encoded)
    header = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    return header + body + storage


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def _build_font(
    tables: list[tuple[str, bytes]],
    version: int = _TRUETYPE,
    base: int = 0,
) -> bytes:
    num = len(tables)
    header = struct.pack(">IHHHH", version, num, 0, 0, 0)
    directory_size = 12 + 16 * num
    data = b""
    records = []
    offset = base + directory_size
    for tag, payload in tables:
        padded = _pad4(payload)
        records.append((tag, offset, len(payload)))
        data += padded
        offset += len(padded)
    directory = b"".join(
        tag.encode("ascii") + struct.pack(">III", 0, off, length)
        for tag, off, length in records
    )
    return header + directory + data


def _make_truetype(family: str, **kwargs) -> bytes:
    return _build_font([("glyf", b""), ("name", _name_table(family, **kwargs))])


def _make_opentype(family: str, **kwargs) -> bytes:
    return _build_font(
        [("CFF ", b""), ("name", _name_table(family, **kwargs))],
        version=_OTTO,
    )


def _make_ttc(families: list[str]) -> bytes:
    """Build a valid TrueType Collection with one face per family name."""
    font_tables = [[("glyf", b""), ("name", _name_table(f))] for f in families]
    n = len(font_tables)
    ttc_header_size = 12 + 4 * n
    directory_sizes = [12 + 16 * len(t) for t in font_tables]
    data_start = ttc_header_size + sum(directory_sizes)

    # Lay out table data with absolute offsets.
    data_all = b""
    per_font_records: list[list[tuple[str, int, int]]] = []
    cursor = data_start
    for tables in font_tables:
        records = []
        for tag, payload in tables:
            padded = _pad4(payload)
            records.append((tag, cursor, len(payload)))
            data_all += padded
            cursor += len(padded)
        per_font_records.append(records)

    # Build per-font offset tables (sfnt header + directory).
    directory_region = b""
    font_offsets = []
    font_dir_offset = ttc_header_size
    for i, tables in enumerate(font_tables):
        num = len(tables)
        header = struct.pack(">IHHHH", _TRUETYPE, num, 0, 0, 0)
        directory = b"".join(
            tag.encode("ascii") + struct.pack(">III", 0, off, length)
            for tag, off, length in per_font_records[i]
        )
        font_offsets.append(font_dir_offset)
        directory_region += header + directory
        font_dir_offset += 12 + 16 * num

    ttc_header = (
        b"ttcf"
        + struct.pack(">HHI", 1, 0, n)
        + b"".join(struct.pack(">I", o) for o in font_offsets)
    )
    return ttc_header + directory_region + data_all


# ---------------------------------------------------------------------------
# Keep the global repository state isolated per test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_repository():
    saved = FontRepository.get_sources()
    FontRepository.clear_sources()
    try:
        yield
    finally:
        FontRepository._sources = saved


# ---------------------------------------------------------------------------
# SFNT parser
# ---------------------------------------------------------------------------


def test_is_sfnt_detects_containers():
    assert is_sfnt(_make_truetype("Acme"))
    assert is_sfnt(_make_opentype("Acme"))
    assert is_sfnt(_make_ttc(["A", "B"]))
    assert not is_sfnt(b"not a font")
    assert not is_sfnt(b"")


def test_parse_truetype_names_and_type():
    faces = parse_faces(_make_truetype("Acme Sans", subfamily="Bold"))
    assert len(faces) == 1
    face = faces[0]
    assert face.family_name == "Acme Sans"
    assert face.subfamily_name == "Bold"
    assert face.full_name == "Acme Sans Bold"
    assert face.postscript_name == "AcmeSans"
    assert face.font_type == "TrueType"
    assert "glyf" in face.table_tags


def test_parse_opentype_is_classified_opentype():
    faces = parse_faces(_make_opentype("Beta Serif"))
    assert len(faces) == 1
    assert faces[0].font_type == "OpenType"
    assert faces[0].family_name == "Beta Serif"


def test_parse_collection_returns_face_per_font():
    faces = parse_faces(_make_ttc(["Alpha", "Beta", "Gamma"]))
    assert [f.family_name for f in faces] == ["Alpha", "Beta", "Gamma"]


def test_parse_faces_rejects_woff_and_garbage():
    assert parse_faces(b"wOFF" + b"\x00" * 40) == []
    assert parse_faces(b"wOF2" + b"\x00" * 40) == []
    assert parse_faces(b"junk") == []


def test_parse_truncated_font_does_not_raise():
    blob = _make_truetype("Acme")[:20]
    # Should degrade gracefully rather than raising.
    assert isinstance(parse_faces(blob), list)


# ---------------------------------------------------------------------------
# FontSource base + concrete sources
# ---------------------------------------------------------------------------


def test_base_font_source_requires_subclass():
    with pytest.raises(NotImplementedError):
        FontSource().get_font_definitions()


def test_memory_font_source_parses_and_keeps_bytes():
    blob = _make_truetype("Mem Font")
    source = MemoryFontSource(blob, name="Mem Font")
    defs = source.get_font_definitions()
    assert len(defs) == 1
    descriptor = defs[0]
    assert descriptor.name == "Mem Font"
    assert descriptor.font_type == "TrueType"
    assert descriptor.has_font_data
    assert descriptor.get_font_bytes() == blob


def test_file_font_source_reads_real_name_not_filename(tmp_path):
    path = tmp_path / "whatever-filename.ttf"
    blob = _make_truetype("Real Family")
    path.write_bytes(blob)
    defs = FileFontSource(path).get_font_definitions()
    assert len(defs) == 1
    # Name comes from the parsed 'name' table, not the file stem.
    assert defs[0].name == "Real Family"
    assert defs[0].path == str(path)
    assert defs[0].get_font_bytes() == blob


def test_file_font_source_missing_file_returns_empty(tmp_path):
    assert FileFontSource(tmp_path / "nope.ttf").get_font_definitions() == []


def test_folder_font_source_discovers_and_ignores_non_fonts(tmp_path):
    (tmp_path / "a.ttf").write_bytes(_make_truetype("Font A"))
    (tmp_path / "b.otf").write_bytes(_make_opentype("Font B"))
    (tmp_path / "readme.txt").write_bytes(b"not a font")
    names = {d.name for d in FolderFontSource(tmp_path).get_font_definitions()}
    assert names == {"Font A", "Font B"}


def test_folder_font_source_subdirectories(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.ttf").write_bytes(_make_truetype("Deep Font"))

    flat = FolderFontSource(tmp_path).get_font_definitions()
    assert flat == []

    deep = FolderFontSource(
        tmp_path, scan_subdirectories=True
    ).get_font_definitions()
    assert [d.name for d in deep] == ["Deep Font"]


def test_folder_font_source_collection_yields_multiple_faces(tmp_path):
    (tmp_path / "collection.ttc").write_bytes(_make_ttc(["Face One", "Face Two"]))
    defs = FolderFontSource(tmp_path).get_font_definitions()
    assert [d.name for d in defs] == ["Face One", "Face Two"]
    assert [d.face_index for d in defs] == [0, 1]


def test_folder_font_source_woff_falls_back_to_filename(tmp_path):
    # WOFF is recognised but not deep-parsed; discovery falls back to the stem.
    (tmp_path / "WebFont.woff").write_bytes(b"wOFF" + b"\x00" * 40)
    defs = FolderFontSource(tmp_path).get_font_definitions()
    assert len(defs) == 1
    assert defs[0].name == "WebFont"
    assert defs[0].font_type == "WOFF"


def test_system_font_source_directories_are_listed():
    # Smoke test: discovery must not raise even if directories are absent.
    source = SystemFontSource()
    assert isinstance(source._directories(), list)
    assert isinstance(source.get_font_definitions(), list)


# ---------------------------------------------------------------------------
# FontDescriptor
# ---------------------------------------------------------------------------


def test_descriptor_without_backing_raises():
    descriptor = FontDescriptor("Nameless")
    assert not descriptor.has_font_data
    with pytest.raises(FontEmbeddingException):
        descriptor.get_font_bytes()


def test_descriptor_matches_is_case_insensitive():
    descriptor = FontDescriptor(
        "Acme",
        family_name="Acme",
        full_name="Acme Bold",
        postscript_name="Acme-Bold",
    )
    assert descriptor.matches("acme")
    assert descriptor.matches("ACME BOLD")
    assert descriptor.matches("Acme-Bold")
    assert not descriptor.matches("Other")
    assert not descriptor.matches("")


# ---------------------------------------------------------------------------
# FontRepository
# ---------------------------------------------------------------------------


def test_repository_clear_and_reset():
    FontRepository.clear_sources()
    assert FontRepository.get_sources() == []
    FontRepository.reset_sources()
    sources = FontRepository.get_sources()
    assert len(sources) == 1
    assert isinstance(sources[0], SystemFontSource)


def test_repository_add_source_orders_by_priority():
    low = MemoryFontSource(_make_truetype("Low"), priority=1, name="Low")
    high = MemoryFontSource(_make_truetype("High"), priority=10, name="High")
    FontRepository.add_source(low)
    FontRepository.add_source(high)
    ordered = FontRepository.get_sources()
    assert ordered[0] is high
    assert ordered[1] is low


def test_repository_get_available_fonts_dedupes():
    blob = _make_truetype("Dup Font")
    FontRepository.add_source(MemoryFontSource(blob, name="Dup Font"))
    FontRepository.add_source(MemoryFontSource(blob, name="Dup Font"))
    names = [d.name for d in FontRepository.get_available_fonts()]
    assert names.count("Dup Font") == 1


def test_repository_find_font_by_various_names(tmp_path):
    path = tmp_path / "f.ttf"
    path.write_bytes(_make_truetype("Lookup Family"))
    FontRepository.add_source(FileFontSource(path))

    assert FontRepository.find_font("lookup family").name == "Lookup Family"
    assert FontRepository.find_font("LookupFamily").name == "Lookup Family"  # PS
    assert FontRepository.find_font("Lookup Family Regular").name == "Lookup Family"
    assert FontRepository.search("Lookup Family") is not None  # alias
    assert FontRepository.find_font("Missing") is None
    assert FontRepository.find_font("") is None


def test_repository_find_font_falls_back_to_standard_registry():
    # No sources registered; standard alias still resolves.
    descriptor = FontRepository.find_font("Arial")
    assert descriptor is not None
    assert descriptor.name == "Helvetica"
    assert descriptor.is_standard


def test_repository_open_font_returns_bytes():
    blob = _make_truetype("Embed Me")
    FontRepository.add_source(MemoryFontSource(blob, name="Embed Me"))
    assert FontRepository.open_font("Embed Me") == blob


def test_repository_open_font_none_for_standard_without_program():
    # Standard alias has no backing program -> no embeddable bytes.
    assert FontRepository.open_font("Arial") is None
    assert FontRepository.open_font("Definitely Missing") is None
