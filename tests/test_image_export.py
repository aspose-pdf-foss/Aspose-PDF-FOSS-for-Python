"""Image extraction produces real, openable files (Images audit row).

Covers the pure-Python PNG encoder, colour conversion (CMYK/Indexed/Gray->RGB),
the reconstruction orchestrator, image-metadata resolution from the COS graph,
and the end-to-end ``SimplePdf.save_image`` / ``ImagePlacement.save`` paths.
Pillow-only behaviour is exercised behind ``importorskip``.
"""

import struct
import zlib

import pytest

from aspose_pdf.engine import image_export as ie
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import CosExtractor, SimplePdf
from aspose_pdf.images import ImagePlacement, ImagePlacementAbsorber


# ---------------------------------------------------------------------------
# Helper: a tiny dependency-free PNG parser for assertions
# ---------------------------------------------------------------------------
def parse_png(png):
    assert png[:8] == ie.PNG_MAGIC, "missing PNG signature"
    pos = 8
    idat = b""
    info = {"chunks": [], "palette": None}
    while pos < len(png):
        (ln,) = struct.unpack(">I", png[pos : pos + 4])
        pos += 4
        tag = png[pos : pos + 4]
        pos += 4
        data = png[pos : pos + ln]
        pos += ln
        (crc,) = struct.unpack(">I", png[pos : pos + 4])
        pos += 4
        assert crc == (zlib.crc32(tag + data) & 0xFFFFFFFF), f"bad CRC for {tag}"
        info["chunks"].append(tag)
        if tag == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", data[:10])
            info.update(width=w, height=h, bit_depth=bd, color_type=ct)
        elif tag == b"PLTE":
            info["palette"] = data
        elif tag == b"IDAT":
            idat += data
    info["raw"] = zlib.decompress(idat)
    return info


# ---------------------------------------------------------------------------
# write_png
# ---------------------------------------------------------------------------
class TestWritePng:
    def test_rgb_roundtrip(self):
        data = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])  # 2x2 RGB
        png = ie.write_png(2, 2, "RGB", data)
        info = parse_png(png)
        assert (info["width"], info["height"], info["color_type"]) == (2, 2, 2)
        # filter byte + 6 pixel bytes per row
        assert info["raw"][0] == 0 and info["raw"][1:7] == data[:6]
        assert info["raw"][8:14] == data[6:]

    def test_gray8(self):
        png = ie.write_png(3, 1, "L", bytes([0, 128, 255]))
        info = parse_png(png)
        assert info["color_type"] == 0 and info["bit_depth"] == 8
        assert info["raw"] == bytes([0, 0, 128, 255])  # filter byte + 3 samples

    def test_gray1(self):
        # 4px wide, 1bpp -> one byte/row (padded)
        png = ie.write_png(4, 1, "L", bytes([0b10100000]), bit_depth=1)
        info = parse_png(png)
        assert info["color_type"] == 0 and info["bit_depth"] == 1

    def test_indexed_with_palette(self):
        palette = bytes([10, 20, 30, 200, 100, 50])
        png = ie.write_png(2, 1, "P", bytes([0, 1]), palette=palette)
        info = parse_png(png)
        assert info["color_type"] == 3
        assert info["palette"] == palette

    def test_rgba(self):
        png = ie.write_png(1, 1, "RGBA", bytes([1, 2, 3, 4]))
        info = parse_png(png)
        assert info["color_type"] == 6

    def test_short_data_is_padded(self):
        # Only one of two rows supplied -> encoder pads, still valid PNG.
        png = ie.write_png(1, 2, "L", bytes([7]))
        info = parse_png(png)
        assert info["raw"] == bytes([0, 7, 0, 0])

    def test_rejects_bad_mode_and_depth(self):
        with pytest.raises(ValueError):
            ie.write_png(1, 1, "YCbCr", b"\x00")
        with pytest.raises(ValueError):
            ie.write_png(1, 1, "L", b"\x00", bit_depth=4)
        with pytest.raises(ValueError):
            ie.write_png(0, 1, "L", b"")


