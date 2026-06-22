"""Dependency-free PDF page rasterization helpers.

The renderer is intentionally small and conservative. It handles the common
graphics/image/text operators used by simple generated PDFs and leaves the
heavier PDF imaging features (transparency groups, overprint, mesh shadings)
for later engine layers.
"""

from __future__ import annotations

import copy
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

from .cff_outlines import CffOutlines
from .content_stream_parser import ContentStreamParser
from .glyph_outlines import TrueTypeOutlines
from .shading import Shading, build_function, build_shading
from .std_font_data import load_substitute_sfnt, resolve_substitute_key
from .type1_outlines import Type1Outlines
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
from .image_export import (
    cmyk_to_rgb,
    ext_from_magic,
    gray_to_rgb,
    indexed_to_rgb,
    to_8bpc_bytes,
    write_png,
)

Matrix = Tuple[float, float, float, float, float, float]
Point = Tuple[float, float]
Color = Tuple[int, int, int]

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

    def to_png(self) -> bytes:
        """Encode this raster as a PNG file."""
        return write_png(self.width, self.height, "RGB", self.pixels)

    def to_tiff(self) -> bytes:
        """Encode this raster as an uncompressed baseline RGB TIFF file."""
        return _write_tiff_rgb(self.width, self.height, self.pixels, self.dpi)

    def save(self, path: str | Path) -> Path:
        """Save the raster to ``.png`` or ``.tif/.tiff`` and return the path."""
        out = Path(path)
        suffix = out.suffix.lower()
        if suffix in ("", ".png"):
            if suffix == "":
                out = out.with_suffix(".png")
            data = self.to_png()
        elif suffix in (".tif", ".tiff"):
            data = self.to_tiff()
        else:
            raise PdfValidationException(
                "Unsupported raster output format; use .png, .tif, or .tiff"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out


@dataclass
class _TextState:
    in_text: bool = False
    font_name: Optional[str] = None
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
    line_width: float = 1.0
    stroke_alpha: float = 1.0
    fill_alpha: float = 1.0
    blend_mode: str = "Normal"
    # Soft mask from the ExtGState /SMask: a device-space, supersampled
    # per-pixel alpha map (one byte 0-255 per canvas pixel) that further
    # modulates every paint, or None. Stored as immutable ``bytes`` so the
    # per-``q`` ``deepcopy`` of the state is O(1).
    soft_mask: Optional[bytes] = None
    # When set, paths are filled with a shading pattern: (shading, pattern matrix).
    fill_shading: Optional[Tuple["Shading", Matrix]] = None
    # When set, paths are filled with a tiling pattern:
    # (pattern stream, pattern matrix, paint type, uncoloured paint colour).
    fill_tiling: Optional[Tuple[Any, Matrix, int, Color]] = None
    text: _TextState = field(default_factory=_TextState)


@dataclass
class _Path:
    subpaths: List[List[Point]] = field(default_factory=list)
    current: Optional[List[Point]] = None

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

    def clone_subpaths(self) -> List[List[Point]]:
        return [[tuple(p) for p in subpath] for subpath in self.subpaths]


class _Canvas:
    def __init__(self, width: int, height: int, background: Color):
        self.width = width
        self.height = height
        self.pixels = bytearray(background * (width * height))
        self.clip = bytearray(b"\x01" * (width * height))
        # Optional accumulated-alpha channel (0-255), allocated only for the
        # offscreen canvases used to build Alpha soft masks and to composite
        # transparency groups as a unit. None on the main page canvas.
        self.coverage: Optional[bytearray] = None

    def set_pixel(
        self,
        x: int,
        y: int,
        color: Color,
        alpha: float = 1.0,
        blend_mode: str = "Normal",
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
            cov = self.coverage[idx]
            self.coverage[idx] = _byte(alpha * 255.0 + cov * (1.0 - alpha))
        backdrop = (
            self.pixels[off],
            self.pixels[off + 1],
            self.pixels[off + 2],
        )
        blended = _blend_color(color, backdrop, blend_mode)
        if alpha >= 1.0:
            self.pixels[off] = blended[0]
            self.pixels[off + 1] = blended[1]
            self.pixels[off + 2] = blended[2]
            return
        inv = max(0.0, 1.0 - alpha)
        self.pixels[off] = _byte(blended[0] * alpha + backdrop[0] * inv)
        self.pixels[off + 1] = _byte(blended[1] * alpha + backdrop[1] * inv)
        self.pixels[off + 2] = _byte(blended[2] * alpha + backdrop[2] * inv)


@dataclass
class _GlyphFont:
    """A resolved embedded TrueType font ready for glyph rasterization.

    ``code_to_gid`` maps a character code (simple font) or CID (composite) to a
    glyph id; ``width_1000`` returns the advance in text-space/1000 units keyed
    the same way; ``bytes_per_code`` is 1 for simple fonts and 2 for Identity
    composite fonts.
    """

    outlines: Any  # TrueTypeOutlines | CffOutlines (duck-typed outline source)
    code_to_gid: Callable[[int], Optional[int]]
    width_1000: Callable[[int], float]
    bytes_per_code: int

    def iter_glyphs(self, raw: bytes):
        """Yield ``(gid_or_None, width_1000, applies_word_spacing)`` per code."""
        if self.bytes_per_code == 2:
            for i in range(0, len(raw) - 1, 2):
                code = (raw[i] << 8) | raw[i + 1]
                yield self.code_to_gid(code), self.width_1000(code), False
        else:
            for byte in raw:
                yield self.code_to_gid(byte), self.width_1000(byte), byte == 32


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


def render_page(
    pdf: Any,
    page_index: int,
    *,
    dpi: float = 72.0,
    scale: float = 1.0,
    background: Sequence[int] = (255, 255, 255),
    antialias: "bool | int" = True,
) -> RasterizedPage:
    """Render ``page_index`` from a ``SimplePdf`` into an RGB raster.

    ``antialias`` smooths edges by supersampling: ``True`` (the default) renders
    at 3x and box-downsamples, an integer 1-8 sets the factor explicitly, and
    ``False`` (or ``1``) disables it for an exact, hard-edged raster.
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
        antialias: "bool | int" = True,
    ):
        if dpi <= 0 or scale <= 0:
            raise PdfValidationException("dpi and scale must be positive")
        self.pdf = pdf
        self.page_index = page_index
        self.dpi = float(dpi)
        self._ss = _normalize_antialias(antialias)
        base_scale = (float(dpi) / 72.0) * float(scale)
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
        self.target_width = max(1, int(math.ceil(page_w * base_scale)))
        self.target_height = max(1, int(math.ceil(page_h * base_scale)))
        self.width = self.target_width * self._ss
        self.height = self.target_height * self._ss
        self.page_width_pts = page_w
        self.page_height_pts = page_h
        self.background = _coerce_rgb(background)
        self.canvas = _Canvas(self.width, self.height, self.background)
        self.state = _GraphicsState()
        self.state_stack: List[_GraphicsState] = []
        self.path = _Path()
        self.pending_clip: Optional[List[List[Point]]] = None
        self.resources_cos = self._page_resources_cos()
        self.resources_plain = self._page_resources_plain()
        self._font_cache: dict[str, Optional[_GlyphFont]] = {}
        self._pattern_depth = 0
        # Guards against a soft-mask group that itself sets a soft mask.
        self._in_soft_mask = False

    def render(self) -> RasterizedPage:
        content = self._page_content()
        if content:
            self._interpret(content, self.resources_cos, self.resources_plain, depth=0)
        pixels = (
            self._downsample() if self._ss > 1 else bytes(self.canvas.pixels)
        )
        return RasterizedPage(
            width=self.target_width,
            height=self.target_height,
            pixels=pixels,
            dpi=self.dpi,
        )

    def _downsample(self) -> bytes:
        """Box-average each ``ss x ss`` block of the supersampled canvas."""
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

    def _normalize_box(self, box: Any) -> Tuple[float, float, float, float]:
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            return (0.0, 0.0, 612.0, 792.0)
        x0, y0, x1, y1 = (float(v) for v in box[:4])
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

    def _page_resources_cos(self) -> Optional[PdfDictionary]:
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
        resources_cos: Optional[PdfDictionary],
        resources_plain: dict,
        *,
        depth: int,
    ) -> None:
        if depth > 8:
            return
        try:
            tokens = list(ContentStreamParser(content, resources_plain)._tokenize())
        except Exception:
            return

        operands: List[Any] = []
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

    def _handle_operator(
        self,
        op: str,
        operands: List[Any],
        resources_cos: Optional[PdfDictionary],
        resources_plain: dict,
        depth: int,
    ) -> None:
        if op == "q":
            self.state_stack.append(copy.deepcopy(self.state))
            return
        if op == "Q":
            if self.state_stack:
                self.state = self.state_stack.pop()
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
        if op in ("cs", "CS"):
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

    def _set_color(self, op: str, operands: List[Any]) -> None:
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
        else:
            self.state.fill_color = color
            self.state.fill_shading = None
            self.state.fill_tiling = None

    def _set_current_space_color(
        self, op: str, operands: List[Any], resources_cos: Optional[PdfDictionary]
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
        if len(nums) >= 4:
            color = _cmyk(nums[-4], nums[-3], nums[-2], nums[-1])
        elif len(nums) >= 3:
            color = _rgb(nums[-3], nums[-2], nums[-1])
        elif nums:
            color = _gray(nums[-1])
        else:
            return
        if is_fill:
            self.state.fill_color = color
            self.state.fill_shading = None
            self.state.fill_tiling = None
        else:
            self.state.stroke_color = color

    def _set_fill_pattern(
        self,
        name: str,
        resources_cos: Optional[PdfDictionary],
        color_nums: List[Optional[float]],
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
            shading = build_shading(self.pdf, pattern.mapping.get(PdfName("Shading")))
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
                if len(nums) >= 4:
                    paint_color = _cmyk(nums[-4], nums[-3], nums[-2], nums[-1])
                elif len(nums) >= 3:
                    paint_color = _rgb(nums[-3], nums[-2], nums[-1])
                else:
                    paint_color = _gray(nums[-1])
            self.state.fill_tiling = (pattern, matrix, paint_type, paint_color)
            return
        self.state.fill_color = (128, 128, 128)

    def _apply_extgstate(
        self,
        operand: Any,
        resources_cos: Optional[PdfDictionary],
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

    def _blend_mode(self, obj: Any) -> Optional[str]:
        names = self._blend_mode_names(obj)
        if not names:
            return None
        for name in names:
            mode = _normalize_blend_mode(name)
            if mode is not None:
                return mode
        return "Normal"

    def _blend_mode_names(self, obj: Any) -> List[str]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfArray):
            names: List[str] = []
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

    def _append_path(self, op: str, operands: List[Any]) -> None:
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

    def _append_curve(self, op: str, operands: List[Any]) -> None:
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
        self, subpaths: Iterable[List[Point]], color: Color, alpha: float
    ) -> None:
        for subpath in subpaths:
            polygon = [self._user_to_pixel(x, y) for x, y in subpath]
            self._fill_polygon_pixels(polygon, color, alpha)

    def _fill_tiling(
        self,
        subpaths: List[List[Point]],
        fill_tiling: Tuple[Any, Matrix, int, Color],
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
        polys: List[List[Point]],
        inv: Matrix,
        bbox: Tuple[float, float, float, float],
        xstep: float,
        ystep: float,
    ) -> Optional[Tuple[int, int, int, int]]:
        """Return the inclusive ``(i_lo, i_hi, j_lo, j_hi)`` tile range to draw."""
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        dev = (
            (min(xs), min(ys)),
            (max(xs), min(ys)),
            (max(xs), max(ys)),
            (min(xs), max(ys)),
        )
        pat_xs: List[float] = []
        pat_ys: List[float] = []
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
        i_lo, i_hi = int(math.floor(min(i0, i1))), int(math.ceil(max(i0, i1)))
        j_lo, j_hi = int(math.floor(min(j0, j1))), int(math.ceil(max(j0, j1)))
        if (i_hi - i_lo + 1) * (j_hi - j_lo + 1) > 4096:
            return None  # too many tiles; skip rather than stall
        return i_lo, i_hi, j_lo, j_hi

    def _cos_rect(
        self, obj: Any
    ) -> Optional[Tuple[float, float, float, float]]:
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
        subpaths: Iterable[List[Point]],
        fill_shading: Tuple["Shading", Matrix],
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
        polygon: List[Point],
        shading: "Shading",
        to_shading: Matrix,
        alpha: float,
    ) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, int(math.floor(min(ys))))
        max_y = min(self.height - 1, int(math.ceil(max(ys))))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: List[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, int(math.floor(nodes[i])))
                x_end = min(self.width - 1, int(math.ceil(nodes[i + 1])))
                self._shade_span(y, x_start, x_end, shading, to_shading, alpha)

    def _paint_sh(self, name: str, resources_cos: Optional[PdfDictionary]) -> None:
        """Paint a shading (the ``sh`` operator) over the current clip region."""
        if resources_cos is None:
            return
        shadings = self._resource_dict(resources_cos, "Shading")
        if shadings is None:
            return
        shading = build_shading(self.pdf, shadings.mapping.get(PdfName(name)))
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
            start: Optional[int] = None
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
        shading: "Shading",
        to_shading: Matrix,
        alpha: float,
    ) -> None:
        for x in range(x_start, x_end + 1):
            ux, uy = self._pixel_to_user(x, y)
            sx, sy = _transform_point(to_shading, ux, uy)
            color = shading.color_at(sx, sy)
            if color is not None:
                self._composite_pixel(x, y, color, alpha)

    def _composite_pixel(
        self, x: int, y: int, color: Color, alpha: float
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
        self.canvas.set_pixel(x, y, color, alpha, blend_mode=self.state.blend_mode)

    def _stroke_subpaths(
        self, subpaths: Iterable[List[Point]], color: Color, alpha: float
    ) -> None:
        px_width = max(1.0, self.state.line_width * self.point_scale)
        radius = max(0.5, px_width / 2.0)
        for subpath in subpaths:
            if len(subpath) < 2:
                continue
            pts = [self._user_to_pixel(x, y) for x, y in subpath]
            for p0, p1 in zip(pts, pts[1:]):
                self._stroke_segment_pixels(p0, p1, radius, color, alpha)

    def _fill_polygon_pixels(
        self, polygon: List[Point], color: Color, alpha: float
    ) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, int(math.floor(min(ys))))
        max_y = min(self.height - 1, int(math.ceil(max(ys))))
        if min_y > max_y:
            return
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: List[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, int(math.floor(nodes[i])))
                x_end = min(self.width - 1, int(math.ceil(nodes[i + 1])))
                for x in range(x_start, x_end + 1):
                    self._composite_pixel(x, y, color, alpha)

    def _stroke_segment_pixels(
        self, p0: Point, p1: Point, radius: float, color: Color, alpha: float
    ) -> None:
        x0, y0 = p0
        x1, y1 = p1
        min_x = max(0, int(math.floor(min(x0, x1) - radius)))
        max_x = min(self.width - 1, int(math.ceil(max(x0, x1) + radius)))
        min_y = max(0, int(math.floor(min(y0, y1) - radius)))
        max_y = min(self.height - 1, int(math.ceil(max(y0, y1) + radius)))
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
                    self._composite_pixel(x, y, color, alpha)

    def _apply_clip(self, subpaths: List[List[Point]]) -> None:
        if not subpaths:
            return
        next_clip = bytearray(b"\x00" * (self.width * self.height))
        for subpath in subpaths:
            polygon = [self._user_to_pixel(x, y) for x, y in subpath]
            self._rasterize_clip_polygon(polygon, next_clip)
        for i, val in enumerate(next_clip):
            self.canvas.clip[i] = 1 if self.canvas.clip[i] and val else 0

    def _rasterize_clip_polygon(self, polygon: List[Point], mask: bytearray) -> None:
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        min_y = max(0, int(math.floor(min(ys))))
        max_y = min(self.height - 1, int(math.ceil(max(ys))))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            nodes: List[float] = []
            for p0, p1 in zip(polygon, polygon[1:] + polygon[:1]):
                x0, y0 = p0
                x1, y1 = p1
                if (y0 < scan_y <= y1) or (y1 < scan_y <= y0):
                    if y1 != y0:
                        nodes.append(x0 + (scan_y - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                x_start = max(0, int(math.floor(nodes[i])))
                x_end = min(self.width - 1, int(math.ceil(nodes[i + 1])))
                row = y * self.width
                for x in range(x_start, x_end + 1):
                    mask[row + x] = 1

    def _handle_text(
        self,
        op: str,
        operands: List[Any],
        resources_cos: Optional[PdfDictionary],
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
        resources_cos: Optional[PdfDictionary],
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
        for gid, width_1000, applies_word in font.iter_glyphs(raw):
            if gid is not None:
                contours = font.outlines.outline(gid)
                if contours:
                    self._fill_glyph(contours, units_per_em)
            advance = width_1000 / 1000.0 * text.font_size + text.char_spacing
            if applies_word:
                advance += text.word_spacing
            advance *= text.horizontal_scale
            text.text_matrix = _multiply(
                (1.0, 0.0, 0.0, 1.0, advance, 0.0), text.text_matrix
            )

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

    def _fill_glyph(self, contours: List[List[Point]], units_per_em: int) -> None:
        """Fill a glyph's font-unit contours through the text/CTM transform."""
        text = self.state.text
        if units_per_em <= 0 or text.font_size == 0:
            return
        scale = text.font_size / units_per_em
        # glyph space -> text space: scale by font size, apply horizontal
        # scaling on x and the text rise on y.
        glyph_to_text: Matrix = (
            scale * text.horizontal_scale,
            0.0,
            0.0,
            scale,
            0.0,
            text.rise,
        )
        base = _multiply(
            _multiply(self.state.ctm, text.text_matrix), glyph_to_text
        )
        pixel_contours: List[List[Point]] = []
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
        self, contours: List[List[Point]], color: Color, alpha: float
    ) -> None:
        """Scanline-fill multiple contours together using the nonzero rule.

        Filling each contour independently would paint a glyph's counters (the
        hole in ``o``/``e``/``a``); the nonzero winding rule across all contours
        leaves them open, matching TrueType's fill convention.
        """
        ys = [p[1] for contour in contours for p in contour]
        if not ys:
            return
        min_y = max(0, int(math.floor(min(ys))))
        max_y = min(self.height - 1, int(math.ceil(max(ys))))
        for y in range(min_y, max_y + 1):
            scan_y = y + 0.5
            crossings: List[Tuple[float, int]] = []
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
                x_start = int(math.ceil(xa - 0.5))
                x_end = int(math.floor(xb - 0.5))
                if x_end < x_start:
                    # Sub-pixel span: keep one pixel so thin stems do not drop out.
                    if xb <= xa:
                        continue
                    x_start = x_end = int(math.floor((xa + xb) / 2.0))
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
        self, name: Optional[str], resources_cos: Optional[PdfDictionary]
    ) -> Optional[_GlyphFont]:
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
    ) -> Optional[_GlyphFont]:
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
        font: Optional[_GlyphFont] = None
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
        # metric-compatible substitute so the Standard-14 fonts render as real
        # glyphs instead of boxes. Symbol/ZapfDingbats have no substitute.
        if font is None:
            font = self._build_substitute_font(font_dict)
        return font

    def _build_substitute_font(
        self, font_dict: PdfDictionary
    ) -> Optional[_GlyphFont]:
        base = self._cos_name(font_dict.mapping.get(PdfName("BaseFont")))
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        flags = 0
        italic_angle = 0.0
        font_weight: Optional[float] = None
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
        key = resolve_substitute_key(
            base, flags=flags, italic_angle=italic_angle, font_weight=font_weight
        )
        sfnt = load_substitute_sfnt(key)
        if sfnt is None:
            return None
        outlines = TrueTypeOutlines(sfnt)
        if not outlines.ok:
            return None
        code_to_gid = self._substitute_code_to_gid(font_dict, outlines)
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(outlines, code_to_gid, width_1000, bytes_per_code=1)

    def _substitute_code_to_gid(
        self, font_dict: PdfDictionary, outlines: TrueTypeOutlines
    ) -> Callable[[int], Optional[int]]:
        from .font_subset import read_unicode_cmap

        uni = read_unicode_cmap(outlines._data)
        # Default to WinAnsi (cp1252) -- the de-facto Standard-14 Latin encoding
        # used when a font omits /Encoding -- then overlay any explicit PDF
        # /Encoding (named base and/or /Differences) the document declares.
        code_to_unicode: dict[int, int] = {}
        for code in range(256):
            try:
                code_to_unicode[code] = ord(bytes([code]).decode("cp1252"))
            except (UnicodeDecodeError, TypeError):
                pass
        if hasattr(self.pdf, "_simple_code_to_unicode"):
            try:
                code_to_unicode.update(self.pdf._simple_code_to_unicode(font_dict) or {})
            except Exception:
                pass

        def resolve(code: int, _uni=uni, _c2u=code_to_unicode) -> Optional[int]:
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

        return resolve

    def _build_type0_font(self, font_dict: PdfDictionary) -> Optional[_GlyphFont]:
        encoding = self._cos_name(font_dict.mapping.get(PdfName("Encoding")))
        if encoding not in ("Identity-H", "Identity-V", "Identity"):
            return None  # Named CMaps are not decoded yet; fall back to boxes.
        descendants = self._resolve(font_dict.mapping.get(PdfName("DescendantFonts")))
        cidfont = None
        if isinstance(descendants, PdfArray) and descendants.items:
            cidfont = self._resolve(descendants.items[0])
        if not isinstance(cidfont, PdfDictionary):
            return None
        cid_subtype = self._cos_name(cidfont.mapping.get(PdfName("Subtype")))
        descriptor = self._resolve(cidfont.mapping.get(PdfName("FontDescriptor")))
        width_1000 = self._cid_widths(cidfont)
        if cid_subtype == "CIDFontType2":
            outlines = self._load_truetype_outlines(descriptor)
            if outlines is None:
                return None
            cid_to_gid = self._cid_to_gid(cidfont)
        elif cid_subtype == "CIDFontType0":
            program = self._load_fontfile3(descriptor)
            if not program:
                return None
            outlines = CffOutlines(program)
            if not outlines.ok:
                return None
            cid_to_gid = self._cff_cid_to_gid(program)
        else:
            return None
        return _GlyphFont(outlines, cid_to_gid, width_1000, bytes_per_code=2)

    def _build_simple_cff_font(
        self, font_dict: PdfDictionary
    ) -> Optional[_GlyphFont]:
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        program = self._load_fontfile3(descriptor)
        if not program:
            return None
        outlines = CffOutlines(program)
        if not outlines.ok:
            return None
        # Resolve codes through the CFF's own built-in custom Encoding; predefined
        # encodings need the CFF standard-strings tables, so leave those to boxes.
        enc_map = outlines.encoding_code_to_gid()
        if not enc_map:
            return None
        code_to_gid = lambda code, _m=enc_map: _m.get(code)  # noqa: E731
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(outlines, code_to_gid, width_1000, bytes_per_code=1)

    def _cff_cid_to_gid(self, program: bytes) -> Callable[[int], Optional[int]]:
        from .font_subset_cff import cff_charset_cid_to_gid

        charset = cff_charset_cid_to_gid(program)
        if charset:
            return lambda cid, _m=charset: _m.get(cid, cid)
        return lambda cid: cid  # identity / predefined charset: CID == GID.

    def _build_type1_font(
        self, font_dict: PdfDictionary, descriptor: PdfDictionary
    ) -> Optional[_GlyphFont]:
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
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(outlines, code_to_gid, width_1000, bytes_per_code=1)

    def _load_fontfile1(
        self, descriptor: PdfDictionary
    ) -> Optional[Tuple[bytes, Optional[int], Optional[int]]]:
        ref = descriptor.mapping.get(PdfName("FontFile"))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return None
        program = stream.content
        if hasattr(self.pdf, "_decode_cos_stream"):
            try:
                program = self.pdf._decode_cos_stream(stream, ref)
            except Exception:
                program = stream.content
        length1 = self._cos_number(stream.mapping.get(PdfName("Length1")))
        length2 = self._cos_number(stream.mapping.get(PdfName("Length2")))
        return (
            program,
            int(length1) if length1 else None,
            int(length2) if length2 else None,
        )

    def _type1_code_to_gid(
        self, font_dict: PdfDictionary, outlines: Type1Outlines
    ) -> Callable[[int], Optional[int]]:
        # Resolve a code to a glyph name through the font's built-in encoding,
        # overlaid by any PDF /Encoding /Differences, then to a synthetic gid.
        code_to_name = dict(outlines.builtin_encoding)
        enc = self._resolve(font_dict.mapping.get(PdfName("Encoding")))
        if isinstance(enc, PdfDictionary):
            diffs = self._resolve(enc.mapping.get(PdfName("Differences")))
            if isinstance(diffs, PdfArray):
                current = 0
                for item in diffs.items:
                    item = self._resolve(item)
                    if isinstance(item, PdfNumber):
                        current = int(item.value)
                    elif isinstance(item, PdfName):
                        code_to_name[current] = item.name.lstrip("/")
                        current += 1
        name_to_gid = outlines.name_to_gid

        def resolve(code: int, _c2n=code_to_name, _n2g=name_to_gid) -> Optional[int]:
            name = _c2n.get(code)
            return _n2g.get(name) if name is not None else None

        return resolve

    def _build_simple_truetype_font(
        self, font_dict: PdfDictionary
    ) -> Optional[_GlyphFont]:
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        outlines = self._load_truetype_outlines(descriptor)
        if outlines is None:
            return None
        code_to_gid = self._simple_code_to_gid(font_dict, outlines)
        width_1000 = self._simple_widths(font_dict, outlines, code_to_gid)
        return _GlyphFont(outlines, code_to_gid, width_1000, bytes_per_code=1)

    def _load_truetype_outlines(
        self, descriptor: Any
    ) -> Optional[TrueTypeOutlines]:
        if not isinstance(descriptor, PdfDictionary):
            return None
        program = self._load_fontfile2(descriptor)
        if not program:
            return None
        outlines = TrueTypeOutlines(program)
        return outlines if outlines.ok else None

    def _load_fontfile2(self, descriptor: PdfDictionary) -> Optional[bytes]:
        return self._load_font_program(descriptor, "FontFile2")

    def _load_fontfile3(self, descriptor: Any) -> Optional[bytes]:
        if not isinstance(descriptor, PdfDictionary):
            return None
        return self._load_font_program(descriptor, "FontFile3")

    def _load_font_program(
        self, descriptor: PdfDictionary, key: str
    ) -> Optional[bytes]:
        ref = descriptor.mapping.get(PdfName(key))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return None
        if hasattr(self.pdf, "_decode_cos_stream"):
            try:
                return self.pdf._decode_cos_stream(stream, ref)
            except Exception:
                pass
        return stream.content

    def _cid_to_gid(self, cidfont: PdfDictionary) -> Callable[[int], Optional[int]]:
        if hasattr(self.pdf, "_build_cid_to_gid"):
            try:
                return self.pdf._build_cid_to_gid(cidfont)
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
            i = 0
            while i < len(items):
                c = self._cos_number(items[i])
                if c is None:
                    break
                nxt = self._resolve(items[i + 1]) if i + 1 < len(items) else None
                if isinstance(nxt, PdfArray):
                    for j, item in enumerate(nxt.items):
                        wv = self._cos_number(item)
                        if wv is not None:
                            table[int(c) + j] = wv
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
                    for cid in range(int(c), int(clast) + 1):
                        table[cid] = wv
                    i += 3
        return lambda cid, _t=table, _d=default: _t.get(cid, _d)

    def _simple_code_to_gid(
        self, font_dict: PdfDictionary, outlines: TrueTypeOutlines
    ) -> Callable[[int], Optional[int]]:
        from .font_subset import read_symbol_code_to_gid, read_unicode_cmap

        program = outlines._data
        symbol = read_symbol_code_to_gid(program)
        unicode_map = read_unicode_cmap(program)
        code_to_unicode: dict[int, int] = {}
        if hasattr(self.pdf, "_simple_code_to_unicode"):
            try:
                code_to_unicode = self.pdf._simple_code_to_unicode(font_dict) or {}
            except Exception:
                code_to_unicode = {}

        def resolve(
            code: int,
            _sym=symbol,
            _uni=unicode_map,
            _c2u=code_to_unicode,
        ) -> Optional[int]:
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
        code_to_gid: Callable[[int], Optional[int]],
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
        resources_cos: Optional[PdfDictionary],
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
    ) -> Optional[Tuple[int, int, bytes]]:
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
        except Exception:
            data = sm.content
        meta = self._image_meta_from_stream(sm)
        decoded = _decode_image_to_rgb(meta, data)
        if decoded is None:
            return None
        w, h, rgb = decoded
        return (w, h, bytes(rgb[0::3]))  # gray -> R == G == B, take one channel

    def _build_soft_mask(
        self,
        smask: Any,
        resources_cos: Optional[PdfDictionary],
        resources_plain: dict,
    ) -> Optional[bytes]:
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
                group, g_ref, backdrop, track_coverage=not luminosity
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

    def _build_transfer_lut(self, tr: Any) -> Optional[List[int]]:
        tr = self._resolve(tr)
        if tr is None or (
            isinstance(tr, PdfName) and tr.name.lstrip("/") in ("Identity", "Default")
        ):
            return None
        try:
            fn = build_function(self.pdf, tr)
        except Exception:
            fn = None
        if fn is None:
            return None
        lut: List[int] = []
        for v in range(256):
            try:
                out = fn.eval(v / 255.0)
                val = out[0] if isinstance(out, (list, tuple)) else out
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
        seed_pixels: Optional[bytes] = None,
    ) -> "_Canvas":
        """Render a form XObject into a fresh canvas at the current CTM.

        Used for soft-mask groups and unit-composited transparency groups. The
        offscreen render gets a fresh graphics state (no soft mask, empty save
        stack) so it is not modulated by the mask being built. When
        *seed_pixels* is given the canvas starts from that backdrop copy (for
        group compositing) instead of the solid *background*.
        """
        off = _Canvas(self.width, self.height, background)
        if seed_pixels is not None:
            off.pixels = bytearray(seed_pixels)
        if track_coverage:
            off.coverage = bytearray(self.width * self.height)
        saved_canvas = self.canvas
        saved_state = self.state
        saved_stack = self.state_stack
        self.canvas = off
        self.state = _GraphicsState(ctm=saved_state.ctm)
        self.state_stack = []
        try:
            self._paint_form(
                group, ref, self.resources_cos, self.resources_plain, depth=0
            )
        finally:
            self.canvas = saved_canvas
            self.state = saved_state
            self.state_stack = saved_stack
        return off

    def _paint_form(
        self,
        stream: PdfStream,
        ref: Any,
        parent_resources_cos: Optional[PdfDictionary],
        parent_resources_plain: dict,
        depth: int,
    ) -> None:
        matrix = (
            _cos_matrix(self._resolve(stream.mapping.get(PdfName("Matrix"))))
            or IDENTITY
        )
        # A transparency group drawn under a constant alpha < 1 must be
        # composited as a unit, otherwise overlapping elements inside it
        # double-darken. (Per-element soft mask / blend already compose
        # correctly through _composite_pixel, so only group alpha needs this.)
        if (
            not self._in_soft_mask
            and self.state.fill_alpha < 1.0
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
    ) -> Optional[Tuple[int, int, int, int]]:
        bbox = self._resolve(stream.mapping.get(PdfName("BBox")))
        if not isinstance(bbox, PdfArray) or len(bbox.items) < 4:
            return None
        v = [self._cos_number(it) or 0.0 for it in bbox.items[:4]]
        full = _multiply(matrix, self.state.ctm)
        corners = [(v[0], v[1]), (v[2], v[1]), (v[2], v[3]), (v[0], v[3])]
        dev = [
            self._user_to_pixel(*_transform_point(full, ux, uy)) for ux, uy in corners
        ]
        min_x = max(0, int(math.floor(min(p[0] for p in dev))))
        max_x = min(self.width - 1, int(math.ceil(max(p[0] for p in dev))))
        min_y = max(0, int(math.floor(min(p[1] for p in dev))))
        max_y = min(self.height - 1, int(math.ceil(max(p[1] for p in dev))))
        if min_x > max_x or min_y > max_y:
            return None
        return (min_x, min_y, max_x, max_y)

    def _paint_group_composited(
        self, stream: PdfStream, ref: Any, region: Tuple[int, int, int, int]
    ) -> None:
        """Render a transparency group offscreen and composite it as a unit.

        The group is rendered at full opacity over a copy of the current
        backdrop, then composited back within *region* at the group's constant
        alpha (further modulated by any active soft mask). Treated as an
        isolated group; knockout is not modelled.
        """
        group_alpha = self.state.fill_alpha
        blend = self.state.blend_mode
        sm = self.state.soft_mask
        off = self._render_form_offscreen(
            stream,
            ref,
            self.background,
            track_coverage=True,
            seed_pixels=bytes(self.canvas.pixels),
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
                main.set_pixel(x, y, (px[o], px[o + 1], px[o + 2]), alpha, blend)

    def _paint_image_pixels(
        self,
        meta: dict,
        data: bytes,
        matrix: Matrix,
        smask: Optional[Tuple[int, int, bytes]] = None,
    ) -> None:
        image = _decode_image_to_rgb(meta, data)
        if image is None:
            return
        width, height, pixels = image
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
        min_x = max(0, int(math.floor(min(p[0] for p in dev))))
        max_x = min(self.width - 1, int(math.ceil(max(p[0] for p in dev))))
        min_y = max(0, int(math.floor(min(p[1] for p in dev))))
        max_y = min(self.height - 1, int(math.ceil(max(p[1] for p in dev))))
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
                self._composite_pixel(px, py, color, alpha)

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
    ) -> Tuple[str, Optional[int], Optional[bytes], Optional[int]]:
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

    def _terminal_filter(self, filt: Any) -> Optional[str]:
        filt = self._resolve(filt)
        if isinstance(filt, PdfName):
            return filt.name.lstrip("/")
        if isinstance(filt, PdfArray) and filt.items:
            return self._cos_name(filt.items[-1])
        return None

    def _resource_dict(
        self, resources: PdfDictionary, name: str
    ) -> Optional[PdfDictionary]:
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

    def _cos_number(self, obj: Any) -> Optional[float]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return float(obj.value)
        if isinstance(obj, (int, float)):
            return float(obj)
        return None

    def _cos_name(self, obj: Any) -> Optional[str]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        if isinstance(obj, str):
            return obj.lstrip("/")
        return None

    def _transform(self, x: float, y: float) -> Point:
        return _transform_point(self.state.ctm, x, y)

    def _user_to_pixel(self, x: float, y: float) -> Point:
        dx, dy = self._user_to_display(x, y)
        return (
            dx * self.point_scale,
            (self.page_height_pts - dy) * self.point_scale,
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


def _decode_image_to_rgb(meta: dict, data: bytes) -> Optional[Tuple[int, int, bytes]]:
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    filt = str(meta.get("filter") or "").lstrip("/")
    sniff = ext_from_magic(data)
    if filt in ("DCTDecode", "DCT") or sniff == "jpg":
        from .dct import decode as decode_jpeg

        decoded = decode_jpeg(data)
        if decoded is None:
            return None
        pixels = decoded.samples
        if decoded.mode == "L":
            pixels = gray_to_rgb(pixels)
        elif decoded.mode == "CMYK":
            pixels = cmyk_to_rgb(pixels)
        return (decoded.width, decoded.height, pixels)
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


def _write_tiff_rgb(width: int, height: int, pixels: bytes, dpi: float) -> bytes:
    data = bytearray()
    ifd_offset = 8
    num_entries = 13
    extra_offset = ifd_offset + 2 + num_entries * 12 + 4

    def add_extra(blob: bytes) -> int:
        off = extra_offset + len(data)
        data.extend(blob)
        if len(data) % 2:
            data.append(0)
        return off

    bits_off = add_extra(struct.pack("<3H", 8, 8, 8))
    xres_off = add_extra(struct.pack("<II", max(1, int(round(dpi))), 1))
    yres_off = add_extra(struct.pack("<II", max(1, int(round(dpi))), 1))
    pixel_off = extra_offset + len(data)
    byte_count = len(pixels)

    entries = [
        _tiff_entry(256, 4, 1, width),
        _tiff_entry(257, 4, 1, height),
        _tiff_entry(258, 3, 3, bits_off),
        _tiff_entry(259, 3, 1, 1),
        _tiff_entry(262, 3, 1, 2),
        _tiff_entry(273, 4, 1, pixel_off),
        _tiff_entry(277, 3, 1, 3),
        _tiff_entry(278, 4, 1, height),
        _tiff_entry(279, 4, 1, byte_count),
        _tiff_entry(282, 5, 1, xres_off),
        _tiff_entry(283, 5, 1, yres_off),
        _tiff_entry(284, 3, 1, 1),
        _tiff_entry(296, 3, 1, 2),
    ]

    out = bytearray(b"II")
    out.extend(struct.pack("<HI", 42, ifd_offset))
    out.extend(struct.pack("<H", len(entries)))
    out.extend(b"".join(entries))
    out.extend(struct.pack("<I", 0))
    out.extend(data)
    out.extend(pixels)
    return bytes(out)


def _tiff_entry(tag: int, typ: int, count: int, value: int) -> bytes:
    return struct.pack("<HHII", tag, typ, count, value)


def _coerce_rgb(value: Sequence[int]) -> Color:
    if len(value) < 3:
        raise PdfValidationException("background must contain three RGB components")
    return (_byte(value[0]), _byte(value[1]), _byte(value[2]))


def _byte(value: float | int) -> int:
    return int(max(0, min(255, round(float(value)))))


def _normalize_blend_mode(name: str) -> Optional[str]:
    return _BLEND_MODES.get(name.lstrip("/").lower())


def _blend_color(source: Color, backdrop: Color, mode: str) -> Color:
    if mode == "Normal":
        return source
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


def _is_operator(token: Any) -> bool:
    return (
        isinstance(token, str)
        and not token.startswith("/")
        and token not in ("<<", ">>")
    )


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _last_numbers(operands: Sequence[Any], count: int) -> Optional[List[float]]:
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


def _invert_matrix(m: Matrix) -> Optional[Matrix]:
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


def _cos_matrix(obj: Any) -> Optional[Matrix]:
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
