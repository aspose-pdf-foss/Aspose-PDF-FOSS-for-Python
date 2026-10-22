"""Image ZIP exports preserve assets, page provenance, lazy reads and destinations."""

from __future__ import annotations

import io
import json
import stat
import zlib
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import image_zip
from aspose_pdf.engine.content_authoring import _decode_png
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.load_limits import _LoadBudget


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _read(data):
    with zipfile_reader(data) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def zipfile_reader(data):
    import zipfile

    return zipfile.ZipFile(io.BytesIO(data))


def _png_pixels(data):
    return _decode_png(data, _LoadBudget())[4]


def _image(document, rgb=b"\xff\x00\x00", name=None):
    page = document.pages.add(size=(10, 10))
    page.add_image(
        RasterizedPage(1, 1, rgb).to_png(), 0, 0, width=10, height=10, name=name
    )
    return document._engine_pdf._get_page_dict(page.index)


def _resources(document, page):
    return document._engine_pdf._resolve_resources_cos(page)


def _register(document, page, name, value):
    pdf = document._engine_pdf
    xobjects = (
        pdf._ensure_resource_subdict(
            page.index if hasattr(page, "index") else 0, "XObject"
        )
        if not isinstance(page, PdfDictionary)
        else pdf._resolve(_resources(document, page).mapping[PdfName("XObject")])
    )
    reference = pdf._cos_doc.register_object(value)
    xobjects.mapping[PdfName(name)] = reference
    return reference


@pytest.mark.parametrize("compression", ["deflate", "stored", " DEFLATE "])
def test_export_real_png_files_and_deterministic_portable_zip_metadata(compression):
    with Document() as document:
        _image(document, b"\xff\x00\x00", "../../CON")
        _image(document, b"\x00\x00\xff", "\\unsafe")
        before = _bytes(document)
        data = document.export_images_zip(
            compression=compression, include_manifest=True
        )
        assert (
            document.export_images_zip(compression=compression, include_manifest=True)
            == data
        )
        assert _bytes(document) == before
        files = _read(data)
        assert list(files) == ["image-0001.png", "image-0002.png", "manifest.json"]
        assert [_png_pixels(files[name]) for name in list(files)[:2]] == [
            b"\xff\x00\x00",
            b"\x00\x00\xff",
        ]
        manifest = json.loads(files["manifest.json"])
        assert manifest["schema_version"] == 1
        assert [
            record["occurrences"][0]["page_index"] for record in manifest["images"]
        ] == [0, 1]
        for record in manifest["images"]:
            assert (
                record["width"],
                record["height"],
                record["bits_per_component"],
            ) == (1, 1, 8)
            assert record["size"] == len(files[record["archive_name"]])
        with zipfile_reader(data) as archive:
            for info in archive.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)
                assert stat.S_ISREG(info.external_attr >> 16)
                assert stat.S_IMODE(info.external_attr >> 16) == 0o644
                assert "/" not in info.filename and "\\" not in info.filename


def test_selection_keeps_order_deduplicates_pages_and_shared_objects_only():
    with Document() as document:
        first = _image(document)
        second = _image(document)
        third = _image(document, b"\x00\x00\xff")
        pdf = document._engine_pdf
        first_ref = next(
            iter(
                pdf._resolve(
                    _resources(document, first).mapping[PdfName("XObject")]
                ).mapping.values()
            )
        )
        third_xobjects = pdf._resolve(
            _resources(document, third).mapping[PdfName("XObject")]
        )
        third_xobjects.mapping[PdfName("Shared")] = first_ref
        files = _read(
            document.export_images_zip(
                pages=(p for p in (2, 0, 2, 1)), include_manifest=True
            )
        )
        records = json.loads(files["manifest.json"])["images"]
        assert len(records) == 3
        assert [_png_pixels(files[record["archive_name"]]) for record in records] == [
            b"\x00\x00\xff",
            b"\xff\x00\x00",
            b"\xff\x00\x00",
        ]
        assert records[1]["occurrences"] == [
            {"page_index": 2, "resource_path": ["Shared"]},
            {"page_index": 0, "resource_path": ["Im1"]},
        ]
        assert records[2]["occurrences"] == [
            {"page_index": 1, "resource_path": ["Im1"]}
        ]
        assert second is not first