# ---------------------------------------------------------------------------
# Colour conversion / sample unpacking
# ---------------------------------------------------------------------------
class TestColorConversion:
    def test_cmyk_to_rgb(self):
        # white (0,0,0,0) -> (255,255,255); pure black via K -> (0,0,0)
        out = ie.cmyk_to_rgb(bytes([0, 0, 0, 0, 0, 0, 0, 255]))
        assert out == bytes([255, 255, 255, 0, 0, 0])

    def test_gray_rgb_roundtrip(self):
        assert ie.gray_to_rgb(bytes([5, 9])) == bytes([5, 5, 5, 9, 9, 9])
        # luma of pure red ~ 76
        assert ie.rgb_to_gray(bytes([255, 0, 0]))[0] == (255 * 299) // 1000

    def test_indexed_to_rgb(self):
        palette = bytes([10, 20, 30, 200, 100, 50])
        out = ie.indexed_to_rgb(bytes([0, 1, 1, 0]), palette, 8, 2, 2, base_comps=3)
        assert out[:3] == bytes([10, 20, 30])
        assert out[3:6] == bytes([200, 100, 50])

    def test_indexed_cmyk_base(self):
        # one CMYK palette entry = pure black -> RGB (0,0,0)
        palette = bytes([0, 0, 0, 255])
        out = ie.indexed_to_rgb(bytes([0]), palette, 8, 1, 1, base_comps=4)
        assert out == bytes([0, 0, 0])

    def test_to_8bpc_subbyte(self):
        # 4bpp 2x2: row0 = [15,0], row1 = [0,15] -> scaled to 255/0
        assert ie.to_8bpc_bytes(bytes([0xF0, 0x0F]), 4, 2, 2, 1) == bytes(
            [255, 0, 0, 255]
        )

    def test_unpack_handles_row_padding(self):
        # width 3 @ 1bpp -> 3 bits in a padded byte; only 3 samples returned/row.
        vals = ie.unpack_samples(bytes([0b10100000]), 1, 3, 1, 1)
        assert vals == [1, 0, 1]

    def test_to_8bpc_16bit_keeps_high_byte(self):
        # one 16-bit sample 0x1234 -> high byte 0x12
        assert ie.to_8bpc_bytes(bytes([0x12, 0x34]), 16, 1, 1, 1) == bytes([0x12])


