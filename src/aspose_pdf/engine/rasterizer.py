"""Dependency-free PDF page rasterization helpers.

The renderer is intentionally small and conservative. It handles the common
graphics/image/text operators used by generated PDFs, including mesh shadings
and transparency groups. It provides composite overprint preview for common
CMYK and spot-colour cases; plate-accurate and complete PDF 2.0 imaging
semantics remain outside this renderer.
"""

from __future__ import annotations

import contextlib
import copy
import itertools
import math
import struct
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aspose_pdf.exceptions import (
    PDF_OPERATION_ERRORS,
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

from .cff_outlines import CffOutlines
from .content_stream_parser import ContentStreamParser
from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from .font_resolver import ResolvedFace, resolver_for
from .glyph_outlines import TrueTypeOutlines
from .image_export import (
    TiffPage,
    cmyk_to_rgb,
    ext_from_magic,
    gray_to_rgb,
    indexed_to_rgb,
    rgb_to_gray,
    to_8bpc_bytes,
    write_png,
    write_tiff,
)
from .jpeg_encoder import encode as jpeg_encode
from .optional_content import OptionalContent
from .shading import Shading, build_color_converter, build_function, build_shading
from .std_font_data import load_substitute_sfnt, resolve_substitute_key
from .type1_outlines import Type1Outlines

Matrix = tuple[float, float, float, float, float, float]
Point = tuple[float, float]
Color = tuple[int, int, int]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_BLEND_MODES = {
    "normal": "Normal",
    "compatible": "Normal",
    "multiply": "Multiply",
    "screen": "Screen",
    "overlay": "Overlay",
    "darken": "Darken",
    "lighten": "Lighten",
    "colordodge": "ColorDodge",
    "colorburn": "ColorBurn",
    "hardlight": "HardLight",
    "softlight": "SoftLight",
    "difference": "Difference",
    "exclusion": "Exclusion",
    "hue": "Hue",
    "saturation": "Saturation",
    "color": "Color",
    "luminosity": "Luminosity",
}


@dataclass(frozen=True)
class RasterizedPage:
    """A rendered PDF page in packed RGB format."""

    width: int
    height: int
    pixels: bytes
    dpi: float = 72.0

    def get_pixel(self, x: int, y: int) -> Color:
        """Return the RGB pixel at ``(x, y)`` with origin at the top-left."""
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise IndexError("pixel coordinate is outside the raster")
        i = (y * self.width + x) * 3
        return (self.pixels[i], self.pixels[i + 1], self.pixels[i + 2])

    def to_gray(self) -> bytes:
        """Return this raster as one 8-bit luminance byte per pixel (Rec. 601)."""
        return rgb_to_gray(self.pixels)

    def to_bilevel(self, threshold: int = 128) -> bytes:
        """Return this raster as packed 1-bit rows, ``1`` = white.

        Pixels are thresholded on luminance -- above *threshold* is white --
        with each row padded to a byte boundary. Thresholding is a plain cut,
        not a dithering pass, which is what scanned-document-style output of
        text and line art wants.
        """
        gray = self.to_gray()
        limit = _byte(threshold)
        # Map each luminance byte to the ASCII digit of its bit, then let the
        # big-integer parser pack a whole row at C speed.
        digits = bytes(0x30 if value < limit else 0x31 for value in range(256))
        row_bytes = (self.width + 7) // 8
        padding = b"0" * (row_bytes * 8 - self.width)
        out = bytearray()
        for y in range(self.height):
            row = gray[y * self.width : (y + 1) * self.width].translate(digits)
            out += int(row + padding, 2).to_bytes(row_bytes, "big")
        return bytes(out)

    def _samples(self, mode: str, threshold: int) -> tuple[str, bytes]:
        """Return ``(encoder mode, samples)`` for a public colour *mode*."""
        normalized = str(mode).strip().lower()
        if normalized in ("rgb", "color", "colour"):
            return "RGB", self.pixels
        if normalized in ("gray", "grey", "grayscale", "greyscale", "l"):
            return "L", self.to_gray()
        if normalized in ("bilevel", "bw", "1"):
            return "1", self.to_bilevel(threshold)
        raise PdfValidationException(
            f"Unsupported raster colour mode {mode!r}; use 'rgb', 'gray' or 'bilevel'"
        )

    def to_png(self, *, mode: str = "rgb", threshold: int = 128) -> bytes:
        """Encode this raster as a PNG file.

        ``mode`` is ``"rgb"`` (the default), ``"gray"`` (8-bit luminance) or
        ``"bilevel"`` (1 bit per pixel, thresholded at *threshold*), which for
        text pages is a fraction of the size.
        """
        encoder_mode, samples = self._samples(mode, threshold)
        if encoder_mode == "1":
            return write_png(self.width, self.height, "L", samples, bit_depth=1)
        return write_png(self.width, self.height, encoder_mode, samples)

    def to_tiff(
        self,
        *,
        mode: str = "rgb",
        compression: str = "deflate",
        threshold: int = 128,
    ) -> bytes:
        """Encode this raster as a baseline TIFF file.

        ``compression`` defaults to ``"deflate"``: an uncompressed A4 page at
        300 dpi is about 25 MB, which is rarely what a caller wants. Pass
        ``"none"`` for the raw strip. ``mode`` behaves as in :meth:`to_png`.
        """
        encoder_mode, samples = self._samples(mode, threshold)
        try:
            return write_tiff(
                [
                    TiffPage(
                        width=self.width,
                        height=self.height,
                        mode=encoder_mode,
                        data=samples,
                        dpi=self.dpi,
                    )
                ],
                compression=compression,
            )
        except ValueError as exc:
            raise PdfValidationException(str(exc)) from exc

    def to_jpeg(
        self,
        *,
        quality: int = 85,
        mode: str = "rgb",
        progressive: bool = False,
    ) -> bytes:
        """Encode this raster as a JPEG file at *quality* (1-100).

        ``mode`` is ``"rgb"`` or ``"gray"``; JPEG has no bilevel form, so
        ``"bilevel"`` is rejected rather than silently producing a grey image
        with ringing around every glyph. The page DPI is written to the JFIF
        header.
        """
        encoder_mode, samples = self._samples(mode, 128)
        if encoder_mode == "1":
            raise PdfValidationException(
                "JPEG has no bilevel mode; use 'gray', or write PNG/TIFF"
            )
        components = 3 if encoder_mode == "RGB" else 1
        return jpeg_encode(
            self.width,
            self.height,
            components,
            samples,
            int(quality),
            progressive=progressive,
            dpi=self.dpi,
        )

    def save(
        self,
        path: str | Path,
        *,
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        threshold: int = 128,
    ) -> Path:
        """Save the raster and return the path it was written to.

        The format follows the suffix: ``.png``, ``.tif``/``.tiff``,
        ``.jpg``/``.jpeg``, or PNG when there is no suffix at all. ``mode``,
        ``compression`` (TIFF) and ``quality`` (JPEG) are passed through to the
        matching encoder.
        """
        out = Path(path)
        suffix = out.suffix.lower()
        if suffix in ("", ".png"):
            if suffix == "":
                out = out.with_suffix(".png")
            data = self.to_png(mode=mode, threshold=threshold)
        elif suffix in (".tif", ".tiff"):
            data = self.to_tiff(
                mode=mode, compression=compression, threshold=threshold
            )
        elif suffix in (".jpg", ".jpeg"):
            data = self.to_jpeg(quality=quality, mode=mode)
        else:
            raise PdfValidationException(
                "Unsupported raster output format; use .png, .tif, .tiff, "
                ".jpg, or .jpeg"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out


@dataclass
class _TextState:
    in_text: bool = False
    font_name: str | None = None
    font_size: float = 12.0
    leading: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    horizontal_scale: float = 1.0
    rendering_mode: int = 0
    rise: float = 0.0
    text_matrix: Matrix = IDENTITY
    line_matrix: Matrix = IDENTITY


@dataclass
class _GraphicsState:
    ctm: Matrix = IDENTITY
    stroke_color: Color = (0, 0, 0)
    fill_color: Color = (0, 0, 0)
    stroke_color_space: Any = None
    fill_color_space: Any = None
    stroke_color_kind: str = "gray"
    fill_color_kind: str = "gray"
    stroke_overprint: bool = False
    fill_overprint: bool = False
    overprint_mode: int = 0
    line_width: float = 1.0
    stroke_alpha: float = 1.0
    fill_alpha: float = 1.0
    blend_mode: str = "Normal"
    # Soft mask from the ExtGState /SMask: a device-space, supersampled
    # per-pixel alpha map (one byte 0-255 per canvas pixel) that further
    # modulates every paint, or None. Stored as immutable ``bytes`` so the
    # per-``q`` ``deepcopy`` of the state is O(1).
    soft_mask: bytes | None = None
    # When set, paths are filled with a shading pattern: (shading, pattern matrix).
    fill_shading: tuple[Shading, Matrix] | None = None
    # When set, paths are filled with a tiling pattern:
    # (pattern stream, pattern matrix, paint type, uncoloured paint colour).
    fill_tiling: tuple[Any, Matrix, int, Color] | None = None
    text: _TextState = field(default_factory=_TextState)


@dataclass
class _Path:
    subpaths: list[list[Point]] = field(default_factory=list)
    current: list[Point] | None = None

    def move_to(self, point: Point) -> None:
        self.current = [point]
        self.subpaths.append(self.current)

    def line_to(self, point: Point) -> None:
        if self.current is None:
            self.move_to(point)
        else:
            self.current.append(point)

    def close(self) -> None:
        if self.current and len(self.current) > 1:
            self.current.append(self.current[0])

    def clear(self) -> None:
        self.subpaths.clear()
        self.current = None

    def clone_subpaths(self) -> list[list[Point]]:
        return [[tuple(p) for p in subpath] for subpath in self.subpaths]


def _repeated_bytearray(pattern: bytes, count: int) -> bytearray:
    """Build a repeated byte pattern without a full-size temporary object."""
    out = bytearray(len(pattern) * count)
    if not pattern or count <= 0:
        return out
    block = pattern * min(count, 64 * 1024)
    view = memoryview(out)
    for start in range(0, len(out), len(block)):
        end = min(len(out), start + len(block))
        view[start:end] = block[: end - start]
    return out


class _Canvas:
    def __init__(self, width: int, height: int, background: Color):
        self.width = width
        self.height = height
        pixel_count = width * height
        self.pixels = _repeated_bytearray(bytes(background), pixel_count)
        self.clip = _repeated_bytearray(b"\x01", pixel_count)
        # Optional accumulated-alpha channel (0-255), allocated only for the
        # offscreen canvases used to build Alpha soft masks and to composite
        # transparency groups as a unit. None on the main page canvas.
        self.coverage: bytearray | None = None
        # Straight-alpha storage is enabled for isolated offscreen groups. The
        # page canvas and non-isolated groups have an opaque backdrop and avoid
        # this extra allocation.
        self.alpha: bytearray | None = None
        # Knockout groups composite each element against their initial backdrop.
        self.knockout = False
        self.initial_pixels: bytes | None = None
        self.initial_alpha: bytes | None = None

    def set_pixel(
        self,
        x: int,
        y: int,
        color: Color,
        alpha: float = 1.0,
        blend_mode: str = "Normal",
        overprint: bool = False,
    ) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        idx = y * self.width + x
        if not self.clip[idx]:
            return
        off = idx * 3
        alpha = min(1.0, max(0.0, alpha))
        if alpha <= 0.0:
            return
        if self.coverage is not None:
            if self.knockout:
                self.coverage[idx] = _byte(alpha * 255.0)
            else:
                cov = self.coverage[idx]
                self.coverage[idx] = _byte(alpha * 255.0 + cov * (1.0 - alpha))
        backdrop_pixels = (
            self.initial_pixels
            if self.knockout and self.initial_pixels is not None
            else self.pixels
        )
        backdrop = (
            backdrop_pixels[off],
            backdrop_pixels[off + 1],
            backdrop_pixels[off + 2],
        )
        if self.knockout and self.initial_alpha is not None:
            backdrop_alpha = self.initial_alpha[idx] / 255.0
        elif self.knockout:
            backdrop_alpha = 1.0
        elif self.alpha is not None:
            backdrop_alpha = self.alpha[idx] / 255.0
        else:
            backdrop_alpha = 1.0
        if overprint:
            # Composite overprint preview in the subtractive device model: a
            # source colorant paints only where its tint is non-zero, while a
            # zero-tint colorant leaves the backdrop's colorant untouched. This
            # is the nonzero-overprint rule for DeviceCMYK/DeviceGray and the
            # colorant-isolation rule for Separation/DeviceN. The old blanket
            # Multiply instead darkened untouched colorants and could not
            # replace a colorant with a lighter tint of itself.
            sc, sm, sy, sk = _rgb_to_cmyk(color)
            bc, bm, by, bk = _rgb_to_cmyk(backdrop)
            overprinted = _cmyk(
                sc if sc > 0.0 else bc,
                sm if sm > 0.0 else bm,
                sy if sy > 0.0 else by,
                sk if sk > 0.0 else bk,
            )
            blended = (
                overprinted
                if blend_mode == "Normal"
                else _blend_color(overprinted, backdrop, blend_mode)
            )
        else:
            blended = _blend_color(color, backdrop, blend_mode)
        output_alpha = alpha + backdrop_alpha * (1.0 - alpha)
        if output_alpha <= 0.0:
            return
        for channel in range(3):
            premultiplied = (
                (1.0 - alpha) * backdrop_alpha * backdrop[channel]
                + alpha
                * (
                    (1.0 - backdrop_alpha) * color[channel]
                    + backdrop_alpha * blended[channel]
                )
            )
            self.pixels[off + channel] = _byte(premultiplied / output_alpha)
        if self.alpha is not None:
            self.alpha[idx] = _byte(output_alpha * 255.0)


@dataclass
class _GlyphFont:
    """A resolved embedded TrueType font ready for glyph rasterization.

    ``code_to_gid`` maps a character code (simple font) or CID (composite) to a
    glyph id; ``width_1000`` returns the advance in text-space/1000 units keyed
    the same way; ``bytes_per_code`` is 1 for simple fonts and 2 for Identity
    composite fonts.

    For a composite font whose ``/Encoding`` names a bundled predefined CMap,
    ``cmap`` carries the compact code-to-CID view: ``iter_glyphs`` then splits
    the show string on the CMap's (possibly mixed-width) codespaces and maps
    each code to a CID before ``code_to_gid``/``width_1000``. ``bytes_per_code``
    is ignored while ``cmap`` is set.
    """

    outlines: Any  # TrueTypeOutlines | CffOutlines (duck-typed outline source)
    code_to_gid: Callable[[int], int | None]
    width_1000: Callable[[int], float]
    bytes_per_code: int
    cmap: Any = None  # PredefinedCMapEncoding for named composite fonts
    cmap_budget: Any = None  # _LoadBudget bounding decode_units work
    default_width_1000: float = 1000.0  # advance for codes the CMap cannot map
    # Set only for a bundled substitute face: its SFNT program (for HarfBuzz
    # cursive joining) and a single-byte code -> Unicode codepoint map. Used to
    # render complex-script runs with joined forms instead of isolated glyphs.
    shaping_program: bytes | None = None
    code_to_unicode: Callable[[int], int | None] | None = None
    # Set for a vertical composite font: ``vertical_metrics_1000(cid)`` returns
    # ``(w1y, v1x, v1y)`` in 1000 units for the vertical displacement and glyph
    # position vector (from /W2 and /DW2).
    vertical: bool = False
    vertical_metrics_1000: Callable[[int], tuple[float, float, float]] | None = None

    def iter_glyphs(self, raw: bytes):
        """Yield ``(gid_or_None, width_1000, applies_word_spacing, cid)`` per code.

        ``cid`` is the resolved CID (composite) or the raw code (simple/Identity),
        used to look up the per-glyph vertical metric.
        """
        if self.cmap is not None:
            for offset, length, cid in self.cmap.decode_units(
                raw, budget=self.cmap_budget
            ):
                # PDF word spacing applies only to a single-byte code 32.
                applies_word = length == 1 and raw[offset] == 32
                if cid is None:
                    yield None, self.default_width_1000, applies_word, None
                else:
                    yield self.code_to_gid(cid), self.width_1000(cid), applies_word, cid
            return
        if self.bytes_per_code == 2:
            for i in range(0, len(raw) - 1, 2):
                code = (raw[i] << 8) | raw[i + 1]
                yield self.code_to_gid(code), self.width_1000(code), False, code
        else:
            for byte in raw:
                yield self.code_to_gid(byte), self.width_1000(byte), byte == 32, byte


@dataclass
class _StreamCMap:
    """An embedded (stream) CMap as a compact code->CID decoder for rendering.

    Mirrors :meth:`PredefinedCMapEncoding.decode_units` so ``_GlyphFont`` can
    consume it identically: codespace-aware code splitting, then a direct
    code-bytes -> CID lookup. An unmapped code yields ``cid=None`` (drawn as
    nothing at the default advance), never a wrong glyph.
    """

    code_to_cid: dict[bytes, int]
    codespaces: tuple[tuple[bytes, bytes], ...]
    vertical: bool = False

    def decode_units(
        self, raw: bytes, *, budget: Any = None
    ) -> list[tuple[int, int, int | None]]:
        by_length: dict[int, list[tuple[int, int]]] = {}
        for low, high in self.codespaces:
            by_length.setdefault(len(low), []).append(
                (int.from_bytes(low, "big"), int.from_bytes(high, "big"))
            )
        lengths = sorted(by_length)
        units: list[tuple[int, int, int | None]] = []
        offset = 0
        n = len(raw)
        while offset < n:
            matched: bytes | None = None
            for length in lengths:
                if offset + length > n:
                    continue
                candidate = raw[offset : offset + length]
                value = int.from_bytes(candidate, "big")
                if any(low <= value <= high for low, high in by_length[length]):
                    matched = candidate
                    break
            length = len(matched) if matched is not None else 1
            cid = self.code_to_cid.get(matched) if matched is not None else None
            if budget is not None:
                budget.check(
                    len(units) + 1,
                    "max_container_items",
                    "stream CMap decoded units",
                )
            units.append((offset, length, cid))
            offset += length
        return units


def _normalize_antialias(antialias: Any) -> int:
    """Map the ``antialias`` argument to a supersampling factor (1 = off)."""
    if antialias is True or antialias is None:
        return 3
    if antialias is False:
        return 1
    factor = int(antialias)
    if factor < 1 or factor > 8:
        raise PdfValidationException("antialias must be a factor between 1 and 8")
    return factor


def _annotation_placement(
    bbox: tuple[float, float, float, float],
    matrix: Matrix,
    rect: tuple[float, float, float, float],
) -> Matrix | None:
    """Map a form's ``/Matrix``-transformed ``/BBox`` onto *rect* (ISO 12.5.5)."""
    corners = [
        _transform_point(matrix, bbox[0], bbox[1]),
        _transform_point(matrix, bbox[2], bbox[1]),
        _transform_point(matrix, bbox[2], bbox[3]),
        _transform_point(matrix, bbox[0], bbox[3]),
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    if not all(math.isfinite(v) for v in xs + ys):
        return None
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    # A zero-area transformed box has no scale to derive; place it unscaled.
    sx = (rect[2] - rect[0]) / bw if bw > 1e-9 else 1.0
    sy = (rect[3] - rect[1]) / bh if bh > 1e-9 else 1.0
    return (sx, 0.0, 0.0, sy, rect[0] - min(xs) * sx, rect[1] - min(ys) * sy)


def _wanted_style(
    base_font: str | None,
    flags: int,
    italic_angle: float,
    font_weight: float | None,
) -> tuple[bool, bool]:
    """Bold/italic intent from a ``/BaseFont`` name and FontDescriptor."""
    name = (base_font or "").lower()
    bold = (
        any(word in name for word in ("bold", "black", "heavy"))
        or bool(flags & (1 << 18))
        or (font_weight is not None and font_weight >= 600)
    )
    italic = (
        "italic" in name
        or "oblique" in name
        or bool(flags & (1 << 6))
        or abs(italic_angle) > 1e-6
    )
    return bold, italic


def render_page(
    pdf: Any,
    page_index: int,
    *,
    dpi: float = 72.0,
    scale: float = 1.0,
    background: Sequence[int] = (255, 255, 255),
    antialias: bool | int = True,
    shape_substitute_text: bool = True,
    draw_annotations: bool = True,
    font_substitution: Any = None,
    performance: Any = None,
) -> RasterizedPage:
    """Render ``page_index`` from a ``SimplePdf`` into an RGB raster.

    ``antialias`` smooths edges by supersampling: ``True`` (the default) renders
    at 3x and box-downsamples, an integer 1-8 sets the factor explicitly, and
    ``False`` (or ``1``) disables it for an exact, hard-edged raster.

    ``shape_substitute_text`` (default on) draws complex-script runs that fall
    back to a bundled substitute face with cursive-joined forms instead of
    isolated glyphs, when the optional ``text-layout`` extra is present.

    ``draw_annotations`` (default on) composites each visible annotation's
    normal appearance over the page content, the way a viewer shows it. Turn it
    off to render the page content alone.

    ``font_substitution`` is an optional
    :class:`~aspose_pdf.font_substitution.FontSubstitutionOptions` naming font
    directories, font programs or the platform fonts to draw non-embedded
    fonts with; it defaults to the document's own setting. Without one only the
    bundled substitute faces are used, exactly as before.

    ``performance`` is an optional
    :class:`~aspose_pdf.visualization.PerformanceLogger`; when one is given the
    render records how long each phase took into it. Without one nothing is
    timed, so a plain render pays nothing for the option.
    """
    if pdf is None:
        raise AsposePdfException("No document loaded")
    if page_index < 0 or page_index >= len(getattr(pdf, "pages", [])):
        raise IndexError("Page index out of range.")
    renderer = _PageRasterizer(
        pdf,
        page_index,
        dpi=dpi,
        scale=scale,
        background=background,
        antialias=antialias,
        shape_substitute_text=shape_substitute_text,
        draw_annotations=draw_annotations,
        font_substitution=font_substitution,
        performance=performance,
    )
    return renderer.render()


class _PageRasterizer:
    def __init__(
        self,
        pdf: Any,
        page_index: int,
        *,
        dpi: float,
        scale: float,
        background: Sequence[int],
        antialias: bool | int = True,
        shape_substitute_text: bool = True,
        draw_annotations: bool = True,
        font_substitution: Any = None,
        performance: Any = None,
    ):
        self.shape_substitute_text = bool(shape_substitute_text)
        self.draw_annotations = bool(draw_annotations)
        self.performance = performance
        if font_substitution is None:
            font_substitution = getattr(pdf, "_font_substitution", None)
        self._font_resolver = resolver_for(font_substitution)
        try:
            dpi_value = float(dpi)
            scale_value = float(scale)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PdfValidationException("dpi and scale must be finite numbers") from exc
        if (
            not math.isfinite(dpi_value)
            or not math.isfinite(scale_value)
            or dpi_value <= 0
            or scale_value <= 0
        ):
            raise PdfValidationException("dpi and scale must be positive")
        self.pdf = pdf
        self.page_index = page_index
        budget = getattr(pdf, "_load_budget", None)
        if isinstance(budget, _LoadBudget):
            self._load_budget = budget
            self._load_limits = budget.limits
        else:
            self._load_limits = _coerce_limits(getattr(pdf, "_load_limits", None))
            self._load_budget = _LoadBudget(self._load_limits)
        self.dpi = dpi_value
        self._ss = _normalize_antialias(antialias)
        base_scale = (dpi_value / 72.0) * scale_value
        if not math.isfinite(base_scale) or base_scale <= 0:
            raise PdfValidationException("dpi and scale produce invalid geometry")
        # Draw at ``base_scale * ss`` and box-downsample by ``ss`` at the end;
        # the drawing code uses self.width/height as the (supersampled) canvas
        # bounds, while the returned raster is the target resolution.
        self.point_scale = base_scale * self._ss
        self.media_box = self._normalize_box(pdf.pages[page_index])
        crop = None
        if hasattr(pdf, "get_page_crop_box"):
            crop = pdf.get_page_crop_box(page_index)
        self.crop_box = self._normalize_box(crop or self.media_box)
        self.rotation = self._page_rotation()
        self.crop_width = max(1e-6, self.crop_box[2] - self.crop_box[0])
        self.crop_height = max(1e-6, self.crop_box[3] - self.crop_box[1])
        if self.rotation in (90, 270):
            page_w, page_h = self.crop_height, self.crop_width
        else:
            page_w, page_h = self.crop_width, self.crop_height
        scaled_width = page_w * base_scale
        scaled_height = page_h * base_scale
        if (
            not math.isfinite(scaled_width)
            or not math.isfinite(scaled_height)
            or scaled_width <= 0
            or scaled_height <= 0
        ):
            raise PdfValidationException("page dimensions produce invalid raster geometry")
        self.target_width = max(1, math.ceil(scaled_width))
        self.target_height = max(1, math.ceil(scaled_height))
        self.width = self.target_width * self._ss
        self.height = self.target_height * self._ss
        self._load_budget.check_raster_pixels(
            self.width,
            self.height,
            "supersampled page raster",
        )
        self.page_width_pts = page_w
        self.page_height_pts = page_h
        self.background = _coerce_rgb(background)
        self.canvas = _Canvas(self.width, self.height, self.background)
        # Clip masks saved by ``q`` and restored by ``Q``.
        self._clip_stack: list[bytearray] = []
        self.state = _GraphicsState()
        self.state_stack: list[_GraphicsState] = []
        self.path = _Path()
        self.pending_clip: list[list[Point]] | None = None
        self.resources_cos = self._page_resources_cos()
        self.resources_plain = self._page_resources_plain()
        self._font_cache: dict[str, _GlyphFont | None] = {}
        self._color_converter_cache: dict[int, Callable[[list[float]], Color]] = {}
        self._pattern_depth = 0
        # Guards against a soft-mask group that itself sets a soft mask.
        self._in_soft_mask = False
        # Optional content (layers): hidden marked-content sections are not
        # painted. The depth counts nested BDC/BMC inside a hidden section so
        # the matching EMC ends it.
        self._optional_content: OptionalContent | None = None
        self._oc_hidden_depth = 0
        self._offscreen_work_bytes = 0

    def render(self) -> RasterizedPage:
        with self._phase("content"):
            content = self._page_content()
        if content:
            with self._phase("interpret"):
                self._interpret(
                    content, self.resources_cos, self.resources_plain, depth=0
                )
        if self.draw_annotations:
            with self._phase("annotations"):
                self._paint_annotations()
        with self._phase("downsample"):
            pixels = self._downsample() if self._ss > 1 else bytes(self.canvas.pixels)
        return RasterizedPage(
            width=self.target_width,
            height=self.target_height,
            pixels=pixels,
            dpi=self.dpi,
        )

    @contextlib.contextmanager
    def _phase(self, name: str):
        """Time a render phase into the caller's logger, if there is one.

        Without a logger this is a plain pass-through: a render nobody asked to
        measure does no timing work at all.
        """
        if self.performance is None:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            self.performance.record(name, time.perf_counter() - started)

    # Annotation flags (ISO 32000-1 table 165), 1-based bit positions.
    _ANNOT_HIDDEN = 1 << 1
    _ANNOT_NOVIEW = 1 << 5
    # A Popup is shown only while its parent is open, so it is not painted.
    _ANNOT_SKIP_SUBTYPES = frozenset({"Popup"})

    def _paint_annotations(self) -> None:
        """Composite each visible annotation's normal appearance onto the page.

        An appearance is a form XObject in its own space; ISO 32000-1 12.5.5
        maps it onto the annotation by fitting its ``/Matrix``-transformed
        ``/BBox`` to ``/Rect``. A malformed annotation is skipped rather than
        allowed to abort the page.
        """
        page = self._page_dict()
        if not isinstance(page, PdfDictionary):
            return
        annots = self._resolve(page.mapping.get(PdfName("Annots")))
        if not isinstance(annots, PdfArray):
            return
        for ref in list(annots.items):
            annot = self._resolve(ref)
            if not isinstance(annot, PdfDictionary):
                continue
            try:
                self._paint_one_annotation(annot)
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                continue

    def _page_dict(self) -> Any:
        getter = getattr(self.pdf, "_get_page_dict", None)
        return getter(self.page_index) if callable(getter) else None

    def _paint_one_annotation(self, annot: PdfDictionary) -> None:
        subtype = self._cos_name(annot.mapping.get(PdfName("Subtype")))
        if subtype in self._ANNOT_SKIP_SUBTYPES:
            return
        flags = self._cos_number(annot.mapping.get(PdfName("F")))
        if flags is not None:
            bits = int(flags)
            if bits & (self._ANNOT_HIDDEN | self._ANNOT_NOVIEW):
                return
        if not self._oc_visible(annot.mapping.get(PdfName("OC"))):
            return

        stream = self._annotation_normal_appearance(annot)
        if stream is None:
            return
        rect = self._cos_rect(annot.mapping.get(PdfName("Rect")))
        if rect is None:
            return

        matrix = (
            _cos_matrix(self._resolve(stream.mapping.get(PdfName("Matrix"))))
            or IDENTITY
        )
        bbox = self._cos_rect(stream.mapping.get(PdfName("BBox")))
        if bbox is None:
            return
        placement = _annotation_placement(bbox, matrix, rect)
        if placement is None:
            return

        saved_state, saved_stack = self.state, self.state_stack
        self.state = _GraphicsState(ctm=_multiply(placement, matrix))
        self.state_stack = []
        try:
            self._paint_form(
                stream,
                None,
                self.resources_cos,
                self.resources_plain,
                0,
                apply_matrix=False,
            )
        finally:
            self.state, self.state_stack = saved_state, saved_stack

    def _annotation_normal_appearance(self, annot: PdfDictionary) -> Any:
        """Return the ``/AP /N`` stream, resolving an appearance-state subdictionary."""
        ap = self._resolve(annot.mapping.get(PdfName("AP")))
        if not isinstance(ap, PdfDictionary):
            return None
        normal = self._resolve(ap.mapping.get(PdfName("N")))
        if isinstance(normal, PdfStream):
            return normal
        if not isinstance(normal, PdfDictionary):
            return None
        # A states dictionary: /AS names the one in effect.
        state = self._cos_name(annot.mapping.get(PdfName("AS")))
        if state is not None:
            candidate = self._resolve(normal.mapping.get(PdfName(state)))
            if isinstance(candidate, PdfStream):
                return candidate
            return None
        if len(normal.mapping) == 1:
            only = self._resolve(next(iter(normal.mapping.values())))
            if isinstance(only, PdfStream):
                return only
        return None

    def _cos_rect(self, value: Any) -> tuple[float, float, float, float] | None:
        array = self._resolve(value)
        if not isinstance(array, PdfArray) or len(array.items) < 4:
            return None
        numbers = [self._cos_number(item) for item in array.items[:4]]
        if any(n is None or not math.isfinite(float(n)) for n in numbers):
            return None
        x0, y0, x1, y1 = (float(n) for n in numbers)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _downsample(self) -> bytes:
        """Box-average each ``ss x ss`` block of the supersampled canvas.

        Measured as the most expensive phase of a render -- more than
        interpreting the page -- but it is close to the floor for pure Python:
        every one of the ``target_pixels * ss * ss * 3`` source bytes has to be
        added, and rewriting the loop as sequence operations (slicing the block
        rows, summing them elementwise, reducing each channel in groups) was
        1.4x faster at ``ss=3`` and *slower* at ``ss=2``. Going further means
        handing the work to a native array library, which this renderer
        deliberately does not require.
        """
        ss = self._ss
        src = self.canvas.pixels
        src_stride = self.width * 3
        tw, th = self.target_width, self.target_height
        out = bytearray(tw * th * 3)
        half = ss * ss // 2  # rounding bias
        denom = ss * ss
        o = 0
        for ty in range(th):
            block_top = ty * ss
            for tx in range(tw):
                base = block_top * src_stride + tx * ss * 3
                r = g = b = 0
                for dy in range(ss):
                    row = base + dy * src_stride
                    for dx in range(ss):
                        p = row + dx * 3
                        r += src[p]
                        g += src[p + 1]
                        b += src[p + 2]
                out[o] = (r + half) // denom
                out[o + 1] = (g + half) // denom
                out[o + 2] = (b + half) // denom
                o += 3
        return bytes(out)

    def _normalize_box(self, box: Any) -> tuple[float, float, float, float]:
        if box is None:
            return (0.0, 0.0, 612.0, 792.0)
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            raise PdfValidationException("page box must contain four coordinates")
        try:
            x0, y0, x1, y1 = (float(v) for v in box[:4])
        except (TypeError, ValueError, OverflowError) as exc:
            raise PdfValidationException("page box coordinates must be finite") from exc
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            raise PdfValidationException("page box coordinates must be finite")
        if x0 == x1 or y0 == y1:
            raise PdfValidationException("page box must have positive area")
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _page_rotation(self) -> int:
        if hasattr(self.pdf, "get_page_rotation"):
            return int(self.pdf.get_page_rotation(self.page_index)) % 360
        return 0

    def _page_content(self) -> bytes:
        if hasattr(self.pdf, "get_page_content"):
            return self.pdf.get_page_content(self.page_index)
        contents = getattr(self.pdf, "page_contents", [])
        if self.page_index < len(contents):
            return contents[self.page_index]
        return b""

    def _page_resources_cos(self) -> PdfDictionary | None:
        if not hasattr(self.pdf, "_get_page_dict") or not hasattr(
            self.pdf, "_resolve_resources_cos"
        ):
            return None
        page = self.pdf._get_page_dict(self.page_index)
        if page is None:
            return None
        resources = self.pdf._resolve_resources_cos(page)
        return resources if isinstance(resources, PdfDictionary) else None

    def _page_resources_plain(self) -> dict:
        resources: dict = {}
        if hasattr(self.pdf, "_get_page_resources"):
            try:
                resources = self.pdf._get_page_resources(self.page_index) or {}
            except PdfResourceLimitException:
                raise
            except Exception:
                resources = {}
        if not resources:
            resources = {}
        resources.setdefault("Font", getattr(self.pdf, "fonts", {}) or {})
        resources.setdefault("ExtGState", getattr(self.pdf, "extgstates", {}) or {})
        return resources

    def _interpret(
        self,
        content: bytes,
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
        *,
        depth: int,
    ) -> None:
        if depth > 8:
            return
        try:
            tokens = list(
                ContentStreamParser(
                    content,
                    resources_plain,
                    limits=self._load_limits,
                    budget=self._load_budget,
                )._tokenize()
            )
        except PdfResourceLimitException:
            raise
        except Exception:
            return

        operands: list[Any] = []
        for token in tokens:
            if not _is_operator(token):
                operands.append(token)
                continue
            try:
                self._handle_operator(
                    str(token), operands, resources_cos, resources_plain, depth
                )
            finally:
                operands.clear()

    def _optional_content_state(self) -> OptionalContent:
        if self._optional_content is None:
            self._optional_content = OptionalContent(self.pdf)
        return self._optional_content

    def _oc_visible(self, oc: Any) -> bool:
        """True when content tagged with an ``/OC`` value is shown."""
        if oc is None:
            return True
        state = self._optional_content_state()
        if not state.present:
            return True
        try:
            return state.is_visible(oc)
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            return True

    def _marked_content_hidden(
        self, operands: list[Any], resources_cos: PdfDictionary | None
    ) -> bool:
        """True for ``/OC ... BDC`` naming a group the configuration turns off."""
        if len(operands) < 2:
            return False
        tag = operands[0]
        tag_name = tag.name if isinstance(tag, PdfName) else str(tag)
        if tag_name.lstrip("/") != "OC":
            return False
        target = operands[1]
        if isinstance(target, (PdfName, str)):
            name = (
                target.name if isinstance(target, PdfName) else str(target)
            ).lstrip("/")
            if not isinstance(resources_cos, PdfDictionary):
                return False
            properties = self._resource_dict(resources_cos, "Properties")
            if properties is None:
                return False
            target = properties.mapping.get(PdfName(name))
            if target is None:
                return False
        return not self._oc_visible(target)

    # Operators that put marks on the page; suppressed inside hidden content.
    _OC_SUPPRESSED = frozenset(
        {"Do", "sh", "Tj", "TJ", "'", '"', "EI", "BI", "ID"}
    )
    _OC_PATH_PAINTING = frozenset(
        {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
    )

    def _handle_operator(
        self,
        op: str,
        operands: list[Any],
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
        depth: int,
    ) -> None:
        if op in ("BDC", "BMC"):
            if self._oc_hidden_depth:
                self._oc_hidden_depth += 1
            elif op == "BDC" and self._marked_content_hidden(operands, resources_cos):
                self._oc_hidden_depth = 1
            return
        if op == "EMC":
            if self._oc_hidden_depth:
                self._oc_hidden_depth -= 1
            return
        if self._oc_hidden_depth:
            # The content still runs -- graphics state, matrices and clipping
            # apply -- but nothing is drawn. A painting operator becomes the
            # no-op path painter so a pending clip is still honoured.
            if op in self._OC_SUPPRESSED:
                return
            if op in self._OC_PATH_PAINTING:
                op = "n"
        if op == "q":
            self.state_stack.append(copy.deepcopy(self.state))
            # The clipping path is part of the graphics state (ISO 32000-1
            # 8.4.4), so it has to come back at the matching Q. Only the
            # reference is stacked: ``_apply_clip`` builds a new mask rather
            # than mutating this one, so nothing is copied here.
            self._clip_stack.append(self.canvas.clip)
            return
        if op == "Q":
            if self.state_stack:
                self.state = self.state_stack.pop()
            if self._clip_stack:
                self.canvas.clip = self._clip_stack.pop()
            return
        if op == "cm" and len(operands) >= 6:
            vals = _last_numbers(operands, 6)
            if vals:
                self.state.ctm = _multiply(tuple(vals), self.state.ctm)
            return
        if op == "w" and operands:
            number = _number(operands[-1])
            if number is not None:
                self.state.line_width = max(0.0, number)
            return
        if op in ("rg", "g", "k", "RG", "G", "K"):
            self._set_color(op, operands)
            return
        if op in ("sc", "scn", "SC", "SCN"):
            self._set_current_space_color(op, operands, resources_cos)
            return
        if op in ("cs", "CS") and operands:
            self._set_color_space(op, operands[-1], resources_cos)
            return
        if op == "sh" and operands:
            self._paint_sh(str(operands[-1]).lstrip("/"), resources_cos)
            return
        if op == "gs" and operands:
            self._apply_extgstate(operands[-1], resources_cos, resources_plain)
            return
        if op in ("m", "l", "c", "v", "y", "h", "re"):
            self._append_path(op, operands)
            return
        if op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"):
            self._paint_path(op, depth)
            return
        if op in ("W", "W*"):
            self.pending_clip = self.path.clone_subpaths()
            return
        if op in (
            "BT",
            "ET",
            "Tf",
            "Td",
            "TD",
            "Tm",
            "T*",
            "Tj",
            "TJ",
            "'",
            '"',
            "Tc",
            "Tw",
            "Tz",
            "TL",
            "Tr",
            "Ts",
        ):
            self._handle_text(op, operands, resources_cos, resources_plain)
            return
        if op == "Do" and operands:
            name = str(operands[-1]).lstrip("/")
            self._paint_xobject(name, resources_cos, resources_plain, depth)

    def _set_color(self, op: str, operands: list[Any]) -> None:
        vals = [_number(v) for v in operands]
        nums = [v for v in vals if v is not None]
        if op in ("g", "G") and nums:
            color = _gray(nums[-1])
        elif op in ("rg", "RG") and len(nums) >= 3:
            color = _rgb(nums[-3], nums[-2], nums[-1])
        elif op in ("k", "K") and len(nums) >= 4:
            color = _cmyk(nums[-4], nums[-3], nums[-2], nums[-1])
        else:
            return
        if op.isupper():
            self.state.stroke_color = color
            self.state.stroke_color_space = {
                "G": PdfName("DeviceGray"),
                "RG": PdfName("DeviceRGB"),
                "K": PdfName("DeviceCMYK"),
            }[op]
            self.state.stroke_color_kind = {
                "G": "gray",
                "RG": "rgb",
                "K": "cmyk",
            }[op]
        else:
            self.state.fill_color = color
            self.state.fill_color_space = {
                "g": PdfName("DeviceGray"),
                "rg": PdfName("DeviceRGB"),
                "k": PdfName("DeviceCMYK"),
            }[op]
            self.state.fill_color_kind = {
                "g": "gray",
                "rg": "rgb",
                "k": "cmyk",
            }[op]
            self.state.fill_shading = None
            self.state.fill_tiling = None

    def _set_color_space(
        self,
        op: str,
        operand: Any,
        resources_cos: PdfDictionary | None,
    ) -> None:
        name = str(operand).lstrip("/")
        color_space: Any = PdfName(name)
        if resources_cos is not None:
            spaces = self._resource_dict(resources_cos, "ColorSpace")
            if spaces is not None and PdfName(name) in spaces.mapping:
                color_space = spaces.mapping[PdfName(name)]
        if op == "CS":
            self.state.stroke_color_space = color_space
            self.state.stroke_color_kind = self._color_space_kind(color_space)
        else:
            self.state.fill_color_space = color_space
            self.state.fill_color_kind = self._color_space_kind(color_space)

    def _color_space_kind(self, color_space: Any, *, pattern_base: bool = False) -> str:
        resolved = self._resolve(color_space)
        if isinstance(resolved, PdfName):
            return {
                "DeviceGray": "gray",
                "G": "gray",
                "DeviceRGB": "rgb",
                "RGB": "rgb",
                "DeviceCMYK": "cmyk",
                "CMYK": "cmyk",
                "Pattern": "pattern",
            }.get(resolved.name.lstrip("/"), "other")
        if isinstance(resolved, PdfArray) and resolved.items:
            head = self._cos_name(resolved.items[0]) or ""
            if head == "Pattern" and pattern_base and len(resolved.items) >= 2:
                return self._color_space_kind(resolved.items[1])
            if head == "Separation":
                return "spot"
            if head in ("DeviceN", "NChannel"):
                return "device_n"
            if head in ("DeviceCMYK", "CMYK"):
                return "cmyk"
            if head == "Pattern":
                return "pattern"
        return "other"

    def _convert_current_color(
        self,
        components: list[float],
        *,
        is_fill: bool,
        pattern_base: bool = False,
    ) -> Color:
        color_space = (
            self.state.fill_color_space if is_fill else self.state.stroke_color_space
        )
        resolved = self._resolve(color_space)
        if pattern_base and isinstance(resolved, PdfArray) and resolved.items:
            if self._cos_name(resolved.items[0]) == "Pattern":
                resolved = resolved.items[1] if len(resolved.items) >= 2 else None
        if resolved is not None and not (
            isinstance(resolved, PdfName)
            and resolved.name.lstrip("/") == "Pattern"
        ):
            key = id(resolved)
            converter = self._color_converter_cache.get(key)
            if converter is None:
                converter = build_color_converter(
                    self.pdf,
                    resolved,
                    limits=self._load_limits,
                    budget=self._load_budget,
                )
                self._color_converter_cache[key] = converter
            return converter(components)
        if len(components) >= 4:
            return _cmyk(*components[-4:])
        if len(components) >= 3:
            return _rgb(*components[-3:])
        if components:
            return _gray(components[-1])
        return (0, 0, 0)

    def _set_current_space_color(
        self, op: str, operands: list[Any], resources_cos: PdfDictionary | None
    ) -> None:
        is_fill = op in ("sc", "scn")
        # A trailing name operand selects a pattern: "/P0 scn" (an uncoloured
        # tiling pattern may carry its colour as leading operands).
        if op in ("scn", "SCN") and operands and _number(operands[-1]) is None:
            if is_fill:
                color_nums = [
                    _number(v) for v in operands[:-1] if _number(v) is not None
                ]
                self._set_fill_pattern(
                    str(operands[-1]).lstrip("/"), resources_cos, color_nums
                )
            return
        nums = [_number(v) for v in operands if _number(v) is not None]
        if not nums:
            return
        color = self._convert_current_color(nums, is_fill=is_fill)
        if is_fill:
            self.state.fill_color = color
            self.state.fill_shading = None
            self.state.fill_tiling = None
        else:
            self.state.stroke_color = color

    def _set_fill_pattern(
        self,
        name: str,
        resources_cos: PdfDictionary | None,
        color_nums: list[float | None],
    ) -> None:
        self.state.fill_shading = None
        self.state.fill_tiling = None
        pattern = None
        if resources_cos is not None:
            patterns = self._resource_dict(resources_cos, "Pattern")
            if patterns is not None:
                pattern = self._resolve(patterns.mapping.get(PdfName(name)))
        if not isinstance(pattern, (PdfDictionary, PdfStream)):
            self.state.fill_color = (128, 128, 128)  # unknown pattern fallback
            return
        ptype = self._cos_number(pattern.mapping.get(PdfName("PatternType")))
        matrix = (
            _cos_matrix(self._resolve(pattern.mapping.get(PdfName("Matrix"))))
            or IDENTITY
        )
        if ptype is not None and int(ptype) == 2:
            shading = build_shading(
                self.pdf,
                pattern.mapping.get(PdfName("Shading")),
                limits=self._load_limits,
                budget=self._load_budget,
                device_scale=self._device_scale(matrix),
            )
            if shading is not None:
                self.state.fill_shading = (shading, matrix)
            else:
                self.state.fill_color = (128, 128, 128)
            return
        if ptype is not None and int(ptype) == 1 and isinstance(pattern, PdfStream):
            paint_type = int(
                self._cos_number(pattern.mapping.get(PdfName("PaintType"))) or 1
            )
            paint_color = self.state.fill_color
            nums = [n for n in color_nums if n is not None]
            if paint_type == 2 and nums:  # uncoloured pattern carries its colour
                paint_color = self._convert_current_color(
                    nums,
                    is_fill=True,
                    pattern_base=True,
                )
                self.state.fill_color_kind = self._color_space_kind(
                    self.state.fill_color_space,
                    pattern_base=True,
                )
            self.state.fill_tiling = (pattern, matrix, paint_type, paint_color)
            return
        self.state.fill_color = (128, 128, 128)

    def _apply_extgstate(
        self,
        operand: Any,
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
    ) -> None:
        name = str(operand).lstrip("/")
        entry = None
        if resources_cos is not None:
            extgs = self._resource_dict(resources_cos, "ExtGState")
            if extgs is not None:
                entry = self._resolve(extgs.mapping.get(PdfName(name)))
        if entry is None:
            entry = (resources_plain.get("ExtGState") or {}).get(name)
        if isinstance(entry, PdfDictionary):
            lw = self._cos_number(entry.mapping.get(PdfName("LW")))
            if lw is not None:
                self.state.line_width = max(0.0, lw)
            ca = self._cos_number(entry.mapping.get(PdfName("ca")))
            if ca is not None:
                self.state.fill_alpha = min(1.0, max(0.0, ca))
            ca_stroke = self._cos_number(entry.mapping.get(PdfName("CA")))
            if ca_stroke is not None:
                self.state.stroke_alpha = min(1.0, max(0.0, ca_stroke))
            blend_mode = self._blend_mode(entry.mapping.get(PdfName("BM")))
            if blend_mode is not None:
                self.state.blend_mode = blend_mode
            if PdfName("OP") in entry.mapping:
                overprint = self._cos_bool(entry.mapping.get(PdfName("OP")))
                self.state.stroke_overprint = overprint
                if PdfName("op") not in entry.mapping:
                    self.state.fill_overprint = overprint
            if PdfName("op") in entry.mapping:
                self.state.fill_overprint = self._cos_bool(
                    entry.mapping.get(PdfName("op"))
                )
            overprint_mode = self._cos_number(entry.mapping.get(PdfName("OPM")))
            if overprint_mode is not None and int(overprint_mode) in (0, 1):
                self.state.overprint_mode = int(overprint_mode)
            if PdfName("SMask") in entry.mapping:
                self.state.soft_mask = self._build_soft_mask(
                    self._resolve(entry.mapping.get(PdfName("SMask"))),
                    resources_cos,
                    resources_plain,
                )
        elif isinstance(entry, dict):
            if isinstance(entry.get("LW"), (int, float)):
                self.state.line_width = max(0.0, float(entry["LW"]))
            if isinstance(entry.get("ca"), (int, float)):
                self.state.fill_alpha = min(1.0, max(0.0, float(entry["ca"])))
            if isinstance(entry.get("CA"), (int, float)):
                self.state.stroke_alpha = min(1.0, max(0.0, float(entry["CA"])))
            blend_mode = self._blend_mode(entry.get("BM"))
            if blend_mode is not None:
                self.state.blend_mode = blend_mode
            if "OP" in entry:
                overprint = bool(entry["OP"])
                self.state.stroke_overprint = overprint
                if "op" not in entry:
                    self.state.fill_overprint = overprint
            if "op" in entry:
                self.state.fill_overprint = bool(entry["op"])
            if isinstance(entry.get("OPM"), (int, float)):
                overprint_mode = int(entry["OPM"])
                if overprint_mode in (0, 1):
                    self.state.overprint_mode = overprint_mode

    def _blend_mode(self, obj: Any) -> str | None:
        names = self._blend_mode_names(obj)
        if not names:
            return None
        for name in names:
            mode = _normalize_blend_mode(name)
            if mode is not None:
                return mode
        return "Normal"

    def _blend_mode_names(self, obj: Any) -> list[str]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfArray):
            names: list[str] = []
            for item in obj.items:
                names.extend(self._blend_mode_names(item))
            return names
        if isinstance(obj, (list, tuple)):
            names = []
            for item in obj:
                names.extend(self._blend_mode_names(item))
            return names
        name = self._cos_name(obj)
        return [name] if name is not None else []

    def _append_path(self, op: str, operands: list[Any]) -> None:
        if op == "m":
            vals = _last_numbers(operands, 2)
            if vals:
                self.path.move_to(self._transform(vals[0], vals[1]))
            return
        if op == "l":
            vals = _last_numbers(operands, 2)
            if vals:
                self.path.line_to(self._transform(vals[0], vals[1]))
            return
        if op == "h":
            self.path.close()
            return
        if op == "re":
            vals = _last_numbers(operands, 4)
            if not vals:
                return
            x, y, w, h = vals
            points = [
                self._transform(x, y),
                self._transform(x + w, y),
                self._transform(x + w, y + h),
                self._transform(x, y + h),
                self._transform(x, y),
            ]
            self.path.current = points
            self.path.subpaths.append(points)
            return
        if op in ("c", "v", "y"):
            self._append_curve(op, operands)

    def _append_curve(self, op: str, operands: list[Any]) -> None:
        if self.path.current is None or not self.path.current:
            return
        p0 = self.path.current[-1]
        if op == "c":
            vals = _last_numbers(operands, 6)
            if not vals:
                return
            p1 = self._transform(vals[0], vals[1])
            p2 = self._transform(vals[2], vals[3])
            p3 = self._transform(vals[4], vals[5])
        elif op == "v":
            vals = _last_numbers(operands, 4)
            if not vals:
                return
            p1 = p0
            p2 = self._transform(vals[0], vals[1])
            p3 = self._transform(vals[2], vals[3])
        else:
            vals = _last_numbers(operands, 4)
            if not vals:
                return
            p1 = self._transform(vals[0], vals[1])
            p2 = self._transform(vals[2], vals[3])
            p3 = p2
        for step in range(1, 13):
            t = step / 12.0
            self.path.line_to(_bezier(p0, p1, p2, p3, t))

    def _paint_path(self, op: str, depth: int = 0) -> None:
        if op in ("s", "b", "b*"):
            self.path.close()
        if op in ("f", "F", "f*", "B", "B*", "b", "b*"):
            if self.state.fill_tiling is not None:
                self._fill_tiling(self.path.subpaths, self.state.fill_tiling, depth)
            elif self.state.fill_shading is not None:
                self._fill_subpaths_shading(
                    self.path.subpaths, self.state.fill_shading, self.state.fill_alpha
                )
            else:
                self._fill_subpaths(
                    self.path.subpaths, self.state.fill_color, self.state.fill_alpha
                )
        if op in ("S", "s", "B", "B*", "b", "b*"):
            self._stroke_subpaths(
                self.path.subpaths, self.state.stroke_color, self.state.stroke_alpha
            )
        if self.pending_clip is not None:
            self._apply_clip(self.pending_clip)
            self.pending_clip = None
        self.path.clear()

    def _fill_subpaths(
        self, subpaths: Iterable[list[Point]], color: Color, alpha: float
    ) -> None:
        for subpath in subpaths:
            polygon = [self._user_to_pixel(x, y) for x, y in subpath]
            self._fill_polygon_pixels(polygon, color, alpha)

    def _fill_tiling(
        self,
        subpaths: list[list[Point]],
        fill_tiling: tuple[Any, Matrix, int, Color],
        depth: int,
    ) -> None:
        if self._pattern_depth >= 4 or depth > 6:
            return
        pattern, matrix, paint_type, paint_color = fill_tiling
        bbox = self._cos_rect(pattern.mapping.get(PdfName("BBox")))
        xstep = self._cos_number(pattern.mapping.get(PdfName("XStep")))
        ystep = self._cos_number(pattern.mapping.get(PdfName("YStep")))
        inv = _invert_matrix(matrix)
        if bbox is None or not xstep or not ystep or inv is None:
            return
        polys = [
            [self._user_to_pixel(x, y) for x, y in sp]
            for sp in subpaths
            if len(sp) >= 3
        ]
        if not polys:
            return
        lattice = self._tile_lattice(polys, inv, bbox, xstep, ystep)
        if lattice is None:
            return
        i_lo, i_hi, j_lo, j_hi = lattice

        try:
            content = (
                self.pdf._decode_cos_stream(pattern, None)
                if hasattr(self.pdf, "_decode_cos_stream")
                else pattern.content
            )
        except PdfResourceLimitException:
            raise
        except Exception:
            content = pattern.content
        res_cos = self._resolve(pattern.mapping.get(PdfName("Resources")))
        res_cos = res_cos if isinstance(res_cos, PdfDictionary) else None
        res_plain: dict = {}
        if res_cos is not None and hasattr(self.pdf, "_convert_cos_to_dict"):
            res_plain = self.pdf._convert_cos_to_dict(res_cos)

        old_clip = bytes(self.canvas.clip)
        self._apply_clip(subpaths)
        # Isolate the pattern cell from the outer fill path / pending clip.
        outer_state = self.state
        outer_path = self.path
        outer_pending = self.pending_clip
        self._pattern_depth += 1
        try:
            for i in range(i_lo, i_hi + 1):
                for j in range(j_lo, j_hi + 1):
                    self.state = copy.deepcopy(outer_state)
                    self.state.ctm = _multiply(
                        matrix, (1.0, 0.0, 0.0, 1.0, i * xstep, j * ystep)
                    )
                    self.state.fill_shading = None
                    self.state.fill_tiling = None
                    if paint_type == 2:
                        self.state.fill_color = paint_color
                        self.state.stroke_color = paint_color
                    self.path = _Path()
                    self.pending_clip = None
                    stack_len = len(self.state_stack)
                    self._interpret(content, res_cos, res_plain, depth=depth + 1)
                    del self.state_stack[stack_len:]
        finally:
            self._pattern_depth -= 1
            self.state = outer_state
            self.path = outer_path
            self.pending_clip = outer_pending
            self.canvas.clip = bytearray(old_clip)

    def _tile_lattice(
        self,
        polys: list[list[Point]],
        inv: Matrix,
        bbox: tuple[float, float, float, float],
        xstep: float,
        ystep: float,
    ) -> tuple[int, int, int, int] | None:
        """Return the inclusive ``(i_lo, i_hi, j_lo, j_hi)`` tile range to draw."""
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        dev = (
            (min(xs), min(ys)),
            (max(xs), min(ys)),
            (max(xs), max(ys)),
            (min(xs), max(ys)),
        )
        pat_xs: list[float] = []
        pat_ys: list[float] = []
        for dx, dy in dev:
            ux, uy = self._pixel_to_user(dx, dy)
            sx, sy = _transform_point(inv, ux, uy)
            pat_xs.append(sx)
            pat_ys.append(sy)
        bx0, by0, bx1, by1 = bbox
        i0 = (min(pat_xs) - bx1) / xstep
        i1 = (max(pat_xs) - bx0) / xstep
        j0 = (min(pat_ys) - by1) / ystep
        j1 = (max(pat_ys) - by0) / ystep
        i_lo, i_hi = math.floor(min(i0, i1)), math.ceil(max(i0, i1))
        j_lo, j_hi = math.floor(min(j0, j1)), math.ceil(max(j0, j1))
        if (i_hi - i_lo + 1) * (j_hi - j_lo + 1) > 4096:
            return None  # too many tiles; skip rather than stall
        return i_lo, i_hi, j_lo, j_hi

    def _cos_rect(
        self, obj: Any
    ) -> tuple[float, float, float, float] | None:
        obj = self._resolve(obj)
        if not isinstance(obj, PdfArray) or len(obj.items) < 4:
            return None
        vals = [self._cos_number(item) for item in obj.items[:4]]
        if any(v is None for v in vals):
            return None
        x0, y0, x1, y1 = vals
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _fill_subpaths_shading(
        self,
        subpaths: Iterable[list[Point]],
        fill_shading: tuple[Shading, Matrix],
        alpha: float,
    ) -> None:
        shading, matrix = fill_shading
        to_shading = _invert_matrix(matrix)
        if to_shading is None:
            return
        for subpath in subpaths:
            polygon = [self._user_to_pixel(x, y) for x, y in subpath]
            self._fill_polygon_shading(polygon, shading, to_shading, alpha)

    def _fill_polygon_shading(
        self,
        polygon: list[Point],
        shading: Shading,
        to_shading: Matrix,
        alpha: float,
    ) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, math.floor(min(ys)))
        max_y = min(self.height - 1, math.ceil(max(ys)))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: list[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, math.floor(nodes[i]))
                x_end = min(self.width - 1, math.ceil(nodes[i + 1]))
                self._shade_span(
                    y,
                    x_start,
                    x_end,
                    shading,
                    to_shading,
                    alpha,
                    use_background=True,
                )

    def _paint_sh(self, name: str, resources_cos: PdfDictionary | None) -> None:
        """Paint a shading (the ``sh`` operator) over the current clip region."""
        if resources_cos is None:
            return
        shadings = self._resource_dict(resources_cos, "Shading")
        if shadings is None:
            return
        shading = build_shading(
            self.pdf,
            shadings.mapping.get(PdfName(name)),
            limits=self._load_limits,
            budget=self._load_budget,
            device_scale=self._device_scale(self.state.ctm),
        )
        if shading is None:
            return
        to_shading = _invert_matrix(self.state.ctm)
        if to_shading is None:
            return
        clip = self.canvas.clip
        width = self.width
        alpha = self.state.fill_alpha
        for y in range(self.height):
            row = y * width
            start: int | None = None
            for x in range(width):
                if clip[row + x]:
                    if start is None:
                        start = x
                elif start is not None:
                    self._shade_span(y, start, x - 1, shading, to_shading, alpha)
                    start = None
            if start is not None:
                self._shade_span(y, start, width - 1, shading, to_shading, alpha)

    def _shade_span(
        self,
        y: int,
        x_start: int,
        x_end: int,
        shading: Shading,
        to_shading: Matrix,
        alpha: float,
        *,
        use_background: bool = False,
    ) -> None:
        for x in range(x_start, x_end + 1):
            ux, uy = self._pixel_to_user(x, y)
            sx, sy = _transform_point(to_shading, ux, uy)
            color = (
                shading.pattern_color_at(sx, sy)
                if use_background
                else shading.color_at(sx, sy)
            )
            if color is not None:
                self._composite_pixel(
                    x,
                    y,
                    color,
                    alpha,
                    color_kind=shading.color_kind,
                )

    def _composite_pixel(
        self,
        x: int,
        y: int,
        color: Color,
        alpha: float,
        *,
        stroke: bool = False,
        color_kind: str | None = None,
    ) -> None:
        """Composite one pixel, modulating alpha by the active soft mask.

        Every paint path routes through here so the ExtGState ``/SMask`` (built
        in device space at the supersampled canvas resolution) attenuates fills,
        strokes, glyphs, shadings, patterns, and images uniformly.
        """
        mask = self.state.soft_mask
        if mask is not None:
            if 0 <= x < self.width and 0 <= y < self.height:
                alpha *= mask[y * self.width + x] * (1.0 / 255.0)
            else:
                return
        kind = color_kind or (
            self.state.stroke_color_kind if stroke else self.state.fill_color_kind
        )
        enabled = (
            self.state.stroke_overprint if stroke else self.state.fill_overprint
        )
        simulate_overprint = enabled and (
            kind in ("spot", "device_n")
            or (kind == "cmyk" and self.state.overprint_mode == 1)
        )
        self.canvas.set_pixel(
            x,
            y,
            color,
            alpha,
            blend_mode=self.state.blend_mode,
            overprint=simulate_overprint,
        )

    def _stroke_subpaths(
        self, subpaths: Iterable[list[Point]], color: Color, alpha: float
    ) -> None:
        px_width = max(1.0, self.state.line_width * self.point_scale)
        radius = max(0.5, px_width / 2.0)
        for subpath in subpaths:
            if len(subpath) < 2:
                continue
            pts = [self._user_to_pixel(x, y) for x, y in subpath]
            for p0, p1 in itertools.pairwise(pts):
                self._stroke_segment_pixels(p0, p1, radius, color, alpha)

    def _fill_polygon_pixels(
        self, polygon: list[Point], color: Color, alpha: float
    ) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, math.floor(min(ys)))
        max_y = min(self.height - 1, math.ceil(max(ys)))
        if min_y > max_y:
            return
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: list[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, math.floor(nodes[i]))
                x_end = min(self.width - 1, math.ceil(nodes[i + 1]))
                for x in range(x_start, x_end + 1):
                    self._composite_pixel(x, y, color, alpha)

    def _stroke_segment_pixels(
        self, p0: Point, p1: Point, radius: float, color: Color, alpha: float
    ) -> None:
        x0, y0 = p0
        x1, y1 = p1
        min_x = max(0, math.floor(min(x0, x1) - radius))
        max_x = min(self.width - 1, math.ceil(max(x0, x1) + radius))
        min_y = max(0, math.floor(min(y0, y1) - radius))
        max_y = min(self.height - 1, math.ceil(max(y0, y1) + radius))
        if min_x > max_x or min_y > max_y:
            return
        dx = x1 - x0
        dy = y1 - y0
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 1e-12:
            return
        rr = radius * radius
        for y in range(min_y, max_y + 1):
            py = y + 0.5
            for x in range(min_x, max_x + 1):
                px = x + 0.5
                t = ((px - x0) * dx + (py - y0) * dy) / seg_len_sq
                t = min(1.0, max(0.0, t))
                cx = x0 + t * dx
                cy = y0 + t * dy
                if (px - cx) * (px - cx) + (py - cy) * (py - cy) <= rr:
                    self._composite_pixel(x, y, color, alpha, stroke=True)

    def _apply_clip(self, subpaths: list[list[Point]]) -> None:
        if not subpaths:
            return
        next_clip = bytearray(b"\x00" * (self.width * self.height))
        for subpath in subpaths:
            polygon = [self._user_to_pixel(x, y) for x, y in subpath]
            self._rasterize_clip_polygon(polygon, next_clip)
        # A *new* mask, never an in-place edit: the outer graphics states on
        # the stack still hold the previous one and must keep seeing it.
        current = self.canvas.clip
        self.canvas.clip = bytearray(
            1 if current[i] and val else 0 for i, val in enumerate(next_clip)
        )

    def _rasterize_clip_polygon(self, polygon: list[Point], mask: bytearray) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, math.floor(min(ys)))
        max_y = min(self.height - 1, math.ceil(max(ys)))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: list[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, math.floor(nodes[i]))
                x_end = min(self.width - 1, math.ceil(nodes[i + 1]))
                row = y * self.width
                for x in range(x_start, x_end + 1):
                    mask[row + x] = 1

    def _handle_text(
        self,
        op: str,
        operands: list[Any],
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
    ) -> None:
        text = self.state.text
        if op == "BT":
            self.state.text = _TextState(in_text=True)
            return
        if op == "ET":
            text.in_text = False
            return
        if not text.in_text:
            return
        if op == "Tf" and len(operands) >= 2:
            text.font_name = str(operands[-2]).lstrip("/")
            size = _number(operands[-1])
            if size is not None:
                text.font_size = abs(size)
            return
        if op in ("Tc", "Tw", "Tz", "TL", "Tr", "Ts") and operands:
            val = _number(operands[-1])
            if val is None:
                return
            if op == "Tc":
                text.char_spacing = val
            elif op == "Tw":
                text.word_spacing = val
            elif op == "Tz":
                text.horizontal_scale = val / 100.0
            elif op == "TL":
                text.leading = val
            elif op == "Tr":
                text.rendering_mode = int(val)
            elif op == "Ts":
                text.rise = val
            return
        if op == "Tm":
            vals = _last_numbers(operands, 6)
            if vals:
                text.text_matrix = tuple(vals)  # type: ignore[assignment]
                text.line_matrix = text.text_matrix
            return
        if op in ("Td", "TD"):
            vals = _last_numbers(operands, 2)
            if vals:
                tx, ty = vals
                if op == "TD":
                    text.leading = -ty
                text.line_matrix = _multiply(
                    (1.0, 0.0, 0.0, 1.0, tx, ty), text.line_matrix
                )
                text.text_matrix = text.line_matrix
            return
        if op == "T*":
            text.line_matrix = _multiply(
                (1.0, 0.0, 0.0, 1.0, 0.0, -text.leading), text.line_matrix
            )
            text.text_matrix = text.line_matrix
            return
        if op == "Tj" and operands:
            self._show_text(operands[-1], resources_cos, resources_plain)
            return
        if op == "TJ" and operands and isinstance(operands[-1], list):
            for item in operands[-1]:
                if isinstance(item, (bytes, bytearray)):
                    self._show_text(bytes(item), resources_cos, resources_plain)
                elif isinstance(item, (int, float)):
                    adjust = (
                        -float(item) / 1000.0 * text.font_size * text.horizontal_scale
                    )
                    text.text_matrix = _multiply(
                        (1.0, 0.0, 0.0, 1.0, adjust, 0.0), text.text_matrix
                    )
            return
        if op == "'":
            self._handle_text("T*", [], resources_cos, resources_plain)
            if operands:
                self._show_text(operands[-1], resources_cos, resources_plain)
            return
        if op == '"':
            if len(operands) >= 3:
                aw = _number(operands[-3])
                ac = _number(operands[-2])
                if aw is not None:
                    text.word_spacing = aw
                if ac is not None:
                    text.char_spacing = ac
            self._handle_text("T*", [], resources_cos, resources_plain)
            if operands:
                self._show_text(operands[-1], resources_cos, resources_plain)

    def _show_text(
        self,
        raw: Any,
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
    ) -> None:
        text = self.state.text
        # Modes 3 (invisible) and 7 (clip only) add nothing to the raster.
        if text.rendering_mode in (3, 7):
            return
        if not isinstance(raw, (bytes, bytearray)):
            return
        raw = bytes(raw)
        font = self._resolve_glyph_font(text.font_name, resources_cos)
        if font is None:
            self._show_text_boxes(raw)
            return
        units_per_em = font.outlines.units_per_em
        joined = (
            self._joined_substitute_gids(raw, font)
            if self.shape_substitute_text and font.shaping_program is not None
            else None
        )
        size = text.font_size
        for index, (gid, width_1000, applies_word, cid) in enumerate(
            font.iter_glyphs(raw)
        ):
            draw_gid = joined[index] if joined is not None else gid
            if font.vertical:
                # Vertical writing: displace the glyph by its position vector and
                # advance downward by the CID's /W2 (or /DW2) displacement.
                if cid is not None and font.vertical_metrics_1000 is not None:
                    w1y, v1x, v1y = font.vertical_metrics_1000(cid)
                else:
                    w1y, v1x, v1y = -1000.0, font.default_width_1000 / 2.0, 880.0
                if draw_gid is not None:
                    contours = font.outlines.outline(draw_gid)
                    if contours:
                        self._fill_glyph(
                            contours,
                            units_per_em,
                            offset=(-v1x / 1000.0 * size, -v1y / 1000.0 * size),
                        )
                advance = w1y / 1000.0 * size + text.char_spacing
                text.text_matrix = _multiply(
                    (1.0, 0.0, 0.0, 1.0, 0.0, advance), text.text_matrix
                )
                continue
            if draw_gid is not None:
                contours = font.outlines.outline(draw_gid)
                if contours:
                    self._fill_glyph(contours, units_per_em)
            advance = width_1000 / 1000.0 * size + text.char_spacing
            if applies_word:
                advance += text.word_spacing
            advance *= text.horizontal_scale
            text.text_matrix = _multiply(
                (1.0, 0.0, 0.0, 1.0, advance, 0.0), text.text_matrix
            )

    def _joined_substitute_gids(
        self, raw: bytes, font: _GlyphFont
    ) -> list[int | None] | None:
        """Per-code shaped glyph ids for a complex-script substitute run.

        Reconstructs the run's logical text, applies order-preserving cursive
        joining against the substitute program, and returns a glyph id (or
        ``None`` to draw nothing) aligned to ``iter_glyphs`` order. Returns
        ``None`` to keep the plain per-code path -- for Latin runs, when the
        text-layout extra is missing, or when reconstruction/shaping fails.
        """
        if font.bytes_per_code != 1 or font.code_to_unicode is None:
            return None
        codepoints: list[int] = []
        for code in raw:
            cp = font.code_to_unicode(code)
            if cp is None:
                return None
            codepoints.append(cp)
        logical = "".join(chr(cp) for cp in codepoints)
        from .text_layout import needs_shaping, shape_join_preserving

        if not needs_shaping(logical):
            return None
        try:
            mapping = shape_join_preserving(font.shaping_program, logical)
        except PdfResourceLimitException:
            raise
        except Exception:
            return None
        if mapping is None:
            return None
        return [mapping.get(index) for index in range(len(codepoints))]

    def _show_text_boxes(self, raw: bytes) -> None:
        """Fallback for non-TrueType fonts: draw a box per visible glyph."""
        text = self.state.text
        decoded = raw.decode("latin-1", errors="replace")
        for ch in decoded:
            if ch in "\r\n":
                continue
            glyph_w = text.font_size * 0.6 * text.horizontal_scale
            if ch == " ":
                advance = glyph_w + text.char_spacing + text.word_spacing
                text.text_matrix = _multiply(
                    (1.0, 0.0, 0.0, 1.0, advance, 0.0), text.text_matrix
                )
                continue
            self._draw_glyph_box(glyph_w, text.font_size)
            advance = glyph_w + text.char_spacing
            text.text_matrix = _multiply(
                (1.0, 0.0, 0.0, 1.0, advance, 0.0), text.text_matrix
            )

    def _fill_glyph(
        self,
        contours: list[list[Point]],
        units_per_em: int,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        """Fill a glyph's font-unit contours through the text/CTM transform.

        *offset* is a text-space shift of the glyph origin (used by vertical
        writing to apply the CID position vector).
        """
        text = self.state.text
        if units_per_em <= 0 or text.font_size == 0:
            return
        scale = text.font_size / units_per_em
        # glyph space -> text space: scale by font size, apply horizontal
        # scaling on x, the text rise plus any vertical offset on y.
        glyph_to_text: Matrix = (
            scale * text.horizontal_scale,
            0.0,
            0.0,
            scale,
            offset[0],
            text.rise + offset[1],
        )
        base = _multiply(
            _multiply(self.state.ctm, text.text_matrix), glyph_to_text
        )
        pixel_contours: list[list[Point]] = []
        for contour in contours:
            polygon = []
            for gx, gy in contour:
                ux, uy = _transform_point(base, gx, gy)
                polygon.append(self._user_to_pixel(ux, uy))
            if len(polygon) >= 3:
                pixel_contours.append(polygon)
        if pixel_contours:
            self._fill_contours_nonzero(
                pixel_contours, self.state.fill_color, self.state.fill_alpha
            )

    def _fill_contours_nonzero(
        self, contours: list[list[Point]], color: Color, alpha: float
    ) -> None:
        """Scanline-fill multiple contours together using the nonzero rule.

        Filling each contour independently would paint a glyph's counters (the
        hole in ``o``/``e``/``a``); the nonzero winding rule across all contours
        leaves them open, matching TrueType's fill convention.
        """
        ys = [p[1] for contour in contours for p in contour]
        if not ys:
            return
        min_y = max(0, math.floor(min(ys)))
        max_y = min(self.height - 1, math.ceil(max(ys)))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            crossings: list[tuple[float, int]] = []
            for contour in contours:
                n = len(contour)
                for i in range(n):
                    x0, y0 = contour[i]
                    x1, y1 = contour[(i + 1) % n]
                    if y0 == y1:
                        continue
                    if (y0 <= scan_y < y1) or (y1 <= scan_y < y0):
                        t = (scan_y - y0) / (y1 - y0)
                        crossings.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
            if len(crossings) < 2:
                continue
            crossings.sort()
            winding = 0
            for i in range(len(crossings) - 1):
                winding += crossings[i][1]
                if winding == 0:
                    continue
                xa, xb = crossings[i][0], crossings[i + 1][0]
                x_start = math.ceil(xa - 0.5)
                x_end = math.floor(xb - 0.5)
                if x_end < x_start:
                    # Sub-pixel span: keep one pixel so thin stems do not drop out.
                    if xb <= xa:
                        continue
                    x_start = x_end = math.floor((xa + xb) / 2.0)
                x_start = max(0, x_start)
                x_end = min(self.width - 1, x_end)
                for x in range(x_start, x_end + 1):
                    self._composite_pixel(x, y, color, alpha)

    def _draw_glyph_box(self, width: float, height: float) -> None:
        text = self.state.text
        base = _multiply(self.state.ctm, text.text_matrix)
        if text.rise:
            base = _multiply(base, (1.0, 0.0, 0.0, 1.0, 0.0, text.rise))
        pad = max(0.5, height * 0.08)
        corners = [
            _transform_point(base, pad, pad),
            _transform_point(base, max(pad, width - pad), pad),
            _transform_point(base, max(pad, width - pad), max(pad, height - pad)),
            _transform_point(base, pad, max(pad, height - pad)),
        ]
        polygon = [self._user_to_pixel(x, y) for x, y in corners]
        self._fill_polygon_pixels(
            polygon, self.state.fill_color, self.state.fill_alpha
        )

    # -- embedded TrueType font resolution --------------------------------

    def _resolve_glyph_font(
        self, name: str | None, resources_cos: PdfDictionary | None
    ) -> _GlyphFont | None:
        if name is None or resources_cos is None:
            return None
        if name in self._font_cache:
            return self._font_cache[name]
        try:
            font = self._build_glyph_font(name, resources_cos)
        except (struct.error, IndexError, ValueError, TypeError, KeyError):
            font = None
        self._font_cache[name] = font
        return font

    def _build_glyph_font(
        self, name: str, resources_cos: PdfDictionary
    ) -> _GlyphFont | None:
        fonts = self._resource_dict(resources_cos, "Font")
        if fonts is None:
            return None
        font_dict = self._resolve(fonts.mapping.get(PdfName(name)))
        if not isinstance(font_dict, PdfDictionary):
            return None
        subtype = self._cos_name(font_dict.mapping.get(PdfName("Subtype")))
        if subtype == "Type0":
            return self._build_type0_font(font_dict)
        if subtype not in ("TrueType", "Type1", "MMType1"):
            return None
        font: _GlyphFont | None = None
        if subtype == "TrueType":
            font = self._build_simple_truetype_font(font_dict)
        else:  # Type1 / MMType1
            descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
            if isinstance(descriptor, PdfDictionary):
                if descriptor.mapping.get(PdfName("FontFile3")) is not None:
                    font = self._build_simple_cff_font(font_dict)
                elif descriptor.mapping.get(PdfName("FontFile")) is not None:
                    font = self._build_type1_font(font_dict, descriptor)
        # No embedded program (or it failed to parse): fall back to a bundled
        # substitute so the Standard-14 fonts render as real glyphs instead of
        # boxes (Liberation for the Latin families, DejaVu shape subsets for
        # Symbol/ZapfDingbats).
        if font is None:
            font = self._build_substitute_font(font_dict)
        return font

    def _descriptor_signals(
        self, font_dict: PdfDictionary, descriptor: Any = None
    ) -> tuple[str | None, int, float, float | None]:
        """Return ``(base_font, flags, italic_angle, font_weight)``."""
        base = self._cos_name(font_dict.mapping.get(PdfName("BaseFont")))
        if descriptor is None:
            descriptor = self._resolve(
                font_dict.mapping.get(PdfName("FontDescriptor"))
            )
        flags = 0
        italic_angle = 0.0
        font_weight: float | None = None
        if isinstance(descriptor, PdfDictionary):
            f = self._cos_number(descriptor.mapping.get(PdfName("Flags")))
            if f is not None:
                flags = int(f)
            ia = self._cos_number(descriptor.mapping.get(PdfName("ItalicAngle")))
            if ia is not None:
                italic_angle = float(ia)
            fw = self._cos_number(descriptor.mapping.get(PdfName("FontWeight")))
            if fw is not None:
                font_weight = float(fw)
        return base, flags, italic_angle, font_weight

    def _external_face(
        self,
        base: str | None,
        *,
        flags: int = 0,
        italic_angle: float = 0.0,
        font_weight: float | None = None,
    ) -> ResolvedFace | None:
        """Resolve *base* through the caller's font sources, or ``None``."""
        resolver = self._font_resolver
        if resolver is None:
            return None
        try:
            return resolver.by_name(
                base,
                flags=flags,
                italic_angle=italic_angle,
                font_weight=font_weight,
            )
        except PdfResourceLimitException:
            raise
        except (OSError, struct.error, ValueError, TypeError, KeyError):
            return None

    def _face_outlines(self, face: ResolvedFace) -> Any:
        """Build an outline source for *face*, or ``None`` when unusable."""
        outlines: Any
        if face.is_cff:
            outlines = CffOutlines(face.data, variation=face.variation)
        else:
            outlines = TrueTypeOutlines(face.data)
        return outlines if outlines.ok else None

    def _build_external_simple_font(
        self, font_dict: PdfDictionary, face: ResolvedFace
    ) -> _GlyphFont | None:
        """Draw a simple font with an external face, keeping the PDF widths."""
        outlines = self._face_outlines(face)
        if outlines is None:
            return None
        code_to_gid, code_to_unicode = self._external_code_to_gid(font_dict, face)
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(
            outlines,
            code_to_gid,
            width_1000,
            bytes_per_code=1,
            shaping_program=face.data,
            code_to_unicode=lambda code, _m=code_to_unicode: _m.get(code),
        )

    def _external_code_to_gid(
        self, font_dict: PdfDictionary, face: ResolvedFace
    ) -> tuple[Callable[[int], int | None], dict[int, int]]:
        """``code -> gid`` and ``code -> unicode`` for an external simple face."""
        from .font_subset import read_symbol_code_to_gid, read_unicode_cmap

        symbol = read_symbol_code_to_gid(face.data)
        unicode_map = read_unicode_cmap(face.data)
        explicit: dict[int, int] = {}
        if hasattr(self.pdf, "_simple_code_to_unicode"):
            try:
                explicit = self.pdf._simple_code_to_unicode(font_dict) or {}
            except PdfResourceLimitException:
                raise
            except Exception:
                explicit = {}
        code_to_unicode: dict[int, int] = {}
        for code in range(256):
            try:
                code_to_unicode[code] = ord(bytes([code]).decode("cp1252"))
            except (UnicodeDecodeError, TypeError):
                pass
        code_to_unicode.update(explicit)
        # A symbolic font with no /Encoding of its own is addressed through its
        # (3,0) cmap, where cp1252 would silently pick the wrong glyphs.
        symbol_first = not explicit

        def resolve(
            code: int,
            _sym=symbol,
            _uni=unicode_map,
            _c2u=code_to_unicode,
            _sym_first=symbol_first,
        ) -> int | None:
            if _sym_first and _sym:
                gid = _sym.get(code) or _sym.get(0xF000 + code)
                if gid:
                    return gid
            cp = _c2u.get(code)
            if cp is not None and _uni:
                gid = _uni.get(cp)
                if gid:
                    return gid
            if _sym:
                gid = _sym.get(code) or _sym.get(0xF000 + code)
                if gid:
                    return gid
            if _uni:
                gid = _uni.get(code)
                if gid:
                    return gid
            return None

        return resolve, code_to_unicode

    def _build_substitute_font(
        self, font_dict: PdfDictionary
    ) -> _GlyphFont | None:
        base, flags, italic_angle, font_weight = self._descriptor_signals(font_dict)
        # A caller-supplied or system face named by the document wins: it is the
        # font the producer meant, where a bundled substitute is a stand-in.
        face = self._external_face(
            base, flags=flags, italic_angle=italic_angle, font_weight=font_weight
        )
        if face is not None:
            font = self._build_external_simple_font(font_dict, face)
            if font is not None:
                return font
        key = resolve_substitute_key(
            base, flags=flags, italic_angle=italic_angle, font_weight=font_weight
        )
        sfnt = load_substitute_sfnt(key)
        if sfnt is None:
            return None
        outlines = TrueTypeOutlines(sfnt)
        if not outlines.ok:
            return None
        code_to_gid, code_to_unicode = self._substitute_code_to_gid(
            font_dict, outlines, key
        )
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(
            outlines,
            code_to_gid,
            width_1000,
            bytes_per_code=1,
            shaping_program=sfnt,
            code_to_unicode=lambda code, _m=code_to_unicode: _m.get(code),
        )

    def _substitute_code_to_gid(
        self, font_dict: PdfDictionary, outlines: TrueTypeOutlines, key: str
    ) -> tuple[Callable[[int], int | None], dict[int, int]]:
        from .font_subset import read_unicode_cmap
        from .std_font_data import substitute_code_to_unicode

        uni = read_unicode_cmap(outlines._data)
        # Symbol/ZapfDingbats text uses the fonts' built-in encodings; Latin
        # faces default to WinAnsi (cp1252) -- the de-facto Standard-14 Latin
        # encoding used when a font omits /Encoding. Then overlay any explicit
        # PDF /Encoding (named base and/or /Differences) the document declares.
        builtin = substitute_code_to_unicode(key)
        if builtin is not None:
            code_to_unicode = dict(builtin)
        else:
            code_to_unicode = {}
            for code in range(256):
                try:
                    code_to_unicode[code] = ord(bytes([code]).decode("cp1252"))
                except (UnicodeDecodeError, TypeError):
                    pass
        if hasattr(self.pdf, "_simple_code_to_unicode"):
            try:
                explicit = self.pdf._simple_code_to_unicode(font_dict) or {}
                code_to_unicode.update(explicit)
            except PdfResourceLimitException:
                raise
            except Exception:
                pass

        def resolve(code: int, _uni=uni, _c2u=code_to_unicode) -> int | None:
            cp = _c2u.get(code)
            if cp is not None and _uni:
                gid = _uni.get(cp)
                if gid:
                    return gid
            if _uni:  # last resort: treat the byte itself as a codepoint (ASCII)
                gid = _uni.get(code)
                if gid:
                    return gid
            return None

        return resolve, code_to_unicode

    def _build_type0_font(self, font_dict: PdfDictionary) -> _GlyphFont | None:
        encoding = self._cos_name(font_dict.mapping.get(PdfName("Encoding")))
        identity = encoding in ("Identity-H", "Identity-V", "Identity")
        descendants = self._resolve(font_dict.mapping.get(PdfName("DescendantFonts")))
        cidfont = None
        if isinstance(descendants, PdfArray) and descendants.items:
            cidfont = self._resolve(descendants.items[0])
        if not isinstance(cidfont, PdfDictionary):
            return None
        cmap = None
        if not identity:
            # A named /Encoding resolves against the descendant CIDSystemInfo to
            # one of the bundled predefined CMaps; an /Encoding *stream* is
            # decoded directly. Unknown predefined names still fall back to boxes.
            cmap = self._predefined_cmap_encoding(font_dict, cidfont)
            if cmap is None:
                cmap = self._stream_encoding_cmap(font_dict)
            if cmap is None:
                return None
        cid_subtype = self._cos_name(cidfont.mapping.get(PdfName("Subtype")))
        descriptor = self._resolve(cidfont.mapping.get(PdfName("FontDescriptor")))
        width_1000 = self._cid_widths(cidfont)
        outlines: Any = None
        cid_to_gid: Callable[[int], int | None] | None = None
        if cid_subtype == "CIDFontType2":
            outlines = self._load_truetype_outlines(descriptor)
            if outlines is not None:
                cid_to_gid = self._cid_to_gid(cidfont)
        elif cid_subtype == "CIDFontType0":
            program = self._load_fontfile3(descriptor)
            if program:
                candidate = CffOutlines(program)
                if candidate.ok:
                    outlines = candidate
                    cid_to_gid = self._cff_cid_to_gid(program)
        else:
            return None
        if outlines is None or cid_to_gid is None:
            # No embedded program. Draw through an external face indexed by
            # Unicode (Adobe's CID-to-Unicode table for a predefined
            # collection, or the font's own /ToUnicode), keeping the PDF's /W
            # advances so glyphs land exactly where the producer placed them.
            substitute = self._build_type0_substitute(
                font_dict, cidfont, cmap, descriptor
            )
            if substitute is None:
                return None
            outlines, cid_to_gid = substitute
        dw = self._cos_number(cidfont.mapping.get(PdfName("DW")))
        default_width = float(dw) if dw is not None else 1000.0
        vertical = bool(getattr(cmap, "vertical", False))
        vertical_metrics = (
            self._cid_vertical_metrics(cidfont, width_1000) if vertical else None
        )
        return _GlyphFont(
            outlines,
            cid_to_gid,
            width_1000,
            bytes_per_code=2,
            cmap=cmap,
            cmap_budget=self._load_budget,
            default_width_1000=default_width,
            vertical=vertical,
            vertical_metrics_1000=vertical_metrics,
        )

    def _build_type0_substitute(
        self,
        font_dict: PdfDictionary,
        cidfont: PdfDictionary,
        cmap: Any,
        descriptor: Any,
    ) -> tuple[Any, Callable[[int], int | None]] | None:
        """Outline source and ``cid -> gid`` for a non-embedded composite font.

        Returns ``None`` -- leaving the caller's glyph-box fallback in place --
        when no font sources are configured, the CIDs cannot be mapped to
        Unicode, or nothing available covers the text.
        """
        from .font_subset import read_unicode_cmap

        resolver = self._font_resolver
        if resolver is None:
            return None
        lookup = self._cid_to_unicode_lookup(font_dict, cidfont, cmap)
        if lookup is None:
            return None
        cid_text, samples = lookup
        base, flags, italic_angle, font_weight = self._descriptor_signals(
            font_dict, descriptor
        )
        face = self._external_face(
            base, flags=flags, italic_angle=italic_angle, font_weight=font_weight
        )
        if face is None:
            bold, italic = _wanted_style(base, flags, italic_angle, font_weight)
            try:
                face = resolver.by_ordering(
                    self._cid_ordering(cidfont),
                    serif=bool(flags & 2),
                    bold=bold,
                    italic=italic,
                    probe_scalars=samples,
                )
            except PdfResourceLimitException:
                raise
            except (OSError, struct.error, ValueError, TypeError, KeyError):
                face = None
        if face is None:
            return None
        outlines = self._face_outlines(face)
        if outlines is None:
            return None
        unicode_map = read_unicode_cmap(face.data)
        if not unicode_map:
            return None

        def cid_to_gid(
            cid: int, _text=cid_text, _uni=unicode_map
        ) -> int | None:
            text = _text(cid)
            if not text:
                return None
            return _uni.get(ord(text[0])) or None

        return outlines, cid_to_gid

    def _cid_to_unicode_lookup(
        self, font_dict: PdfDictionary, cidfont: PdfDictionary, cmap: Any
    ) -> tuple[Callable[[int], str | None], tuple[int, ...]] | None:
        """Return ``(cid -> text, sample scalars)`` for a composite font.

        The font's own ``/ToUnicode`` wins where it maps a code, since it is
        written for this document; Adobe's CID-to-Unicode table for the
        descendant's character collection fills in the rest.
        """
        from .predefined_cmaps import cid_to_unicode_text

        ordering = self._cid_ordering(cidfont)
        table: dict[int, str] = {}
        to_unicode = None
        if hasattr(self.pdf, "_font_to_unicode_map"):
            try:
                to_unicode = self.pdf._font_to_unicode_map(font_dict)
            except PdfResourceLimitException:
                raise
            except Exception:
                to_unicode = None
        if to_unicode:
            for code, text in to_unicode.items():
                if not text:
                    continue
                cid = self._code_to_cid(code, cmap)
                if cid is not None:
                    table.setdefault(cid, text)
        if not table and not ordering:
            return None

        def lookup(cid: int, _table=table, _ordering=ordering) -> str | None:
            text = _table.get(cid)
            if text:
                return text
            return cid_to_unicode_text(_ordering, cid) if _ordering else None

        samples = tuple(
            dict.fromkeys(
                ord(text[0]) for text in list(table.values())[:32] if text
            )
        )
        return lookup, samples

    def _code_to_cid(self, code: bytes, cmap: Any) -> int | None:
        """Map raw code bytes to a CID under *cmap* (``None`` = Identity)."""
        if cmap is None:
            return int.from_bytes(code, "big") if code else None
        resolver = getattr(cmap, "cid_for", None)
        if callable(resolver):
            try:
                return resolver(code)
            except PdfResourceLimitException:
                raise
            except Exception:
                return None
        mapping = getattr(cmap, "code_to_cid", None)
        if isinstance(mapping, dict):
            return mapping.get(code)
        return None

    def _cid_ordering(self, cidfont: PdfDictionary) -> str:
        """The descendant's ``/CIDSystemInfo /Ordering``, or ``""``."""
        info = self._resolve(cidfont.mapping.get(PdfName("CIDSystemInfo")))
        if not isinstance(info, PdfDictionary):
            return ""
        value = self._resolve(info.mapping.get(PdfName("Ordering")))
        raw = getattr(value, "value", value)
        if isinstance(raw, bytes):
            try:
                return raw.decode("ascii")
            except UnicodeDecodeError:
                return ""
        return raw if isinstance(raw, str) else ""

    def _cid_vertical_metrics(
        self, cidfont: PdfDictionary, width_1000: Callable[[int], float]
    ) -> Callable[[int], tuple[float, float, float]]:
        """Return a ``cid -> (w1y, v1x, v1y)`` callable from ``/W2`` and ``/DW2``.

        Defaults follow PDF 32000-1 9.7.4.3: position vector ``v1x = w0/2`` and
        ``/DW2`` ``[v1y, w1y]`` defaulting to ``[880, -1000]`` (1000 units).
        """
        from .content_stream_parser import load_cid_vertical_metrics

        default_v1y, default_w1y = 880.0, -1000.0
        dw2 = self._resolve(cidfont.mapping.get(PdfName("DW2")))
        if isinstance(dw2, PdfArray) and len(dw2.items) >= 2:
            v = self._cos_number(dw2.items[0])
            w = self._cos_number(dw2.items[1])
            if v is not None:
                default_v1y = float(v)
            if w is not None:
                default_w1y = float(w)
        w2_list = None
        if hasattr(self.pdf, "_convert_cos_to_dict"):
            w2_list = self.pdf._convert_cos_to_dict(cidfont.mapping.get(PdfName("W2")))
        metrics = load_cid_vertical_metrics(w2_list, budget=self._load_budget)

        def vertical_metric(
            cid: int,
            _m=metrics,
            _w1y=default_w1y,
            _v1y=default_v1y,
            _width=width_1000,
        ) -> tuple[float, float, float]:
            return _m.get(cid, (_w1y, _width(cid) / 2.0, _v1y))

        return vertical_metric

    def _predefined_cmap_encoding(
        self, font_dict: PdfDictionary, cidfont: PdfDictionary
    ) -> Any:
        """Resolve a named predefined CMap's compact code->CID view, or None.

        Delegates to the engine so the renderer shares the exact allowlist,
        CIDSystemInfo matching, and ``usecmap`` handling used by extraction and
        editing. Resource-limit errors propagate; anything else means the CMap
        is unsupported and the caller draws boxes.
        """
        resolver = getattr(self.pdf, "_predefined_cmap_encoding_for_font", None)
        if resolver is None:
            return None
        try:
            return resolver(font_dict, cidfont)
        except PdfResourceLimitException:
            raise
        except Exception:
            return None

    def _stream_encoding_cmap(self, font_dict: PdfDictionary) -> _StreamCMap | None:
        """Decode an embedded ``/Encoding`` CMap stream into a render decoder.

        Reuses the extraction parsers so a Type0 font whose encoding is a CMap
        *stream* (rather than Identity or a bundled predefined name) renders
        instead of boxing. Returns ``None`` when there is no stream, it declares
        no CID ranges, or its codespaces are ambiguous.
        """
        from .content_stream_parser import (
            parse_encoding_cmap,
            parse_encoding_cmap_codespaces,
            parse_encoding_cmap_wmode,
        )

        ref = font_dict.mapping.get(PdfName("Encoding"))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return None
        decoder = getattr(self.pdf, "_decode_cos_stream", None)
        if decoder is None:
            return None
        try:
            data = decoder(stream, ref)
        except PdfResourceLimitException:
            raise
        except Exception:
            return None

        code_to_cid, _lengths = parse_encoding_cmap(data, budget=self._load_budget)
        if not code_to_cid:
            return None
        codespaces = parse_encoding_cmap_codespaces(data, budget=self._load_budget)
        if not codespaces:
            # No declared codespaces: synthesize one only when every code shares
            # a single, unambiguous byte length.
            key_lengths = {len(code) for code in code_to_cid}
            if len(key_lengths) != 1:
                return None
            (length,) = tuple(key_lengths)
            codespaces = ((bytes(length), b"\xff" * length),)
        vertical = parse_encoding_cmap_wmode(data, budget=self._load_budget) == 1
        return _StreamCMap(code_to_cid, codespaces, vertical)

    def _build_simple_cff_font(
        self, font_dict: PdfDictionary
    ) -> _GlyphFont | None:
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        program = self._load_fontfile3(descriptor)
        if not program:
            return None
        outlines = CffOutlines(program)
        if not outlines.ok:
            return None
        # Prefer the PDF /Encoding (base + /Differences) or a predefined encoding
        # resolved through the CFF charset (name -> gid); fall back to the CFF's
        # own custom code -> gid Encoding for any code without a name.
        name_to_gid = outlines.name_to_gid()
        custom = outlines.encoding_code_to_gid()
        if not name_to_gid and not custom:
            return None
        code_to_name = self._simple_code_to_name(
            font_dict, {}, has_custom_gid=bool(custom)
        )

        def code_to_gid(code, _c2n=code_to_name, _n2g=name_to_gid, _cm=custom):
            name = _c2n.get(code)
            if name is not None:
                gid = _n2g.get(name)
                if gid is not None:
                    return gid
            return _cm.get(code) if _cm else None

        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(
            outlines,
            code_to_gid,
            width_1000,
            bytes_per_code=1,
            code_to_unicode=self._code_to_unicode_lookup(code_to_name),
        )

    def _code_to_unicode_lookup(
        self, code_to_name: dict[int, str]
    ) -> Callable[[int], int | None] | None:
        """Build a code -> single Unicode scalar lookup from a code -> name map."""
        from .agl import glyph_name_to_unicode

        mapping: dict[int, int] = {}
        for code, name in code_to_name.items():
            mapped = glyph_name_to_unicode(name)
            if mapped is not None and len(mapped) == 1:
                mapping[code] = ord(mapped)
        if not mapping:
            return None
        return lambda code, _m=mapping: _m.get(code)

    def _cff_cid_to_gid(self, program: bytes) -> Callable[[int], int | None]:
        from .font_subset_cff import cff_charset_cid_to_gid

        charset = cff_charset_cid_to_gid(program)
        if charset:
            return lambda cid, _m=charset: _m.get(cid, cid)
        return lambda cid: cid  # identity / predefined charset: CID == GID.

    def _build_type1_font(
        self, font_dict: PdfDictionary, descriptor: PdfDictionary
    ) -> _GlyphFont | None:
        loaded = self._load_fontfile1(descriptor)
        if loaded is None:
            return None
        program, length1, length2 = loaded
        if not program:
            return None
        outlines = Type1Outlines(program, length1, length2)
        if not outlines.ok:
            return None
        code_to_gid = self._type1_code_to_gid(font_dict, outlines)
        code_to_name = self._simple_code_to_name(
            font_dict, outlines.builtin_encoding, has_custom_gid=False
        )
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(
            outlines,
            code_to_gid,
            width_1000,
            bytes_per_code=1,
            code_to_unicode=self._code_to_unicode_lookup(code_to_name),
        )

    def _load_fontfile1(
        self, descriptor: PdfDictionary
    ) -> tuple[bytes, int | None, int | None] | None:
        ref = descriptor.mapping.get(PdfName("FontFile"))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return None
        program = stream.content
        if hasattr(self.pdf, "_decode_cos_stream"):
            try:
                program = self.pdf._decode_cos_stream(stream, ref)
            except PdfResourceLimitException:
                raise
            except Exception:
                program = stream.content
        length1 = self._cos_number(stream.mapping.get(PdfName("Length1")))
        length2 = self._cos_number(stream.mapping.get(PdfName("Length2")))
        return (
            program,
            int(length1) if length1 else None,
            int(length2) if length2 else None,
        )

    def _simple_code_to_name(
        self,
        font_dict: PdfDictionary,
        builtin_code_to_name: dict[int, str],
        has_custom_gid: bool,
    ) -> dict[int, str]:
        """Resolve a simple font's code -> glyph name table.

        The base is the PDF ``/Encoding`` (a predefined name, or a dictionary's
        ``/BaseEncoding``); failing that, the font's own built-in encoding, then
        StandardEncoding -- unless the font already supplies a custom code -> gid
        map, in which case no name base is imposed. ``/Differences`` overlay last.
        """
        from .agl import base_encoding_table

        enc_ref = font_dict.mapping.get(PdfName("Encoding"))
        base_name = self._cos_name(enc_ref)
        enc_obj = self._resolve(enc_ref)
        differences = None
        if isinstance(enc_obj, PdfDictionary):
            base_name = self._cos_name(enc_obj.mapping.get(PdfName("BaseEncoding")))
            differences = self._resolve(enc_obj.mapping.get(PdfName("Differences")))

        base: dict[int, str] | None = None
        if base_name:
            table = base_encoding_table(base_name)
            if table is not None:
                base = {code: name for code, name in enumerate(table) if name}
        if base is None:
            if builtin_code_to_name:
                base = dict(builtin_code_to_name)
            elif has_custom_gid:
                base = {}
            else:
                standard = base_encoding_table("StandardEncoding") or ()
                base = {code: name for code, name in enumerate(standard) if name}

        code_to_name = dict(base)
        if isinstance(differences, PdfArray):
            current = 0
            for item in differences.items:
                item = self._resolve(item)
                if isinstance(item, PdfNumber):
                    current = int(item.value)
                elif isinstance(item, PdfName):
                    code_to_name[current] = item.name.lstrip("/")
                    current += 1
        return code_to_name

    def _type1_code_to_gid(
        self, font_dict: PdfDictionary, outlines: Type1Outlines
    ) -> Callable[[int], int | None]:
        # Resolve a code to a glyph name (built-in encoding, then the PDF base
        # encoding and /Differences) and look up the font's own name -> gid.
        code_to_name = self._simple_code_to_name(
            font_dict, outlines.builtin_encoding, has_custom_gid=False
        )
        name_to_gid = outlines.name_to_gid

        def resolve(code: int, _c2n=code_to_name, _n2g=name_to_gid) -> int | None:
            name = _c2n.get(code)
            return _n2g.get(name) if name is not None else None

        return resolve

    def _build_simple_truetype_font(
        self, font_dict: PdfDictionary
    ) -> _GlyphFont | None:
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        outlines = self._load_truetype_outlines(descriptor)
        if outlines is None:
            return None
        code_to_gid = self._simple_code_to_gid(font_dict, outlines)
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(outlines, code_to_gid, width_1000, bytes_per_code=1)

    def _load_truetype_outlines(
        self, descriptor: Any
    ) -> TrueTypeOutlines | None:
        if not isinstance(descriptor, PdfDictionary):
            return None
        program = self._load_fontfile2(descriptor)
        if not program:
            return None
        outlines = TrueTypeOutlines(program)
        return outlines if outlines.ok else None

    def _load_fontfile2(self, descriptor: PdfDictionary) -> bytes | None:
        return self._load_font_program(descriptor, "FontFile2")

    def _load_fontfile3(self, descriptor: Any) -> bytes | None:
        if not isinstance(descriptor, PdfDictionary):
            return None
        return self._load_font_program(descriptor, "FontFile3")

    def _load_font_program(
        self, descriptor: PdfDictionary, key: str
    ) -> bytes | None:
        ref = descriptor.mapping.get(PdfName(key))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return None
        if hasattr(self.pdf, "_decode_cos_stream"):
            try:
                return self.pdf._decode_cos_stream(stream, ref)
            except PdfResourceLimitException:
                raise
            except Exception:
                pass
        return stream.content

    def _cid_to_gid(self, cidfont: PdfDictionary) -> Callable[[int], int | None]:
        if hasattr(self.pdf, "_build_cid_to_gid"):
            try:
                return self.pdf._build_cid_to_gid(cidfont)
            except PdfResourceLimitException:
                raise
            except Exception:
                pass
        return lambda cid: cid

    def _cid_widths(self, cidfont: PdfDictionary) -> Callable[[int], float]:
        dw = self._cos_number(cidfont.mapping.get(PdfName("DW")))
        default = dw if dw is not None else 1000.0
        table: dict[int, float] = {}
        w = self._resolve(cidfont.mapping.get(PdfName("W")))
        if isinstance(w, PdfArray):
            items = w.items
            self._load_budget.check(
                len(items),
                "max_container_items",
                "rasterizer CID width array items",
            )
            i = 0
            while i < len(items):
                c = self._cos_number(items[i])
                if c is None:
                    break
                nxt = self._resolve(items[i + 1]) if i + 1 < len(items) else None
                if isinstance(nxt, PdfArray):
                    self._load_budget.check(
                        len(nxt.items),
                        "max_container_items",
                        "rasterizer CID width subarray items",
                    )
                    for j, item in enumerate(nxt.items):
                        wv = self._cos_number(item)
                        if wv is not None:
                            table[int(c) + j] = wv
                            self._load_budget.check(
                                len(table),
                                "max_container_items",
                                "rasterizer CID width mappings",
                            )
                    i += 2
                else:
                    clast = self._cos_number(nxt)
                    wv = (
                        self._cos_number(items[i + 2])
                        if i + 2 < len(items)
                        else None
                    )
                    if clast is None or wv is None:
                        break
                    first_cid = int(c)
                    last_cid = int(clast)
                    if last_cid >= first_cid:
                        self._load_budget.check(
                            last_cid - first_cid + 1,
                            "max_container_items",
                            "rasterizer CID width range entries",
                        )
                        for cid in range(first_cid, last_cid + 1):
                            table[cid] = wv
                            self._load_budget.check(
                                len(table),
                                "max_container_items",
                                "rasterizer CID width mappings",
                            )
                    i += 3
        return lambda cid, _t=table, _d=default: _t.get(cid, _d)

    def _simple_code_to_gid(
        self, font_dict: PdfDictionary, outlines: TrueTypeOutlines
    ) -> Callable[[int], int | None]:
        from .font_subset import read_symbol_code_to_gid, read_unicode_cmap

        program = outlines._data
        symbol = read_symbol_code_to_gid(program)
        unicode_map = read_unicode_cmap(program)
        code_to_unicode: dict[int, int] = {}
        if hasattr(self.pdf, "_simple_code_to_unicode"):
            try:
                code_to_unicode = self.pdf._simple_code_to_unicode(font_dict) or {}
            except PdfResourceLimitException:
                raise
            except Exception:
                code_to_unicode = {}

        def resolve(
            code: int,
            _sym=symbol,
            _uni=unicode_map,
            _c2u=code_to_unicode,
        ) -> int | None:
            # Prefer the PDF /Encoding (code -> unicode) through the font's
            # Unicode cmap; then a symbol cmap; then the code as a codepoint.
            if _uni and code in _c2u:
                gid = _uni.get(_c2u[code])
                if gid:
                    return gid
            if _sym:
                gid = _sym.get(code) or _sym.get(0xF000 + code)
                if gid:
                    return gid
            if _uni:
                gid = _uni.get(code)
                if gid:
                    return gid
            return None

        return resolve

    def _simple_widths(
        self,
        font_dict: PdfDictionary,
        outlines: TrueTypeOutlines,
        code_to_gid: Callable[[int], int | None],
    ) -> Callable[[int], float]:
        first = self._cos_number(font_dict.mapping.get(PdfName("FirstChar")))
        widths_arr = self._resolve(font_dict.mapping.get(PdfName("Widths")))
        table: dict[int, float] = {}
        if first is not None and isinstance(widths_arr, PdfArray):
            base = int(first)
            for j, item in enumerate(widths_arr.items):
                wv = self._cos_number(item)
                if wv is not None:
                    table[base + j] = wv
        upm = outlines.units_per_em or 1000

        def width(code: int) -> float:
            if code in table:
                return table[code]
            gid = code_to_gid(code)
            if gid is not None:
                advance = outlines.advance_width(gid)
                if advance:
                    return advance * 1000.0 / upm
            return 500.0

        return width

    def _paint_xobject(
        self,
        name: str,
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
        depth: int,
    ) -> None:
        entry = None
        ref = None
        if resources_cos is not None:
            xobjects = self._resource_dict(resources_cos, "XObject")
            if xobjects is not None:
                ref = xobjects.mapping.get(PdfName(name))
                entry = self._resolve(ref)
        if isinstance(entry, PdfStream):
            if not self._oc_visible(entry.mapping.get(PdfName("OC"))):
                return
            subtype = self._cos_name(entry.mapping.get(PdfName("Subtype")))
            if subtype == "Image":
                self._paint_image_stream(name, entry, ref)
            elif subtype == "Form":
                self._paint_form(entry, ref, resources_cos, resources_plain, depth)
            return
        images = getattr(self.pdf, "images", {}) or {}
        if name in images:
            meta = (getattr(self.pdf, "_image_meta", {}) or {}).get(name) or {}
            if not meta:
                size = (getattr(self.pdf, "_image_sizes", {}) or {}).get(name)
                if size:
                    meta = {
                        "width": int(size[0]),
                        "height": int(size[1]),
                        "bpc": 8,
                        "cs_kind": "rgb",
                        "n_comps": 3,
                    }
            self._paint_image_pixels(meta, images[name], self.state.ctm)

    def _paint_image_stream(self, name: str, stream: PdfStream, ref: Any) -> None:
        try:
            if hasattr(self.pdf, "_decode_cos_stream"):
                data = self.pdf._decode_cos_stream(stream, ref)
            else:
                data = stream.content
        except PdfResourceLimitException:
            raise
        except Exception:
            data = stream.content
        meta = self._image_meta_from_stream(stream)
        fallback_meta = (getattr(self.pdf, "_image_meta", {}) or {}).get(name)
        if fallback_meta:
            meta = {
                **fallback_meta,
                **{k: v for k, v in meta.items() if v is not None},
            }
        smask = self._decode_image_smask(stream)
        self._paint_image_pixels(meta, data, self.state.ctm, smask)

    def _decode_image_smask(
        self, stream: PdfStream
    ) -> tuple[int, int, bytes] | None:
        """Decode an image XObject's ``/SMask`` to a ``(w, h, alpha-bytes)`` map.

        The soft mask is a DeviceGray image giving per-pixel alpha (0 fully
        transparent, 255 opaque); it is sampled over the same unit square as the
        base image. Returns ``None`` when there is no usable soft mask.
        """
        sm_ref = stream.mapping.get(PdfName("SMask"))
        sm = self._resolve(sm_ref)
        if not isinstance(sm, PdfStream):
            return None
        try:
            data = (
                self.pdf._decode_cos_stream(sm, sm_ref)
                if hasattr(self.pdf, "_decode_cos_stream")
                else sm.content
            )
        except PdfResourceLimitException:
            raise
        except Exception:
            data = sm.content
        meta = self._image_meta_from_stream(sm)
        decoded = _decode_image_to_rgb(meta, data, limits=self._load_limits)
        if decoded is None:
            return None
        w, h, rgb = decoded
        return (w, h, bytes(rgb[0::3]))  # gray -> R == G == B, take one channel

    def _build_soft_mask(
        self,
        smask: Any,
        resources_cos: PdfDictionary | None,
        resources_plain: dict,
    ) -> bytes | None:
        """Build a device-space soft mask from an ExtGState ``/SMask`` entry.

        ``/SMask /None`` (or anything without a ``/G`` group) clears the mask. A
        dictionary renders its transparency group ``/G`` offscreen at the
        current CTM and reduces it to a per-pixel alpha map (one byte per
        supersampled canvas pixel): group luminosity for ``/S /Luminosity`` (over
        the ``/BC`` backdrop, default black) or accumulated coverage for
        ``/S /Alpha``. An optional ``/TR`` transfer function is then applied.
        """
        if not isinstance(smask, PdfDictionary):
            return None  # /None or malformed -> clear the soft mask
        group = self._resolve(smask.mapping.get(PdfName("G")))
        if not isinstance(group, PdfStream):
            return None
        if self._in_soft_mask:
            return self.state.soft_mask  # ignore a mask nested in a mask group
        g_ref = smask.mapping.get(PdfName("G"))
        luminosity = self._cos_name(smask.mapping.get(PdfName("S"))) == "Luminosity"
        backdrop = self._smask_backdrop(smask) if luminosity else (0, 0, 0)
        self._in_soft_mask = True
        try:
            off = self._render_form_offscreen(
                group,
                g_ref,
                backdrop,
                track_coverage=not luminosity,
                isolated=not luminosity,
            )
        finally:
            self._in_soft_mask = False
        n = self.width * self.height
        if luminosity:
            px = off.pixels
            mask = bytearray(n)
            for i in range(n):
                o = i * 3
                # Rec.601 luma with 8-bit fixed-point weights (77+150+29 = 256).
                mask[i] = (px[o] * 77 + px[o + 1] * 150 + px[o + 2] * 29) >> 8
        elif off.coverage is not None:
            mask = off.coverage
        else:
            mask = bytearray(b"\xff" * n)
        lut = self._build_transfer_lut(smask.mapping.get(PdfName("TR")))
        if lut is not None:
            mask = bytearray(lut[v] for v in mask)
        return bytes(mask)

    def _smask_backdrop(self, smask: PdfDictionary) -> Color:
        bc = self._resolve(smask.mapping.get(PdfName("BC")))
        if isinstance(bc, PdfArray):
            comps = [self._cos_number(it) or 0.0 for it in bc.items]
            if len(comps) >= 4:
                return _cmyk(comps[0], comps[1], comps[2], comps[3])
            if len(comps) == 3:
                return _rgb(comps[0], comps[1], comps[2])
            if len(comps) == 1:
                return _gray(comps[0])
        return (0, 0, 0)  # default luminosity backdrop is black

    def _build_transfer_lut(self, tr: Any) -> list[int] | None:
        tr = self._resolve(tr)
        if tr is None or (
            isinstance(tr, PdfName) and tr.name.lstrip("/") in ("Identity", "Default")
        ):
            return None
        try:
            fn = build_function(
                self.pdf,
                tr,
                limits=self._load_limits,
                budget=self._load_budget,
            )
        except PdfResourceLimitException:
            raise
        except Exception:
            fn = None
        if fn is None:
            return None
        lut: list[int] = []
        for v in range(256):
            try:
                out = fn.eval(v / 255.0)
                val = out[0] if isinstance(out, (list, tuple)) else out
            except PdfResourceLimitException:
                raise
            except Exception:
                val = v / 255.0
            lut.append(_byte(float(val) * 255.0))
        return lut

    def _render_form_offscreen(
        self,
        group: PdfStream,
        ref: Any,
        background: Color,
        track_coverage: bool,
        seed_pixels: bytes | None = None,
        *,
        isolated: bool = False,
        knockout: bool = False,
    ) -> _Canvas:
        """Render a form XObject into a fresh canvas at the current CTM.

        Used for soft-mask groups and unit-composited transparency groups. The
        offscreen render gets a fresh graphics state (no soft mask, empty save
        stack) so it is not modulated by the mask being built. When
        *seed_pixels* is given the canvas starts from that backdrop copy (for
        group compositing) instead of the solid *background*.
        """
        pixel_count = self.width * self.height
        bytes_per_pixel = 4
        if seed_pixels is not None:
            bytes_per_pixel += 3
        if isolated:
            bytes_per_pixel += 1
        if track_coverage:
            bytes_per_pixel += 1
        if knockout:
            bytes_per_pixel += 4 if isolated else 3
        work_bytes = pixel_count * bytes_per_pixel
        self._load_budget.check(
            self._offscreen_work_bytes + work_bytes,
            "max_codec_work_bytes",
            "transparency group offscreen working set",
        )
        self._offscreen_work_bytes += work_bytes
        try:
            off = _Canvas(self.width, self.height, background)
            if seed_pixels is not None:
                off.pixels = bytearray(seed_pixels)
            if isolated:
                off.alpha = bytearray(pixel_count)
            if track_coverage:
                off.coverage = bytearray(pixel_count)
            if knockout:
                off.knockout = True
                off.initial_pixels = bytes(off.pixels)
                off.initial_alpha = (
                    bytes(off.alpha) if off.alpha is not None else None
                )
            saved_canvas = self.canvas
            saved_state = self.state
            saved_stack = self.state_stack
            self.canvas = off
            self.state = _GraphicsState(ctm=saved_state.ctm)
            self.state_stack = []
            try:
                self._paint_form(
                    group,
                    ref,
                    self.resources_cos,
                    self.resources_plain,
                    depth=0,
                    as_group_content=True,
                )
            finally:
                self.canvas = saved_canvas
                self.state = saved_state
                self.state_stack = saved_stack
            return off
        finally:
            self._offscreen_work_bytes -= work_bytes

    def _paint_form(
        self,
        stream: PdfStream,
        ref: Any,
        parent_resources_cos: PdfDictionary | None,
        parent_resources_plain: dict,
        depth: int,
        *,
        as_group_content: bool = False,
        apply_matrix: bool = True,
    ) -> None:
        matrix = (
            _cos_matrix(self._resolve(stream.mapping.get(PdfName("Matrix"))))
            or IDENTITY
        )
        # An annotation composes /Matrix into its placement itself (12.5.5), so
        # it hands over a CTM that already carries it.
        if not apply_matrix:
            matrix = IDENTITY
        # Transparency groups are always rendered as a unit so their isolated
        # and knockout attributes affect internal blend and backdrop semantics.
        if (
            not as_group_content
            and not self._in_soft_mask
            and self._is_transparency_group(stream)
        ):
            region = self._form_device_bbox(stream, matrix)
            if region is not None:
                self._paint_group_composited(stream, ref, region)
                return
        form_resources = self._resolve(stream.mapping.get(PdfName("Resources")))
        if not isinstance(form_resources, PdfDictionary):
            form_resources = parent_resources_cos
        form_resources_plain = parent_resources_plain
        if isinstance(form_resources, PdfDictionary) and hasattr(
            self.pdf, "_convert_cos_to_dict"
        ):
            form_resources_plain = self.pdf._convert_cos_to_dict(form_resources)
        try:
            content = self.pdf._decode_cos_stream(stream, ref) if hasattr(
                self.pdf, "_decode_cos_stream"
            ) else stream.content
        except PdfResourceLimitException:
            raise
        except Exception:
            content = stream.content
        saved = copy.deepcopy(self.state)
        self.state.ctm = _multiply(matrix, self.state.ctm)
        self._interpret(
            content, form_resources, form_resources_plain, depth=depth + 1
        )
        self.state = saved

    def _is_transparency_group(self, stream: PdfStream) -> bool:
        group = self._resolve(stream.mapping.get(PdfName("Group")))
        return (
            isinstance(group, PdfDictionary)
            and self._cos_name(group.mapping.get(PdfName("S"))) == "Transparency"
        )

    def _form_device_bbox(
        self, stream: PdfStream, matrix: Matrix
    ) -> tuple[int, int, int, int] | None:
        bbox = self._resolve(stream.mapping.get(PdfName("BBox")))
        if not isinstance(bbox, PdfArray) or len(bbox.items) < 4:
            return None
        v = [self._cos_number(it) or 0.0 for it in bbox.items[:4]]
        full = _multiply(matrix, self.state.ctm)
        corners = [(v[0], v[1]), (v[2], v[1]), (v[2], v[3]), (v[0], v[3])]
        dev = [
            self._user_to_pixel(*_transform_point(full, ux, uy)) for ux, uy in corners
        ]
        min_x = max(0, math.floor(min(p[0] for p in dev)))
        max_x = min(self.width - 1, math.ceil(max(p[0] for p in dev)))
        min_y = max(0, math.floor(min(p[1] for p in dev)))
        max_y = min(self.height - 1, math.ceil(max(p[1] for p in dev)))
        if min_x > max_x or min_y > max_y:
            return None
        return (min_x, min_y, max_x, max_y)

    def _paint_group_composited(
        self, stream: PdfStream, ref: Any, region: tuple[int, int, int, int]
    ) -> None:
        """Render a transparency group offscreen and composite it as a unit.

        Non-isolated groups begin with the current backdrop, isolated groups
        begin transparent, and knockout elements use the group's initial
        backdrop instead of previously painted elements.
        """
        group_alpha = self.state.fill_alpha
        blend = self.state.blend_mode
        sm = self.state.soft_mask
        group = self._resolve(stream.mapping.get(PdfName("Group")))
        isolated = False
        knockout = False
        if isinstance(group, PdfDictionary):
            isolated = self._cos_bool(group.mapping.get(PdfName("I")), False)
            knockout = self._cos_bool(group.mapping.get(PdfName("K")), False)
        backdrop = self.canvas.pixels
        off = self._render_form_offscreen(
            stream,
            ref,
            (0, 0, 0) if isolated else self.background,
            track_coverage=True,
            seed_pixels=None if isolated else backdrop,
            isolated=isolated,
            knockout=knockout,
        )
        cov = off.coverage or b""
        px = off.pixels
        main = self.canvas
        x0, y0, x1, y1 = region
        w = self.width
        inv255 = 1.0 / 255.0
        for y in range(y0, y1 + 1):
            row = y * w
            for x in range(x0, x1 + 1):
                idx = row + x
                c = cov[idx]
                if not c:
                    continue
                alpha = group_alpha * (c * inv255)
                if sm is not None:
                    alpha *= sm[idx] * inv255
                if alpha <= 0.0:
                    continue
                o = idx * 3
                if isolated:
                    source = (px[o], px[o + 1], px[o + 2])
                else:
                    inverse = 1.0 - c * inv255
                    source = tuple(
                        _byte(
                            (px[o + channel] - inverse * backdrop[o + channel])
                            / (c * inv255)
                        )
                        for channel in range(3)
                    )
                main.set_pixel(x, y, source, alpha, blend)

    def _paint_image_pixels(
        self,
        meta: dict,
        data: bytes,
        matrix: Matrix,
        smask: tuple[int, int, bytes] | None = None,
    ) -> None:
        image = _decode_image_to_rgb(meta, data, limits=self._load_limits)
        if image is None:
            return
        width, height, pixels = image
        color_kind = str(meta.get("cs_kind") or "other")
        inv = _invert_matrix(matrix)
        if inv is None:
            return
        sw = sh = 0
        salpha: bytes = b""
        if smask is not None:
            sw, sh, salpha = smask
        corners = [
            _transform_point(matrix, 0.0, 0.0),
            _transform_point(matrix, 1.0, 0.0),
            _transform_point(matrix, 1.0, 1.0),
            _transform_point(matrix, 0.0, 1.0),
        ]
        dev = [self._user_to_pixel(x, y) for x, y in corners]
        min_x = max(0, math.floor(min(p[0] for p in dev)))
        max_x = min(self.width - 1, math.ceil(max(p[0] for p in dev)))
        min_y = max(0, math.floor(min(p[1] for p in dev)))
        max_y = min(self.height - 1, math.ceil(max(p[1] for p in dev)))
        if min_x > max_x or min_y > max_y:
            return
        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                ux, uy = self._pixel_to_user(px, py)
                ix_f, iy_f = _transform_point(inv, ux, uy)
                if not (0.0 <= ix_f <= 1.0 and 0.0 <= iy_f <= 1.0):
                    continue
                sx = min(width - 1, max(0, int(ix_f * width)))
                sy = min(height - 1, max(0, int((1.0 - iy_f) * height)))
                off = (sy * width + sx) * 3
                color = (pixels[off], pixels[off + 1], pixels[off + 2])
                alpha = self.state.fill_alpha
                if sw:
                    ax = min(sw - 1, max(0, int(ix_f * sw)))
                    ay = min(sh - 1, max(0, int((1.0 - iy_f) * sh)))
                    alpha *= salpha[ay * sw + ax] * (1.0 / 255.0)
                self._composite_pixel(
                    px,
                    py,
                    color,
                    alpha,
                    color_kind=color_kind,
                )

    def _image_meta_from_stream(self, stream: PdfStream) -> dict:
        meta: dict = {}
        m = stream.mapping
        width = self._cos_number(m.get(PdfName("Width")))
        height = self._cos_number(m.get(PdfName("Height")))
        meta["width"] = int(width or 0)
        meta["height"] = int(height or 0)
        bpc = self._cos_number(m.get(PdfName("BitsPerComponent")))
        meta["bpc"] = int(bpc or 8)
        image_mask = self._resolve(m.get(PdfName("ImageMask")))
        if isinstance(image_mask, PdfBoolean) and image_mask.value:
            meta["bpc"] = 1
            meta["cs_kind"] = "gray"
            meta["n_comps"] = 1
        else:
            kind, comps, palette, base_comps = self._colorspace_meta(
                self._resolve(m.get(PdfName("ColorSpace")))
            )
            meta["cs_kind"] = kind
            if comps is not None:
                meta["n_comps"] = comps
            if palette is not None:
                meta["palette"] = palette
                meta["palette_base_comps"] = base_comps or 3
        meta["filter"] = self._terminal_filter(m.get(PdfName("Filter")))
        decode = self._resolve(m.get(PdfName("Decode")))
        if isinstance(decode, PdfArray):
            vals = []
            for item in decode.items:
                num = self._cos_number(item)
                vals.append(float(num or 0.0))
            meta["decode"] = vals
        return meta

    def _colorspace_meta(
        self, cs: Any
    ) -> tuple[str, int | None, bytes | None, int | None]:
        cs = self._resolve(cs)
        if isinstance(cs, PdfName):
            name = cs.name.lstrip("/")
            if name in ("DeviceGray", "G", "CalGray"):
                return ("gray", 1, None, None)
            if name in ("DeviceRGB", "RGB", "CalRGB"):
                return ("rgb", 3, None, None)
            if name in ("DeviceCMYK", "CMYK"):
                return ("cmyk", 4, None, None)
        if isinstance(cs, PdfArray) and cs.items:
            head = self._cos_name(cs.items[0])
            if head in ("Indexed", "I") and len(cs.items) >= 4:
                _, base_comps, _, _ = self._colorspace_meta(cs.items[1])
                lookup = self._resolve(cs.items[3])
                palette = None
                if isinstance(lookup, PdfString):
                    palette = bytes(lookup.value)
                elif isinstance(lookup, PdfStream):
                    try:
                        palette = self.pdf._decode_cos_stream(lookup, cs.items[3])
                    except PdfResourceLimitException:
                        raise
                    except Exception:
                        palette = lookup.content
                return ("indexed", 1, palette, base_comps or 3)
            if head == "ICCBased" and len(cs.items) >= 2:
                stream = self._resolve(cs.items[1])
                if isinstance(stream, PdfStream):
                    n = self._cos_number(stream.mapping.get(PdfName("N")))
                    n_int = int(n or 0)
                    kind = {1: "gray", 3: "rgb", 4: "cmyk"}.get(
                        n_int, "unknown"
                    )
                    return (kind, n_int or None, None, None)
        return ("rgb", 3, None, None)

    def _terminal_filter(self, filt: Any) -> str | None:
        filt = self._resolve(filt)
        if isinstance(filt, PdfName):
            return filt.name.lstrip("/")
        if isinstance(filt, PdfArray) and filt.items:
            return self._cos_name(filt.items[-1])
        return None

    def _resource_dict(
        self, resources: PdfDictionary, name: str
    ) -> PdfDictionary | None:
        obj = self._resolve(resources.mapping.get(PdfName(name)))
        return obj if isinstance(obj, PdfDictionary) else None

    def _resolve(self, obj: Any) -> Any:
        if hasattr(self.pdf, "_resolve"):
            return self.pdf._resolve(obj)
        if isinstance(obj, PdfIndirectReference) and getattr(
            self.pdf, "_cos_doc", None
        ):
            return self.pdf._cos_doc.objects.get(obj.object_number)
        return obj

    def _cos_number(self, obj: Any) -> float | None:
        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return float(obj.value)
        if isinstance(obj, (int, float)):
            return float(obj)
        return None

    def _cos_name(self, obj: Any) -> str | None:
        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        if isinstance(obj, str):
            return obj.lstrip("/")
        return None

    def _cos_bool(self, obj: Any, default: bool = False) -> bool:
        obj = self._resolve(obj)
        if isinstance(obj, PdfBoolean):
            return bool(obj.value)
        if isinstance(obj, bool):
            return obj
        return default

    def _transform(self, x: float, y: float) -> Point:
        return _transform_point(self.state.ctm, x, y)

    def _user_to_pixel(self, x: float, y: float) -> Point:
        dx, dy = self._user_to_display(x, y)
        return (
            dx * self.point_scale,
            (self.page_height_pts - dy) * self.point_scale,
        )

    def _device_scale(self, matrix: Matrix) -> float:
        origin = self._user_to_pixel(*_transform_point(matrix, 0.0, 0.0))
        unit_x = self._user_to_pixel(*_transform_point(matrix, 1.0, 0.0))
        unit_y = self._user_to_pixel(*_transform_point(matrix, 0.0, 1.0))
        return max(
            math.hypot(unit_x[0] - origin[0], unit_x[1] - origin[1]),
            math.hypot(unit_y[0] - origin[0], unit_y[1] - origin[1]),
        )

    def _user_to_display(self, x: float, y: float) -> Point:
        x0, y0, _, _ = self.crop_box
        lx = x - x0
        ly = y - y0
        if self.rotation == 90:
            return (ly, self.crop_width - lx)
        if self.rotation == 180:
            return (self.crop_width - lx, self.crop_height - ly)
        if self.rotation == 270:
            return (self.crop_height - ly, lx)
        return (lx, ly)

    def _pixel_to_user(self, x: int, y: int) -> Point:
        dx = (x + 0.5) / self.point_scale
        dy = self.page_height_pts - ((y + 0.5) / self.point_scale)
        if self.rotation == 90:
            lx = self.crop_width - dy
            ly = dx
        elif self.rotation == 180:
            lx = self.crop_width - dx
            ly = self.crop_height - dy
        elif self.rotation == 270:
            lx = dy
            ly = self.crop_height - dx
        else:
            lx = dx
            ly = dy
        return (self.crop_box[0] + lx, self.crop_box[1] + ly)


def _decode_image_to_rgb(
    meta: dict,
    data: bytes,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, int, bytes] | None:
    resolved_limits = _coerce_limits(limits)
    budget = _LoadBudget(resolved_limits)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    budget.check_image_pixels(width, height, "rasterized image")
    budget.check(
        width * height * 3,
        "max_decoded_stream_bytes",
        "rasterized image RGB bytes",
    )
    budget.check(
        len(data) + (width * height * 7),
        "max_codec_work_bytes",
        "rasterized image conversion working set",
    )
    filt = str(meta.get("filter") or "").lstrip("/")
    sniff = ext_from_magic(data)
    if filt in ("DCTDecode", "DCT") or sniff == "jpg":
        from .dct import decode as decode_jpeg

        decoded = decode_jpeg(data, limits=resolved_limits)
        if decoded is None:
            return None
        pixels = decoded.samples
        if decoded.mode == "L":
            pixels = gray_to_rgb(pixels)
        elif decoded.mode == "CMYK":
            pixels = cmyk_to_rgb(pixels)
        return (decoded.width, decoded.height, pixels)
    if sniff == "jp2":
        # A JPEG 2000 codestream still in its compressed form: the stream
        # decoder hands undecodable filters back as their raw bytes, and those
        # bytes painted as samples are a page of noise. Decode here, or draw
        # nothing. (When the filter *did* run, ``data`` is already samples and
        # no longer sniffs as JPEG 2000, so it takes the plain path below.)
        from .jpx import decode_to_rgb

        return decode_to_rgb(data, meta, limits=resolved_limits)
    bpc = int(meta.get("bpc") or 8)
    kind = meta.get("cs_kind") or "rgb"
    comps = int(meta.get("n_comps") or (1 if kind == "gray" else 3))
    if kind == "indexed" and meta.get("palette") is not None:
        rgb = indexed_to_rgb(
            data,
            meta["palette"],
            bpc,
            width,
            height,
            int(meta.get("palette_base_comps") or 3),
        )
        return (width, height, rgb)
    if kind == "gray" or comps == 1:
        gray = to_8bpc_bytes(data, bpc, width, height, 1)
        return (width, height, gray_to_rgb(gray))
    if kind == "cmyk" or comps == 4:
        cmyk = to_8bpc_bytes(data, bpc, width, height, 4)
        return (width, height, cmyk_to_rgb(cmyk))
    rgb = to_8bpc_bytes(data, bpc, width, height, 3)
    return (width, height, rgb)


def _coerce_rgb(value: Sequence[int]) -> Color:
    if len(value) < 3:
        raise PdfValidationException("background must contain three RGB components")
    return (_byte(value[0]), _byte(value[1]), _byte(value[2]))


def _byte(value: float | int) -> int:
    return int(max(0, min(255, round(float(value)))))


def _normalize_blend_mode(name: str) -> str | None:
    return _BLEND_MODES.get(name.lstrip("/").lower())


def _blend_color(source: Color, backdrop: Color, mode: str) -> Color:
    if mode == "Normal":
        return source
    if mode in ("Hue", "Saturation", "Color", "Luminosity"):
        source_f = tuple(component / 255.0 for component in source)
        backdrop_f = tuple(component / 255.0 for component in backdrop)
        if mode == "Hue":
            result = _set_lum(
                _set_sat(source_f, _saturation(backdrop_f)), _luminosity(backdrop_f)
            )
        elif mode == "Saturation":
            result = _set_lum(
                _set_sat(backdrop_f, _saturation(source_f)), _luminosity(backdrop_f)
            )
        elif mode == "Color":
            result = _set_lum(source_f, _luminosity(backdrop_f))
        else:
            result = _set_lum(backdrop_f, _luminosity(source_f))
        return tuple(_byte(component * 255.0) for component in result)
    return (
        _blend_channel(source[0], backdrop[0], mode),
        _blend_channel(source[1], backdrop[1], mode),
        _blend_channel(source[2], backdrop[2], mode),
    )


def _blend_channel(source: int, backdrop: int, mode: str) -> int:
    cs = source / 255.0
    cb = backdrop / 255.0
    if mode == "Multiply":
        out = cb * cs
    elif mode == "Screen":
        out = cb + cs - cb * cs
    elif mode == "Overlay":
        out = 2.0 * cb * cs if cb <= 0.5 else 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs)
    elif mode == "Darken":
        out = min(cb, cs)
    elif mode == "Lighten":
        out = max(cb, cs)
    elif mode == "ColorDodge":
        out = 1.0 if cs >= 1.0 else min(1.0, cb / (1.0 - cs))
    elif mode == "ColorBurn":
        out = 0.0 if cs <= 0.0 else 1.0 - min(1.0, (1.0 - cb) / cs)
    elif mode == "HardLight":
        out = 2.0 * cb * cs if cs <= 0.5 else 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs)
    elif mode == "SoftLight":
        if cs <= 0.5:
            out = cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb)
        else:
            if cb <= 0.25:
                d = ((16.0 * cb - 12.0) * cb + 4.0) * cb
            else:
                d = math.sqrt(cb)
            out = cb + (2.0 * cs - 1.0) * (d - cb)
    elif mode == "Difference":
        out = abs(cb - cs)
    elif mode == "Exclusion":
        out = cb + cs - 2.0 * cb * cs
    else:
        out = cs
    return _byte(out * 255.0)


