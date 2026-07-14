"""Regression tests for resource limits applied to untrusted PDF input."""

from __future__ import annotations

import io
import base64
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.dss import read_dss
from aspose_pdf.engine.incremental_update import IncrementalUpdate
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.signature import PdfSignature


def _limits(**changes: int | None) -> PdfLoadLimits:
    """Return limits with only the explicitly named safeguards enabled."""
    return replace(PdfLoadLimits.unlimited(), **changes)


def _build_pdf(objects: dict[int, bytes], *, root: int = 1) -> bytes:
    """Build a deterministic classic-xref PDF from indirect object bodies."""
    data = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for object_number in sorted(objects):
        offsets[object_number] = len(data)
        data.extend(f"{object_number} 0 obj\n".encode("ascii"))
        data.extend(objects[object_number])
        data.extend(b"\nendobj\n")

    size = max(objects, default=0) + 1
    xref_offset = len(data)
    data.extend(f"xref\n0 {size}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for object_number in range(1, size):
        offset = offsets.get(object_number)
        if offset is None:
            data.extend(b"0000000000 00000 f \n")
        else:
            data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {size} /Root {root} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(data)


def _stream(payload: bytes, dictionary: bytes = b"") -> bytes:
    separator = b" " if dictionary else b""
    return (
        b"<< /Length "
        + str(len(payload)).encode("ascii")
        + separator
        + dictionary
        + b" >>\nstream\n"
        + payload
        + b"\nendstream"
    )


def _single_page_pdf(*, contents: bytes = b"") -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Contents 4 0 R >>",
        4: _stream(contents),
    }
    return _build_pdf(objects)


def _content_array_pdf() -> bytes:
    return _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents [4 0 R 5 0 R] >>",
            4: _stream(b"12345678"),
            5: _stream(b"abcdefgh"),
        }
    )


def _two_page_content_pdf() -> bytes:
    return _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 2 /Kids [3 0 R 4 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents 5 0 R >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents 6 0 R >>",
            5: _stream(b"12345678"),
            6: _stream(b"abcdefgh"),
        }
    )


def _two_page_shared_content_pdf() -> bytes:
    return _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 2 /Kids [3 0 R 4 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents 5 0 R >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents 5 0 R >>",
            5: _stream(b"12345678"),
        }
    )


def _multi_page_pdf(page_count: int) -> bytes:
    page_ids = list(range(3, 3 + page_count))
    kids = b" ".join(f"{object_number} 0 R".encode("ascii") for object_number in page_ids)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count "
        + str(page_count).encode("ascii")
        + b" /Kids ["
        + kids
        + b"] >>",
    }
    for object_number in page_ids:
        objects[object_number] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>"
        )
    return _build_pdf(objects)


def _deep_page_tree_pdf(levels: int) -> bytes:
    page_object = levels + 2
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        page_object: (
            f"<< /Type /Page /Parent {page_object - 1} 0 R "
            "/MediaBox [0 0 100 100] >>"
        ).encode("ascii"),
    }
    for level in range(levels):
        object_number = level + 2
        child = object_number + 1
        objects[object_number] = (
            f"<< /Type /Pages /Count 1 /Kids [{child} 0 R] >>"
        ).encode("ascii")
    return _build_pdf(objects)


class _ShortReadStream(io.BytesIO):
    """A stream that returns fewer bytes than requested without reaching EOF."""

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = 7
        return super().read(min(size, 7))


def test_document_load_from_accumulates_short_binary_reads() -> None:
    data = _single_page_pdf(contents=b"q Q")
    stream = _ShortReadStream(data)
    document = Document()

    try:
        document.load_from(stream, limits=_limits(max_input_bytes=len(data)))
        assert document.page_count == 1
        assert stream.closed is False
    finally:
        document.close()


def test_document_load_from_stops_at_input_byte_limit() -> None:
    data = _single_page_pdf()
    stream = _ShortReadStream(data)

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        Document().load_from(
            stream,
            limits=_limits(max_input_bytes=len(data) - 1),
        )

    assert stream.closed is False
    assert stream.tell() <= len(data)


@pytest.mark.parametrize(
    ("limits", "expected_name"),
    [
        (_limits(max_decoded_stream_bytes=1024), "max_decoded_stream_bytes"),
        (_limits(max_compression_ratio=2), "max_compression_ratio"),
    ],
)
def test_flate_decode_rejects_bombs(
    limits: PdfLoadLimits,
    expected_name: str,
) -> None:
    compressed = zlib.compress(b"A" * 4096, level=9)

    with pytest.raises(PdfResourceLimitException, match=expected_name):
        StreamDecoder.decode(
            compressed,
            "FlateDecode",
            None,
            limits=limits,
        )