# ---------------------------------------------------------------------------
# reconstruct_image_file orchestrator
# ---------------------------------------------------------------------------
class TestReconstruct:
    def test_raster_rgb_to_png(self):
        meta = {"width": 2, "height": 1, "bpc": 8, "cs_kind": "rgb",
                "filter": "FlateDecode"}
        out, ext = ie.reconstruct_image_file(meta, bytes([1, 2, 3, 4, 5, 6]), "png")
        assert ext == "png"
        info = parse_png(out)
        assert info["raw"][1:7] == bytes([1, 2, 3, 4, 5, 6])

    def test_cmyk_raster_to_rgb_png(self):
        meta = {"width": 1, "height": 1, "bpc": 8, "cs_kind": "cmyk",
                "filter": "FlateDecode"}
        out, ext = ie.reconstruct_image_file(meta, bytes([0, 0, 0, 0]), "png")
        info = parse_png(out)
        assert info["color_type"] == 2  # RGB
        assert info["raw"][1:4] == bytes([255, 255, 255])

    def test_dct_passthrough_to_jpg(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8 + b"\xff\xd9"
        meta = {"width": 4, "height": 4, "bpc": 8, "cs_kind": "rgb",
                "filter": "DCTDecode"}
        out, ext = ie.reconstruct_image_file(meta, jpeg, "jpg")
        assert ext == "jpg" and out == jpeg

    def test_already_encoded_passthrough(self):
        sig = b"\x89PNG\r\n\x1a\n"
        out, ext = ie.reconstruct_image_file(None, sig, "png")
        assert out == sig and ext == "png"

    def test_ccitt_1bpp_to_png(self):
        meta = {"width": 8, "height": 1, "bpc": 1, "cs_kind": "gray",
                "filter": "CCITTFaxDecode"}
        out, ext = ie.reconstruct_image_file(meta, bytes([0b10101010]), "png")
        info = parse_png(out)
        assert info["bit_depth"] == 1 and info["color_type"] == 0

    def test_decode_inversion(self):
        meta = {"width": 8, "height": 1, "bpc": 1, "cs_kind": "gray",
                "filter": "CCITTFaxDecode", "decode": [1.0, 0.0]}
        out, _ = ie.reconstruct_image_file(meta, bytes([0b00000000]), "png")
        info = parse_png(out)
        assert info["raw"][1] == 0xFF  # inverted

    def test_force_grayscale(self):
        meta = {"width": 1, "height": 1, "bpc": 8, "cs_kind": "rgb",
                "filter": "FlateDecode"}
        out, _ = ie.reconstruct_image_file(meta, bytes([255, 0, 0]), "png",
                                           force_cs="Gray")
        info = parse_png(out)
        assert info["color_type"] == 0

    def test_no_meta_writes_verbatim(self):
        out, ext = ie.reconstruct_image_file(None, b"raw-bytes", "bin")
        assert out == b"raw-bytes"


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------
class TestResolveOutputPath:
    def test_keeps_matching_suffix(self, tmp_path):
        p = ie.resolve_output_path(tmp_path / "a.png", "png")
        assert p.name == "a.png"

    def test_jpg_jpeg_alias(self, tmp_path):
        p = ie.resolve_output_path(tmp_path / "a.jpeg", "jpg")
        assert p.name == "a.jpeg"

    def test_swaps_mismatched_suffix(self, tmp_path):
        p = ie.resolve_output_path(tmp_path / "a.tif", "png")
        assert p.name == "a.png"


# ---------------------------------------------------------------------------
# Metadata resolution from the COS graph
# ---------------------------------------------------------------------------
class TestResolveImageMeta:
    def _ext(self):
        doc = PdfDocument()
        doc.trailer = PdfDictionary({PdfName("Root"): PdfDictionary({})})
        return CosExtractor(doc, b"")

    def _img(self, **kw):
        return PdfStream(content=b"", mapping={PdfName(k): v for k, v in kw.items()})

    def test_rgb(self):
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(4), Height=PdfNumber(2),
                      BitsPerComponent=PdfNumber(8), ColorSpace=PdfName("DeviceRGB"),
                      Filter=PdfName("FlateDecode"))
        )
        assert m["cs_kind"] == "rgb" and m["bpc"] == 8
        assert m["filter"] == "FlateDecode" and m["width"] == 4

    def test_cmyk(self):
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(1), Height=PdfNumber(1),
                      BitsPerComponent=PdfNumber(8), ColorSpace=PdfName("DeviceCMYK"))
        )
        assert m["cs_kind"] == "cmyk" and m["n_comps"] == 4

    def test_indexed_string_lookup(self):
        cs = PdfArray([PdfName("Indexed"), PdfName("DeviceRGB"), PdfNumber(1),
                       PdfString(bytes([1, 2, 3, 4, 5, 6]))])
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(2), Height=PdfNumber(2),
                      BitsPerComponent=PdfNumber(8), ColorSpace=cs)
        )
        assert m["cs_kind"] == "indexed"
        assert m["palette"] == bytes([1, 2, 3, 4, 5, 6])
        assert m["palette_base_comps"] == 3

    def test_iccbased_n3(self):
        icc = PdfStream(content=b"", mapping={PdfName("N"): PdfNumber(3)})
        cs = PdfArray([PdfName("ICCBased"), icc])
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(1), Height=PdfNumber(1),
                      BitsPerComponent=PdfNumber(8), ColorSpace=cs)
        )
        assert m["cs_kind"] == "rgb" and m["n_comps"] == 3

    def test_image_mask_is_1bpp_gray(self):
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(8), Height=PdfNumber(1),
                      ImageMask=PdfBoolean(True), Filter=PdfName("CCITTFaxDecode"),
                      Decode=PdfArray([PdfNumber(1), PdfNumber(0)]))
        )
        assert m["bpc"] == 1 and m["cs_kind"] == "gray"
        assert m["decode"] == [1.0, 0.0]

    def test_dct_filter_recorded(self):
        cs = PdfArray([PdfName("DCTDecode")])
        m = self._ext()._resolve_image_meta(
            self._img(Width=PdfNumber(2), Height=PdfNumber(2),
                      BitsPerComponent=PdfNumber(8), ColorSpace=PdfName("DeviceRGB"),
                      Filter=cs)
        )
        assert m["filter"] == "DCTDecode"