def _luminosity(color: Sequence[float]) -> float:
    return 0.3 * color[0] + 0.59 * color[1] + 0.11 * color[2]


def _saturation(color: Sequence[float]) -> float:
    return max(color) - min(color)


def _clip_color(color: Sequence[float]) -> tuple[float, float, float]:
    result = [float(component) for component in color]
    lum = _luminosity(result)
    minimum = min(result)
    maximum = max(result)
    if minimum < 0.0 and lum != minimum:
        result = [lum + (component - lum) * lum / (lum - minimum) for component in result]
    maximum = max(result)
    if maximum > 1.0 and maximum != lum:
        result = [
            lum + (component - lum) * (1.0 - lum) / (maximum - lum)
            for component in result
        ]
    return result[0], result[1], result[2]


def _set_lum(
    color: Sequence[float], luminosity: float
) -> tuple[float, float, float]:
    delta = luminosity - _luminosity(color)
    return _clip_color(tuple(component + delta for component in color))


def _set_sat(
    color: Sequence[float], saturation: float
) -> tuple[float, float, float]:
    order = sorted(range(3), key=lambda index: color[index])
    low, middle, high = order
    result = [float(component) for component in color]
    if color[high] > color[low]:
        result[middle] = (
            (color[middle] - color[low]) * saturation / (color[high] - color[low])
        )
        result[high] = saturation
    else:
        result[middle] = 0.0
        result[high] = 0.0
    result[low] = 0.0
    return result[0], result[1], result[2]