def test_inherited_and_nested_form_resources_include_unpainted_assets_and_skip_cycles():
    with Document() as document:
        page = _image(document)
        pdf = document._engine_pdf
        resource = _resources(document, page)
        images = pdf._resolve(resource.mapping[PdfName("XObject")])
        reference = next(iter(images.mapping.values()))
        nested = PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("../../Picture"): reference})}
        )
        form = PdfStream(
            content=b"invalid form content never read",
            mapping={
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("Resources"): nested,
            },
        )
        form_ref = pdf._cos_doc.register_object(form)
        images.mapping[PdfName("Form")] = form_ref
        nested.mapping[PdfName("XObject")].mapping[PdfName("Cycle")] = form_ref
        parent = pdf._resolve(page.mapping[PdfName("Parent")])
        parent.mapping[PdfName("Resources")] = resource
        page.mapping.pop(PdfName("Resources"))
        files = _read(document.export_images_zip(include_manifest=True))
        (record,) = json.loads(files["manifest.json"])["images"]
        assert len(record["occurrences"]) == 2
        assert record["occurrences"][0]["resource_path"] == ["Form", "../../Picture"]
        assert _png_pixels(files[record["archive_name"]]) == b"\xff\x00\x00"


@pytest.mark.parametrize(
    "terminal,payload,extension",
    [
        ("DCTDecode", b"\xff\xd8\xff\xe0test\xff\xd9", "jpg"),
        ("JPXDecode", b"\x00\x00\x00\x0cjP  \r\n\x87\noriginal", "jp2"),
        ("JPXDecode", b"\xff\x4f\xff\x51original codestream", "j2k"),
    ],
)
@pytest.mark.parametrize("outer", [False, True])
def test_jpeg_and_jpeg2000_preserve_original_payloads_and_outer_filters(
    terminal, payload, extension, outer
):
    with Document() as document:
        page = _image(document)
        mapping = {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(2),
            PdfName("Height"): PdfNumber(1),
            PdfName("ColorSpace"): PdfName("DeviceRGB"),
            PdfName("BitsPerComponent"): PdfNumber(8),
            PdfName("Filter"): PdfArray([PdfName("FlateDecode"), PdfName(terminal)])
            if outer
            else PdfName(terminal),
        }
        stream = PdfStream(
            content=zlib.compress(payload) if outer else payload, mapping=mapping
        )
        _register(document, page, "Native", stream)
        before = dict(stream.mapping), stream.content
        files = _read(document.export_images_zip(include_manifest=True))
        assert files[f"image-0002.{extension}"] == payload
        assert (stream.mapping, stream.content) == before
        records = json.loads(files["manifest.json"])["images"]
        assert records[1]["source_filter"] == terminal