# ---------------------------------------------------------------------------
# End-to-end through a parsed document
# ---------------------------------------------------------------------------
def _rgb_pdf_roundtrip(rgb, w, h):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.images = {"Img0": rgb}
    pdf._image_sizes = {"Img0": (w, h)}
    return SimplePdf.from_bytes(pdf.to_bytes())


class TestEndToEnd:
    def test_save_image_reconstructs_png(self, tmp_path):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])
        pdf = _rgb_pdf_roundtrip(rgb, 2, 2)
        assert "Img0" in pdf._image_meta
        out = pdf.save_image("Img0", tmp_path / "img.png")
        assert out.suffix == ".png"
        info = parse_png(out.read_bytes())
        assert (info["width"], info["height"], info["color_type"]) == (2, 2, 2)
        assert info["raw"][1:7] == rgb[:6]

    def test_save_image_force_gray(self, tmp_path):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])
        pdf = _rgb_pdf_roundtrip(rgb, 2, 2)
        out = pdf.save_image("Img0", tmp_path / "g.png", color_space="Gray")
        assert parse_png(out.read_bytes())["color_type"] == 0

    def test_absorber_placement_save(self, tmp_path):
        rgb = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        pdf = _rgb_pdf_roundtrip(rgb, 2, 2)
        absorber = ImagePlacementAbsorber()
        absorber.visit(pdf)
        placement = next(p for p in absorber.image_placements if p.name == "Img0")
        assert placement.color_space == "rgb" and placement.width == 2
        out = placement.save(tmp_path / "p.png")
        assert parse_png(out.read_bytes())["color_type"] == 2

    def test_backcompat_verbatim_when_no_meta(self, tmp_path):
        # An ImagePlacement built directly (no metadata) still writes raw bytes.
        sig = b"\x89PNG\r\n\x1a\n"
        out = ImagePlacement("x", sig).save(tmp_path / "out.png")
        assert out.read_bytes() == sig

    def test_save_image_unknown_name_raises(self):
        pdf = SimplePdf()
        with pytest.raises(KeyError):
            pdf.save_image("missing", "x.png")


# ---------------------------------------------------------------------------
# Pillow-only behaviour
# ---------------------------------------------------------------------------
class TestPillowPaths:
    def test_dct_to_png_with_pillow(self, tmp_path):
        PILImage = pytest.importorskip("PIL.Image")
        import io

        buf = io.BytesIO()
        PILImage.new("RGB", (3, 2), (10, 20, 30)).save(buf, format="JPEG")
        jpeg = buf.getvalue()
        meta = {"width": 3, "height": 2, "bpc": 8, "cs_kind": "rgb",
                "filter": "DCTDecode"}
        out, ext = ie.reconstruct_image_file(meta, jpeg, "png")
        assert ext == "png"
        assert parse_png(out)["width"] == 3