def test_ascii85_rejects_expansion_before_decoder_allocation(monkeypatch) -> None:
    decoder_called = False

    def unexpected_decode(*args, **kwargs):
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("ASCII85 decoder must not run after the preflight fails")

    monkeypatch.setattr(base64, "a85decode", unexpected_decode)

    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        StreamDecoder.decode(
            b"z" * 10,
            "ASCII85Decode",
            None,
            limits=_limits(max_decoded_stream_bytes=39),
        )

    assert decoder_called is False


def test_asciihex_stops_before_crossing_output_limit() -> None:
    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        StreamDecoder.decode(
            b"AABBCC>",
            "ASCIIHexDecode",
            None,
            limits=_limits(max_decoded_stream_bytes=2),
        )


def test_parser_rejects_excessive_object_nesting() -> None:
    nested = b"[" * 8 + b"0" + b"]" * 8
    data = _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 0 /Kids [] >>",
            3: b"<< /Nested " + nested + b" >>",
        }
    )
    document = PdfCosParser(
        data,
        limits=_limits(max_nesting_depth=4),
    ).parse()

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        _ = document.objects[3]


def test_parser_counts_duplicate_dictionary_entries() -> None:
    data = _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 0 /Kids [] >>",
            3: b"<< /Repeated 1 /Repeated 2 /Repeated 3 >>",
        }
    )
    document = PdfCosParser(
        data,
        limits=_limits(max_container_items=2),
    ).parse()

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        _ = document.objects[3]


def test_parser_rejects_xref_object_count_before_materialization() -> None:
    data = _multi_page_pdf(3)

    with pytest.raises(PdfResourceLimitException, match="max_objects"):
        PdfCosParser(data, limits=_limits(max_objects=3)).parse()


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "lazy"])
def test_combined_page_content_limit_is_consistent(
    lazy: bool,
    tmp_path: Path,
) -> None:
    data = _content_array_pdf()
    limits = _limits(max_content_stream_bytes=12)

    if not lazy:
        with pytest.raises(
            PdfResourceLimitException,
            match="max_content_stream_bytes",
        ):
            SimplePdf.from_bytes(data, limits=limits)
        return

    path = tmp_path / "combined-content.pdf"
    path.write_bytes(data)
    pdf = SimplePdf.from_file_lazy(path, limits=limits)
    try:
        with pytest.raises(
            PdfResourceLimitException,
            match="max_content_stream_bytes",
        ):
            pdf.get_page_content(0)
    finally:
        pdf.dispose()


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "lazy"])
def test_total_decoded_budget_is_shared_across_page_streams(
    lazy: bool,
    tmp_path: Path,
) -> None:
    data = _two_page_content_pdf()
    limits = _limits(max_total_decoded_bytes=12)

    if not lazy:
        with pytest.raises(
            PdfResourceLimitException,
            match="max_total_decoded_bytes",
        ):
            SimplePdf.from_bytes(data, limits=limits)
        return

    path = tmp_path / "total-decoded.pdf"
    path.write_bytes(data)
    pdf = SimplePdf.from_file_lazy(path, limits=limits)
    try:
        assert pdf.get_page_content(0) == b"12345678"
        with pytest.raises(
            PdfResourceLimitException,
            match="max_total_decoded_bytes",
        ):
            pdf.get_page_content(1)
    finally:
        pdf.dispose()


def test_total_decoded_budget_charges_repeated_stream_decodes() -> None:
    with pytest.raises(
        PdfResourceLimitException,
        match="max_total_decoded_bytes",
    ):
        SimplePdf.from_bytes(
            _two_page_shared_content_pdf(),
            limits=_limits(max_total_decoded_bytes=12),
        )