@pytest.mark.parametrize(
    "samples", [b"\xff\xd8\xff\x01\x02\x03", b"\x89PNG\r\n\x1a\n\x00"]
)
def test_raw_pixels_that_look_like_file_signatures_still_export_as_png(samples):
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        page.add_image(
            RasterizedPage(len(samples) // 3, 1, samples).to_png(),
            0,
            0,
            width=10,
            height=10,
        )
        files = _read(document.export_images_zip())
        assert list(files) == ["image-0001.png"]
        assert _png_pixels(files["image-0001.png"]) == samples


@pytest.mark.parametrize("include_manifest", [False, True])
def test_empty_pages_or_selection_export_valid_empty_archive(include_manifest):
    with Document() as document:
        for selection in (None, []):
            files = _read(
                document.export_images_zip(
                    pages=selection, include_manifest=include_manifest
                )
            )
            assert files == (
                {"manifest.json": b'{"schema_version":1,"images":[]}'}
                if include_manifest
                else {}
            )
        _image(document)
        assert _read(document.export_images_zip(pages=[])) == {}


def test_real_jpeg_payload_is_preserved_without_transcoding():
    jpeg = RasterizedPage(2, 1, b"\xff\x00\x00\x00\x00\xff").to_jpeg()
    with Document() as document:
        document.pages.add(size=(10, 10)).add_image(jpeg, 0, 0, width=10, height=10)
        assert _read(document.export_images_zip()) == {"image-0001.jpg": jpeg}


@pytest.mark.parametrize(
    "space,bpc,samples,expected,decode",
    [
        (PdfName("DeviceGray"), 8, b"\x2a", b"\x2a", None),
        (PdfName("DeviceCMYK"), 8, b"\x00\x00\x00\xff", b"\x00\x00\x00", None),
        (PdfName("DeviceRGB"), 16, b"\xff\xff\x00\x00\x00\x00", b"\xff\x00\x00", None),
        (
            PdfArray(
                [
                    PdfName("Indexed"),
                    PdfName("DeviceRGB"),
                    PdfNumber(1),
                    PdfString(b"\xff\x00\x00\x00\x00\xff"),
                ]
            ),
            8,
            b"\x01",
            b"\x00\x00\xff",
            None,
        ),
        (
            PdfName("DeviceGray"),
            8,
            b"\x00",
            b"\xff",
            PdfArray([PdfNumber(1), PdfNumber(0)]),
        ),
    ],
)
def test_supported_sample_color_spaces_depths_and_decode_arrays(
    space, bpc, samples, expected, decode
):
    with Document() as document:
        page = _image(document)
        mapping = {
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(1),
            PdfName("Height"): PdfNumber(1),
            PdfName("BitsPerComponent"): PdfNumber(bpc),
            PdfName("ColorSpace"): space,
        }
        if decode is not None:
            mapping[PdfName("Decode")] = decode
        _register(document, page, "A", PdfStream(content=samples, mapping=mapping))
        files = _read(document.export_images_zip())
        assert _png_pixels(files["image-0001.png"]) == expected


@pytest.mark.parametrize(
    "name,value",
    [
        ("compression", None),
        ("compression", "gzip"),
        ("compression", 1),
        ("compresslevel", -1),
        ("compresslevel", 10),
        ("compresslevel", True),
        ("compresslevel", 1.5),
        ("include_manifest", 1),
        ("include_manifest", None),
        ("overwrite", 1),
        ("overwrite", None),
        ("pages", 0),
        ("pages", "0"),
        ("pages", b"0"),
        ("pages", [True]),
        ("pages", [0.5]),
        ("pages", [-1]),
        ("pages", [9]),
        ("pages", [0, 9]),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_all_invalid_options_and_selectors_fail_without_decoding_or_writing(
    monkeypatch, tmp_path, name, value, empty
):
    with Document() as document:
        if not empty:
            _image(document)
        monkeypatch.setattr(
            image_zip, "_payload", lambda *a: pytest.fail("validate first")
        )
        path = tmp_path / "existing.zip"
        path.write_bytes(b"Existing")
        with pytest.raises(PdfValidationException):
            document.export_images_zip(path, **{name: value})
        assert path.read_bytes() == b"Existing"


@pytest.mark.parametrize(
    "limits,include_manifest,match",
    [
        (PdfLoadLimits(max_container_items=1), True, "max_container_items"),
        (PdfLoadLimits(max_image_pixels=1), False, "max_image_pixels"),
        (PdfLoadLimits(max_decoded_stream_bytes=2), False, "max_decoded_stream_bytes"),
        (PdfLoadLimits(max_total_decoded_bytes=2), False, "max_total_decoded_bytes"),
        (PdfLoadLimits(max_object_bytes=68), False, "max_object_bytes"),
        (PdfLoadLimits(max_object_bytes=150), True, "max_object_bytes"),
        (PdfLoadLimits(max_codec_work_bytes=10), False, "max_codec_work_bytes"),
    ],
)
def test_limits_cover_decoding_files_manifest_and_archive(
    tmp_path, limits, include_manifest, match
):
    with Document() as document:
        _image(document)
        if match == "max_image_pixels":
            image = next(
                value
                for value in document._engine_pdf._cos_doc.objects.values()
                if isinstance(value, PdfStream)
                and value.mapping.get(PdfName("Subtype")) == PdfName("Image")
            )
            image.mapping[PdfName("Width")] = PdfNumber(2)
        document._load_limits = limits
        path = tmp_path / "unchanged.zip"
        path.write_bytes(b"Keep")
        with pytest.raises(PdfResourceLimitException, match=match):
            document.export_images_zip(path, include_manifest=include_manifest)
        assert path.read_bytes() == b"Keep"


def test_page_selection_bounds_unbounded_generators():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.export_images_zip(pages=repeat(0))


def test_nested_form_depth_is_bounded():
    with Document() as document:
        page = _image(document)
        resource = _resources(document, page)
        form = PdfStream(
            content=b"",
            mapping={
                PdfName("Subtype"): PdfName("Form"),
                PdfName("Resources"): resource,
            },
        )
        _register(document, page, "Form", form)
        document._load_limits = PdfLoadLimits(max_nesting_depth=2)
        assert len(_read(document.export_images_zip())) == 1
        document._load_limits = PdfLoadLimits(max_nesting_depth=1)
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.export_images_zip()


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "bad_filter",
        "dimensions",
        "huge_dimensions",
        "depth",
        "unsupported",
        "invalid_native",
    ],
)
def test_damaged_or_unsupported_images_fail_without_publishing_partial_zip(
    tmp_path, kind
):
    with Document() as document:
        page = _image(document)
        stream = PdfStream(
            content=b"\x00",
            mapping={
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
            },
        )
        if kind == "bad_filter":
            stream.mapping[PdfName("Filter")] = PdfName("FlateDecode")
        elif kind == "dimensions":
            stream.mapping[PdfName("Width")] = PdfNumber(1.5)
        elif kind == "huge_dimensions":
            stream.mapping[PdfName("Width")] = PdfNumber(10**1000)
        elif kind == "depth":
            stream.mapping[PdfName("BitsPerComponent")] = PdfNumber(8.5)
        elif kind == "unsupported":
            stream.mapping[PdfName("ColorSpace")] = PdfName("Unsupported")
        elif kind == "invalid_native":
            stream.mapping[PdfName("Filter")] = PdfName("DCTDecode")
        _register(document, page, "ZDamaged", stream)
        path = tmp_path / "result.zip"
        path.write_bytes(b"Existing archive")
        with pytest.raises(
            (
                PdfValidationException,
                UnsupportedFeatureException,
                PdfParseException,
            )
        ):
            document.export_images_zip(path)
        assert path.read_bytes() == b"Existing archive"


def test_escaped_leading_slashes_in_resource_names_remain_distinct_in_manifest():
    with Document() as document:
        page = _image(document)
        other = PdfStream(
            content=b"\x00\x00\xff",
            mapping={
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
            },
        )
        _register(document, page, "//Im1", other)
        files = _read(document.export_images_zip(include_manifest=True))
        records = json.loads(files["manifest.json"])["images"]
        assert [record["occurrences"][0]["resource_path"] for record in records] == [
            ["/Im1"],
            ["Im1"],
        ]
        assert _png_pixels(files["image-0001.png"]) == b"\x00\x00\xff"


def test_invalid_resource_graph_is_rejected():
    with Document() as document:
        page = _image(document)
        _resources(document, page).mapping[PdfName("XObject")] = PdfNumber(1)
        with pytest.raises(PdfParseException):
            document.export_images_zip()


def test_disposed_documents_and_stalled_streams_are_rejected():
    with Document() as document:
        _image(document)

        class Stalled:
            def write(self, data):
                return 0

        with pytest.raises(AsposePdfException):
            document.export_images_zip(Stalled())
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_images_zip()


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_selected_exports_leave_lazy_caches_cursors_and_sources_unchanged(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        for rgb in (b"\xff\x00\x00", b"\x00\xff\x00", b"\x00\x00\xff"):
            _image(source, rgb)
        source.info["Title"] = "Original"
        source.add_attachment("keep.txt", b"Keep")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        before if kind == "bytes" else path, password="secret" if encrypted else None
    ) as document:
        pdf = document._engine_pdf
        protection = document.is_encrypted
        pdf.extract_image()
        pdf._image_cursor = 1
        cursor_names = pdf._image_names
        caches = set(dict.keys(pdf.images)), set(getattr(pdf.images, "_loaders", {}))
        monkeypatch.setattr(
            pdf,
            "get_page_content",
            lambda *a: pytest.fail("no page content should be read"),
        )
        files = _read(
            document.export_images_zip(pages=[2, 0, 2], include_manifest=True)
        )
        assert [_png_pixels(files[name]) for name in list(files)[:2]] == [
            b"\x00\x00\xff",
            b"\xff\x00\x00",
        ]
        assert pdf._image_names is cursor_names and pdf._image_cursor == 1
        assert (
            set(dict.keys(pdf.images)),
            set(getattr(pdf.images, "_loaders", {})),
        ) == caches
        assert document.info["Title"] == "Original" and document.attachments == {
            "keep.txt": b"Keep"
        }
        assert document.is_encrypted is protection
        if kind == "streaming":
            assert pdf.page_contents == [] and pdf._extracted_text is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("streaming", [False, True])
def test_original_jpeg_is_preserved_from_encrypted_saved_pdf(tmp_path, streaming):
    jpeg = RasterizedPage(2, 1, b"\xff\x00\x00\x00\xff\x00").to_jpeg()
    path = tmp_path / "native.pdf"
    with Document() as source:
        source.pages.add(size=(10, 10)).add_image(jpeg, 0, 0, width=10, height=10)
        source.encrypt("secret", "owner")
        source.save(path)
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret") as document:
        assert _read(document.export_images_zip()) == {"image-0001.jpg": jpeg}


def test_only_selected_image_payloads_are_decoded(monkeypatch):
    with Document() as document:
        _image(document)
        _image(document, b"\x00\xff\x00")
        pdf = document._engine_pdf
        page = pdf._get_page_dict(1)
        selected = pdf._resolve(
            next(
                iter(
                    pdf._resolve(
                        _resources(document, page).mapping[PdfName("XObject")]
                    ).mapping.values()
                )
            )
        )
        original = image_zip.CosExtractor._decode_stream

        def decode(extractor, stream, *args, **options):
            assert stream is selected
            return original(extractor, stream, *args, **options)

        monkeypatch.setattr(image_zip.CosExtractor, "_decode_stream", decode)
        assert (
            _png_pixels(_read(document.export_images_zip(pages=[1]))["image-0001.png"])
            == b"\x00\xff\x00"
        )


def test_soft_mask_presence_is_reported_without_exporting_it_as_a_separate_asset():
    from aspose_pdf.engine.image_export import write_png

    with Document() as document:
        document.pages.add(size=(10, 10)).add_image(
            write_png(1, 1, "RGBA", b"\xff\x00\x00\x80"), 0, 0, width=10, height=10
        )
        files = _read(document.export_images_zip(include_manifest=True))
        (record,) = json.loads(files["manifest.json"])["images"]
        assert record["has_soft_mask"] is True
        assert list(files) == ["image-0001.png", "manifest.json"]
        assert _png_pixels(files["image-0001.png"]) == b"\xff\x00\x00"


def test_inline_images_and_page_vector_content_are_not_rasterized_or_exported():
    with Document() as document:
        document.pages.add(
            (
                (0, 0, 10, 10),
                b"q 10 0 0 10 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI Q",
            )
        )
        assert _read(document.export_images_zip()) == {}


def test_native_image_filter_limit_includes_passthrough_filter():
    with Document() as document:
        page = _image(document)
        image = PdfStream(
            content=zlib.compress(b"\xff\xd8\xfforiginal"),
            mapping={
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("Filter"): PdfArray(
                    [PdfName("FlateDecode"), PdfName("DCTDecode")]
                ),
            },
        )
        _register(document, page, "Native", image)
        document._load_limits = PdfLoadLimits(max_stream_filters=1)
        with pytest.raises(PdfResourceLimitException, match="max_stream_filters"):
            document.export_images_zip()


def test_later_decode_failure_does_not_touch_stream_destination(monkeypatch):
    with Document() as document:
        _image(document)
        _image(document, b"\x00\xff\x00")
        original = image_zip._payload
        calls = []

        def payload(*args):
            calls.append(True)
            if len(calls) == 2:
                raise PdfValidationException("Damaged later image")
            return original(*args)

        monkeypatch.setattr(image_zip, "_payload", payload)
        output = io.BytesIO(b"Keep")
        output.seek(4)
        with pytest.raises(PdfValidationException, match="Damaged"):
            document.export_images_zip(output)
        assert output.getvalue() == b"Keep" and not output.closed


def test_files_streams_overwrite_and_short_writes(tmp_path):
    with Document() as document:
        _image(document)
        expected = document.export_images_zip()
        path = tmp_path / "new" / "images.zip"
        assert document.export_images_zip(path) == expected == path.read_bytes()
        with pytest.raises(FileExistsError):
            document.export_images_zip(path, overwrite=False)
        assert path.read_bytes() == expected
        stream = io.BytesIO(b"prefix")
        stream.seek(6)
        assert document.export_images_zip(stream) == expected
        assert stream.getvalue() == b"prefix" + expected and not stream.closed

        class Short(io.BytesIO):
            def write(self, data):
                return super().write(data[:3])

        output = Short()
        assert document.export_images_zip(output) == output.getvalue() == expected
