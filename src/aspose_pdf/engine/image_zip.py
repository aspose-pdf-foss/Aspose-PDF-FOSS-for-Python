"""Export selected page image resources without rendering or hydrating all images."""

from __future__ import annotations

import zipfile
import zlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import (
    PdfParseException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _json_value, _text, _write_entry
from .blank_pages import _number
from .cos import PdfArray, PdfDictionary, PdfIndirectReference, PdfName, PdfStream
from .image_export import _samples_to_png, ext_from_magic
from .simple_pdf import CosExtractor
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _plan(document: Document, pages: Iterable[int] | None, budget: _LoadBudget):
    """Inventory resource graphs once per page, deduplicating by image identity."""
    pdf = document._engine_pdf
    selection = page_indexes(pages, document.page_count, limits=document.load_limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(len(selection), "max_container_items", "image ZIP page selection")
    pdf._ensure_cos()
    extractor = CosExtractor(
        pdf._cos_doc,
        pdf._raw_bytes or b"",
        stream_decrypt_key=pdf._cos_decrypt_key or pdf.encryption_key,
        stream_decrypt_algorithm=pdf._cos_decrypt_algorithm
        if pdf._cos_decrypt_key
        else pdf.encryption_algorithm,
        limits=document.load_limits,
        budget=budget,
    )
    images = {}
    page_order = {}
    edges = 0
    path_items = 0
    for index in selection:
        if index in page_order:
            continue
        page_order[index] = len(page_order)
        page = pdf._get_page_dict(index)
        if not isinstance(page, PdfDictionary):
            raise PdfParseException("Image ZIP source page is not a dictionary")
        resources = pdf._resolve_resources_cos(page)
        stack = [(resources, (), 1)]
        seen = set()
        while stack:
            resources, path, depth = stack.pop()
            budget.check(depth, "max_nesting_depth", "image ZIP resource depth")
            if resources is None:
                continue
            if not isinstance(resources, PdfDictionary):
                raise PdfParseException("Image ZIP Resources must be a dictionary")
            if id(resources) in seen:
                continue
            seen.add(id(resources))
            xobjects = pdf._resolve(resources.mapping.get(PdfName("XObject")))
            if xobjects is None:
                continue
            if not isinstance(xobjects, PdfDictionary):
                raise PdfParseException("Image ZIP XObject must be a dictionary")
            budget.check(
                len(xobjects.mapping),
                "max_container_items",
                "image ZIP resource entries",
            )
            children = []
            for key, reference in xobjects.mapping.items():
                if not isinstance(key, PdfName):
                    raise PdfParseException(
                        "Image ZIP resource names must be PDF names"
                    )
                name = _text(key.name[1:], budget, "image ZIP resource name")
                children.append((name, reference))
            for name, reference in sorted(
                children, key=lambda entry: entry[0], reverse=True
            ):
                edges += 1
                budget.check(edges, "max_container_items", "image ZIP resource visits")
                obj = pdf._resolve(reference)
                if not isinstance(obj, PdfStream):
                    raise PdfParseException("Image ZIP XObject must be a stream")
                kind = extractor._get_name(obj.mapping.get(PdfName("Subtype")))
                if kind == "Form":
                    child = pdf._resolve(obj.mapping.get(PdfName("Resources")))
                    if child is not None:
                        stack.append((child, (*path, name), depth + 1))
                elif kind == "Image":
                    identity = id(obj)
                    if identity not in images:
                        try:
                            dimensions = extractor._image_dimensions(obj, name)
                        except (OverflowError, ValueError, TypeError) as exc:
                            raise PdfValidationException(
                                "Invalid image ZIP dimensions"
                            ) from exc
                        if dimensions is None:
                            raise PdfValidationException(
                                "Image ZIP requires image Width and Height"
                            )
                        if PdfName("BitsPerComponent") in obj.mapping:
                            sample_depth = _number(
                                extractor._get_number(
                                    obj.mapping[PdfName("BitsPerComponent")]
                                ),
                                "image ZIP sample depth",
                            )
                            if (
                                sample_depth != int(sample_depth)
                                or not 1 <= sample_depth <= 16
                            ):
                                raise PdfValidationException(
                                    "Image ZIP sample depth must be an integer from 1 to 16"
                                )
                        meta = extractor._resolve_image_meta(obj)
                        if isinstance(reference, PdfIndirectReference):
                            meta["object_number"] = reference.object_number
                        images[identity] = dict(
                            stream=obj, reference=reference, meta=meta, occurrences=[]
                        )
                    path_items += len(path) + 1
                    budget.check(
                        path_items,
                        "max_container_items",
                        "image ZIP occurrence path components",
                    )
                    images[identity]["occurrences"].append((index, (*path, name)))
    # Image names and archive order follow selected page order and resource paths.
    plan = list(images.values())
    for image in plan:
        image["occurrences"].sort(key=lambda item: (page_order[item[0]], item[1]))
    plan.sort(
        key=lambda image: (
            page_order[image["occurrences"][0][0]],
            image["occurrences"][0][1],
        )
    )
    return extractor, plan


def _payload(pdf, extractor: CosExtractor, image):
    """Preserve terminal JPEG/JPX bytes; pack all other known samples as PNG."""
    stream, reference, meta = image["stream"], image["reference"], image["meta"]
    raw_filters = pdf._resolve(stream.mapping.get(PdfName("Filter")))
    filters = (
        raw_filters.items
        if isinstance(raw_filters, PdfArray)
        else ([] if raw_filters is None else [raw_filters])
    )
    names = [extractor._get_name(value) for value in filters]
    if any(name is None for name in names):
        raise PdfValidationException("Invalid image ZIP stream filter")
    extractor._budget.check(
        len(filters), "max_stream_filters", "image ZIP filter chain"
    )
    if names and names[-1] in {"DCTDecode", "DCT", "JPXDecode"}:
        # Decode only outer filters, keeping the original encoded image intact.
        clone = PdfStream(content=stream.content, mapping=dict(stream.mapping))
        clone.content_decrypted = stream.content_decrypted
        if len(filters) > 1:
            clone.mapping[PdfName("Filter")] = PdfArray(filters[:-1])
            parms = pdf._resolve(stream.mapping.get(PdfName("DecodeParms")))
            if isinstance(parms, PdfArray):
                clone.mapping[PdfName("DecodeParms")] = PdfArray(
                    parms.items[: len(filters) - 1]
                )
        else:
            clone.mapping.pop(PdfName("Filter"), None)
            clone.mapping.pop(PdfName("DecodeParms"), None)
        data = extractor._decode_stream(clone, reference)
        expected = "jp2" if names[-1] == "JPXDecode" else "jpg"
        if ext_from_magic(data) != expected:
            raise PdfValidationException(
                "Image ZIP encoded image has an invalid signature"
            )
        return data, ("j2k" if data.startswith(b"\xff\x4f") else expected)
    converted = pdf._converted_image_for_export(meta)
    if converted is not None:
        meta, samples = converted
    else:
        samples = extractor._decode_stream(stream, reference)
    if meta.get("cs_kind") not in {"rgb", "gray", "cmyk", "indexed"}:
        raise UnsupportedFeatureException(
            "Image ZIP cannot reconstruct this image color space"
        )
    bpc = meta.get("bpc", 8)
    if bpc not in {1, 2, 4, 8, 16}:
        raise UnsupportedFeatureException(
            "Image ZIP supports sample depths 1, 2, 4, 8 and 16"
        )
    components = (
        meta.get("n_comps")
        or {"rgb": 3, "gray": 1, "cmyk": 4, "indexed": 1}[meta["cs_kind"]]
    )
    expected = ((meta["width"] * components * bpc + 7) // 8) * meta["height"]
    if len(samples) != expected:
        raise PdfValidationException(
            "Image ZIP decoded sample size does not match image geometry"
        )
    # These are declared PDF samples, even when their first bytes resemble JPEG.
    return _samples_to_png(meta, samples, None, extractor._limits), "png"


def _manifest(records, budget, total):
    """Serialize a bounded manifest without allocating large escaped strings."""
    with _BoundedBuffer(
        budget, "max_object_bytes", "image ZIP manifest bytes", decoded_prefix=total
    ) as output:
        output.write(b'{"schema_version":1,"images":[')
        for position, record in enumerate(records):
            if position:
                output.write(b",")
            output.write(b"{")
            for number, key in enumerate(
                (
                    "archive_name",
                    "size",
                    "width",
                    "height",
                    "bits_per_component",
                    "source_filter",
                    "has_soft_mask",
                )
            ):
                if number:
                    output.write(b",")
                _json_value(output, key)
                output.write(b":")
                _json_value(output, record[key])
            output.write(b',"occurrences":[')
            for occurrence, (page, path) in enumerate(record["occurrences"]):
                if occurrence:
                    output.write(b",")
                output.write(b'{"page_index":')
                _json_value(output, page)
                output.write(b',"resource_path":[')
                for component, name in enumerate(path):
                    if component:
                        output.write(b",")
                    _json_value(output, name)
                output.write(b"]}")
            output.write(b"]}")
        output.write(b"]}")
        return output.getvalue()


def export_zip(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    compression: str = "deflate",
    compresslevel: int = 6,
    include_manifest: bool = False,
) -> bytes:
    """Build the entire bounded archive before a destination can be touched."""
    if not isinstance(compression, str) or compression.strip().lower() not in {
        "deflate",
        "stored",
    }:
        raise PdfValidationException("compression must be 'deflate' or 'stored'")
    if (
        isinstance(compresslevel, bool)
        or not isinstance(compresslevel, int)
        or not 0 <= compresslevel <= 9
    ):
        raise PdfValidationException("compresslevel must be an integer from 0 to 9")
    if not isinstance(include_manifest, bool):
        raise PdfValidationException("include_manifest must be a boolean")
    method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    extractor, plan = _plan(document, pages, budget)
    budget.check(
        len(plan) + int(include_manifest), "max_container_items", "image ZIP entries"
    )
    records = []
    total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "image ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=method, compresslevel=compresslevel
        ) as archive:
            for number, image in enumerate(plan, 1):
                try:
                    data, extension = _payload(document._engine_pdf, extractor, image)
                except (ValueError, zlib.error) as exc:
                    raise PdfParseException(
                        f"Cannot export image {number}: {exc}"
                    ) from exc
                budget.check(
                    len(data), "max_object_bytes", "image ZIP encoded image bytes"
                )
                budget.check(
                    len(data),
                    "max_decoded_stream_bytes",
                    "image ZIP encoded image bytes",
                )
                total += len(data)
                budget.check(
                    total, "max_total_decoded_bytes", "image ZIP selected files"
                )
                name = f"image-{number:04d}.{extension}"
                _write_entry(archive, name, data, method, compresslevel)
                if include_manifest:
                    meta = image["meta"]
                    records.append(
                        dict(
                            archive_name=name,
                            size=len(data),
                            width=meta["width"],
                            height=meta["height"],
                            bits_per_component=meta["bpc"],
                            source_filter=meta.get("filter"),
                            has_soft_mask=bool(meta.get("smask")),
                            occurrences=image["occurrences"],
                        )
                    )
                del data
            if include_manifest:
                manifest = _manifest(records, budget, total)
                _write_entry(archive, "manifest.json", manifest, method, compresslevel)
        return output.getvalue()