def _rgb(r: float, g: float, b: float) -> Color:
    return (_byte(r * 255.0), _byte(g * 255.0), _byte(b * 255.0))


def _gray(g: float) -> Color:
    v = _byte(g * 255.0)
    return (v, v, v)


def _cmyk(c: float, m: float, y: float, k: float) -> Color:
    return (
        _byte((1.0 - min(1.0, c)) * (1.0 - min(1.0, k)) * 255.0),
        _byte((1.0 - min(1.0, m)) * (1.0 - min(1.0, k)) * 255.0),
        _byte((1.0 - min(1.0, y)) * (1.0 - min(1.0, k)) * 255.0),
    )


def _rgb_to_cmyk(color: Color) -> tuple[float, float, float, float]:
    """Recover approximate device colorants from an RGB pixel.

    The renderer composites in RGB, so overprint reconstructs colorants from the
    painted RGB using the exact inverse of the naive :func:`_cmyk` conversion
    (maximum grey-component removal). Round-tripping a ``_cmyk`` result through
    this function reproduces the original colorants without drift, which keeps
    the composite overprint preview idempotent.
    """
    r = color[0] / 255.0
    g = color[1] / 255.0
    b = color[2] / 255.0
    k = 1.0 - max(r, g, b)
    if k >= 1.0:
        return 0.0, 0.0, 0.0, 1.0
    scale = 1.0 - k
    return (1.0 - r - k) / scale, (1.0 - g - k) / scale, (1.0 - b - k) / scale, k