def test_page_tree_cycle_is_rejected() -> None:
    data = _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [2 0 R] >>",
        }
    )

    with pytest.raises(PdfParseException, match="[Cc]ycle"):
        SimplePdf.from_bytes(data, limits=_limits(max_nesting_depth=10))


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "lazy"])
def test_page_tree_depth_limit_is_consistent(
    lazy: bool,
    tmp_path: Path,
) -> None:
    data = _deep_page_tree_pdf(6)
    limits = _limits(max_nesting_depth=3)

    if not lazy:
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            SimplePdf.from_bytes(data, limits=limits)
        return

    path = tmp_path / "deep-pages.pdf"
    path.write_bytes(data)
    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        SimplePdf.from_file_lazy(path, limits=limits)


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "lazy"])
def test_page_count_limit_is_consistent(
    lazy: bool,
    tmp_path: Path,
) -> None:
    data = _multi_page_pdf(3)
    limits = _limits(max_pages=2)

    if not lazy:
        with pytest.raises(PdfResourceLimitException, match="max_pages"):
            SimplePdf.from_bytes(data, limits=limits)
        return

    path = tmp_path / "many-pages.pdf"
    path.write_bytes(data)
    with pytest.raises(PdfResourceLimitException, match="max_pages"):
        SimplePdf.from_file_lazy(path, limits=limits)


@pytest.mark.parametrize("lazy", [False, True], ids=["eager", "lazy"])
def test_image_pixel_limit_is_consistent(
    lazy: bool,
    tmp_path: Path,
) -> None:
    data = _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Resources << /XObject << /Im1 4 0 R >> >> >>",
            4: _stream(
                b"\x00",
                b"/Type /XObject /Subtype /Image /Width 100 /Height 100 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8",
            ),
        }
    )
    limits = _limits(max_image_pixels=9_999)

    if not lazy:
        with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
            SimplePdf.from_bytes(data, limits=limits)
        return

    path = tmp_path / "large-image.pdf"
    path.write_bytes(data)
    with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
        SimplePdf.from_file_lazy(path, limits=limits)


def test_from_bytes_safe_does_not_turn_resource_limit_into_repair() -> None:
    data = _single_page_pdf()

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        SimplePdf.from_bytes_safe(
            data,
            limits=_limits(max_input_bytes=len(data) - 1),
        )


def test_content_token_limit_propagates_through_text_extraction() -> None:
    data = _single_page_pdf(contents=b"1 2 3 4 5 6 7 8 9 10")
    pdf = SimplePdf.from_bytes(data, limits=_limits(max_content_tokens=5))

    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        pdf.extract_text()


def test_image_placement_scan_uses_content_token_limit() -> None:
    content = b"1 2 3 4 5 6 7 8 9 10 /Im1 Do"
    data = _build_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>",
            4: _stream(content),
            5: _stream(
                b"\x00",
                b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8",
            ),
        }
    )

    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        SimplePdf.from_bytes(data, limits=_limits(max_content_tokens=5))


def test_supersampled_raster_limit_is_checked_before_rendering() -> None:
    document = Document(limits=_limits(max_raster_pixels=9_999))
    try:
        document.load_from(_single_page_pdf())
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.render_page(0, antialias=False)
    finally:
        document.close()


def test_xref_prev_cycle_is_rejected_without_reconstruction() -> None:
    data = _single_page_pdf()
    xref_offset = data.index(b"xref\n")
    data = data.replace(
        b"/Root 1 0 R >>",
        b"/Root 1 0 R /Prev " + str(xref_offset).encode("ascii") + b" >>",
    )

    with pytest.raises(PdfResourceLimitException, match="Cycle"):
        PdfCosParser(data).parse()


def test_incremental_update_fallback_rejects_sparse_object_ids() -> None:
    data = (
        b"%PDF-1.7\n999 0 obj\n<<>>\nendobj\n"
        b"startxref\n0\n%%EOF\n"
    )

    with pytest.raises(PdfResourceLimitException, match="max_objects"):
        IncrementalUpdate(data, limits=_limits(max_objects=10))


def test_incremental_update_checks_xref_line_before_copying() -> None:
    prefix = b"%PDF-1.7\n"
    xref_offset = len(prefix)
    data = (
        prefix
        + b"xref\n"
        + b" " * 64
        + b"\ntrailer\n<< /Size 1 >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )

    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        IncrementalUpdate(data, limits=_limits(max_object_bytes=16))


def test_signature_validation_does_not_swallow_resource_limits() -> None:
    signature = PdfSignature(
        name="limited",
        contents=b"invalid",
        byte_range=[0, 1, 1, 1],
        reference_data=b"ab",
        load_limits=_limits(max_input_bytes=1),
    )

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        _ = signature.valid
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        signature.validate()


def test_dss_reader_applies_input_limit_before_parsing() -> None:
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        read_dss(b"%PDF-1.7", limits=_limits(max_input_bytes=4))