def _is_operator(token: Any) -> bool:
    return (
        isinstance(token, str)
        and not token.startswith("/")
        and token not in ("<<", ">>")
    )


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _last_numbers(operands: Sequence[Any], count: int) -> list[float] | None:
    if len(operands) < count:
        return None
    vals = [_number(v) for v in operands[-count:]]
    if any(v is None for v in vals):
        return None
    return [float(v) for v in vals if v is not None]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def _transform_point(m: Matrix, x: float, y: float) -> Point:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _invert_matrix(m: Matrix) -> Matrix | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    inv_a = d / det
    inv_b = -b / det
    inv_c = -c / det
    inv_d = a / det
    inv_e = (c * f - d * e) / det
    inv_f = (b * e - a * f) / det
    return (inv_a, inv_b, inv_c, inv_d, inv_e, inv_f)


def _bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    return (
        u * u * u * p0[0]
        + 3 * u * u * t * p1[0]
        + 3 * u * t * t * p2[0]
        + t * t * t * p3[0],
        u * u * u * p0[1]
        + 3 * u * u * t * p1[1]
        + 3 * u * t * t * p2[1]
        + t * t * t * p3[1],
    )


def _cos_matrix(obj: Any) -> Matrix | None:
    if isinstance(obj, PdfArray) and len(obj.items) >= 6:
        vals = []
        for item in obj.items[:6]:
            if isinstance(item, PdfNumber):
                vals.append(float(item.value))
            elif isinstance(item, (int, float)):
                vals.append(float(item))
            else:
                return None
        return tuple(vals)  # type: ignore[return-value]
    return None
