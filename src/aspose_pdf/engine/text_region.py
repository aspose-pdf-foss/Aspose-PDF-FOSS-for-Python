"""Filter positioned glyphs by rectangles without changing PDF content."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

from .content_stream_parser import _MAX_FORM_DEPTH, ContentStreamParser
from .cos import PdfArray, PdfDictionary, PdfName, PdfStream
from .optional_content import OptionalContent
from .text_edit import _lex, _mat_mul, _run_char_data, _walk_show_runs
from .text_locate import _apply, _char_geometry

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def validate_clip(clip: Sequence[float]) -> tuple[float, float, float, float]:
    """Require four finite coordinates enclosing a rectangle of positive area."""
    if (
        not isinstance(clip, Sequence)
        or isinstance(clip, (str, bytes, bytearray))
        or len(clip) != 4
    ):
        raise PdfValidationException("clip must contain exactly four coordinates")
    try:
        if any(isinstance(value, bool) for value in clip):
            raise ValueError("boolean coordinate")
        box = tuple(float(value) for value in clip)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PdfValidationException("clip coordinates must be finite numbers") from exc
    if not all(math.isfinite(value) for value in box):
        raise PdfValidationException("clip coordinates must be finite numbers")
    if box[2] <= box[0] or box[3] <= box[1]:
        raise PdfValidationException("clip must have ordered corners and positive area")
    return box


def validate_exclude_regions(
    regions: Iterable[Sequence[float]] | None,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[tuple[float, float, float, float], ...]:
    """Snapshot a bounded iterable of positive-area exclusion rectangles."""
    if regions is None:
        return ()
    if isinstance(regions, (str, bytes, bytearray, Mapping)):
        raise PdfValidationException("exclude_regions must be an iterable of rectangles")
    try:
        iterator = iter(regions)
    except TypeError:
        raise PdfValidationException(
            "exclude_regions must be an iterable of rectangles"
        ) from None
    budget = _LoadBudget(limits)
    rectangles = []
    for position, region in enumerate(iterator):
        budget.check(position + 1, "max_container_items", "text exclusion rectangles")
        try:
            rectangles.append(validate_clip(region))
        except PdfValidationException as exc:
            raise PdfValidationException(f"exclude_regions[{position}]: {exc}") from exc
    return tuple(rectangles)


class _PositionedBytes(bytes):
    """A parser operand retaining its byte offset in the source stream."""

    def __new__(cls, value: bytes, offset: int):
        instance = super().__new__(cls, value)
        instance.offset = offset
        return instance


class _RegionParser(ContentStreamParser):
    def __init__(
        self,
        pdf: Any,
        page_index: int,
        content: bytes,
        resources: Any,
        clip: tuple[float, float, float, float],
        *,
        ctm: tuple = _IDENTITY,
        active_forms: frozenset[int] = frozenset(),
        font_cache: dict | None = None,
        optional_content: OptionalContent | None = None,
        skip_artifacts: bool = False,
        exclude_regions: tuple = (),
        region_work: list[int] | None = None,
    ):
        self.pdf = pdf
        self.page_index = page_index
        self.resource_scope = resources
        self.clip = clip
        self.exclude_regions = exclude_regions
        self.region_work = [0] if region_work is None else region_work
        self.active_forms = active_forms
        self.font_cache = {} if font_cache is None else font_cache
        self.optional_content = (
            OptionalContent(pdf) if optional_content is None else optional_content
        )
        mapping = {}
        if isinstance(resources, PdfDictionary):
            # Decode font maps and named properties, never uninvoked forms/images.
            mapping = {
                key: resources.mapping[key]
                for key in (PdfName("Font"), PdfName("Properties"))
                if key in resources.mapping
            }
        properties = pdf._resolve(mapping.get(PdfName("Properties")))
        hidden = (
            frozenset(
                name.name.lstrip("/")
                for name, value in properties.mapping.items()
                if not self.optional_content.is_visible(value)
            )
            if isinstance(properties, PdfDictionary)
            else frozenset()
        )
        super().__init__(
            content,
            pdf._convert_cos_to_dict(PdfDictionary(mapping)),
            budget=pdf._load_budget,
            hidden_oc_names=hidden,
            skip_artifacts=skip_artifacts,
        )
        self._selected: dict[int, list[tuple[int, int]]] = {}
        self._placements: dict[int, tuple[str, tuple]] = {}
        self._actual_hits: list[bool] = []
        self._actual_excluded: list[bool] = []
        self._excluded_operands: set[int] = set()
        self._had_excluded = False
        self._pending_region_gap = False

        def placed(end: int, name: str, matrix: tuple) -> None:
            self._budget.check(
                len(self._placements) + 1,
                "max_container_items",
                "region form placements",
            )
            self._placements[end] = (name, matrix)

        runs = _walk_show_runs(
            _lex(content, budget=self._budget, skip_inline_images=True),
            metric_for_name=pdf._build_simple_font_metrics(
                page_index, resources=resources, shared_cache=self.font_cache
            ),
            base_ctm=ctm,
            on_xobject=placed,
            budget=self._budget,
        )
        for run in runs:
            if not run.geometry_ok or run.origin_trm is None:
                continue
            infos, _text, _entries, starts = _run_char_data(run, budget=self._budget)
            geometry = _char_geometry(run, infos, budget=self._budget)
            if geometry is None:
                continue
            self._record_run(run, infos, starts, geometry)
            for segment, (kind, text, units), start in zip(run.segments, infos, starts):
                if segment.token is None:
                    continue
                if kind == "cid":
                    units = [
                        (offset, length, value)
                        for offset, length, value, _opaque in units
                    ]
                else:
                    units = [(offset, 1, value) for offset, value in enumerate(text)]
                selected = []
                character = start
                for offset, length, value in units:
                    bounds = self._glyph_bounds(run.origin_trm, geometry[character]) if value else None
                    excluded = self._is_excluded(bounds)
                    if excluded:
                        self._excluded_operands.add(segment.token.start)
                    if bounds is not None and not excluded and self._bounds_overlap(bounds, self.clip):
                        self._budget.check(
                            len(selected) + 1,
                            "max_container_items",
                            "region selected glyphs",
                        )
                        selected.append((offset, length))
                    character += len(value)
                self._budget.check(
                    len(self._selected) + 1,
                    "max_container_items",
                    "region text operands",
                )
                self._selected[segment.token.start] = selected

    def _record_run(self, run, infos, starts, geometry) -> None:
        """Allow structured extraction to retain the same measured glyph units."""

    @staticmethod
    def _glyph_bounds(matrix: tuple, geometry: tuple) -> tuple | None:
        x0, x1, y0, y1, _key = geometry
        points = [
            _apply(matrix, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        ]
        xs, ys = zip(*points)
        left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            return None
        return left, bottom, right, top

    @staticmethod
    def _bounds_overlap(bounds: tuple, rectangle: tuple) -> bool:
        left, bottom, right, top = bounds
        rx0, ry0, rx1, ry1 = rectangle
        # Ignore roundoff at shared edges of adjacent glyphs and the rectangle.
        epsilon = 1e-7
        if left == right:
            return rx0 <= left <= rx1 and bottom < ry1 - epsilon and top > ry0 + epsilon
        return (
            left < rx1 - epsilon
            and right > rx0 + epsilon
            and bottom < ry1 - epsilon
            and top > ry0 + epsilon
        )

    def _is_excluded(self, bounds: tuple | None) -> bool:
        if bounds is None:
            return False
        for rectangle in self.exclude_regions:
            self.region_work[0] += 1
            self._budget.check(
                self.region_work[0], "max_content_tokens", "text exclusion comparisons"
            )
            if self._bounds_overlap(bounds, rectangle):
                return True
        return False

    def _read_string(self) -> bytes:
        offset = self._pos
        return _PositionedBytes(super()._read_string(), offset)

    def _read_hex_string(self) -> bytes:
        offset = self._pos
        return _PositionedBytes(super()._read_hex_string(), offset)

    def _tokenize(self):
        # Decoding must use the font restored by Q, matching the geometry walker.
        fonts = []
        for token in super()._tokenize():
            if token == "q":
                fonts.append((self._current_font, self._font_size, self._leading))
            elif token == "Q" and fonts:
                self._current_font, self._font_size, self._leading = fonts.pop()
                self._prepare_font_maps()
            yield token

    def _selected_text(self, raw: bytes) -> str:
        selected = self._selected.get(getattr(raw, "offset", -1), ())
        if not selected:
            return ""
        # Select complete code units so ligatures and multibyte CIDs stay intact.
        if not self.exclude_regions:
            data = b"".join(raw[offset : offset + length] for offset, length in selected)
            return self._decode_bytes(data)
        # Separate retained pieces instead of inventing a word across an omission.
        pieces = []
        units = iter(selected)
        start, length = next(units)
        end = start + length
        for offset, length in units:
            if offset != end:
                pieces.append(self._decode_bytes(raw[start:end]))
                start = offset
            end = offset + length
        pieces.append(self._decode_bytes(raw[start:end]))
        output = [pieces[0]]
        previous = pieces[0]
        for piece in pieces[1:]:
            if previous and piece and not previous[-1].isspace() and not piece[0].isspace():
                output.append(" ")
            output.append(piece)
            if piece:
                previous = piece
        return "".join(output)

    def _append_selected(self, text: str) -> None:
        if not text:
            return
        self._separate_from_previous_show()
        if (
            self._pending_region_gap
            and self._buffer
            and not self._buffer[-1].endswith((" ", "\n"))
            and (not self.exclude_regions or not text[0].isspace())
        ):
            self._buffer.append(" ")
        self._pending_region_gap = False
        self._buffer.append(text)

    def _emit_text(self, raw: bytes) -> None:
        if self.exclude_regions:
            if not raw:
                return
            if getattr(raw, "offset", -1) in self._excluded_operands:
                self._had_excluded = True
            selected = self._selected.get(getattr(raw, "offset", -1), ())
            if not selected:
                self._pending_region_gap = True
                return
            if selected[0][0] > 0:
                self._pending_region_gap = True
        self._append_selected(self._selected_text(raw))
        if self.exclude_regions and selected[-1][0] + selected[-1][1] < len(raw):
            self._pending_region_gap = True

    def _emit_gap(self) -> None:
        self._pending_region_gap = True

    def _handle_operator(self, op: str, ops: list[Any]) -> None:
        if (
            self._inside_actual_text()
            and not self._oc_hidden_depth
            and not self._artifact_depth
            and op in ("Tj", "TJ", "'", '"')
        ):
            operands = (
                ops[0] if op == "TJ" and ops and isinstance(ops[0], list) else ops[-1:]
            )
            if any(
                isinstance(raw, bytes) and self._selected_text(raw) for raw in operands
            ):
                if self._actual_hits:
                    self._actual_hits[-1] = True
            if any(
                isinstance(raw, bytes)
                and getattr(raw, "offset", -1) in self._excluded_operands
                for raw in operands
            ):
                self._had_excluded = True
                if self._actual_excluded:
                    self._actual_excluded[-1] = True
        super()._handle_operator(op, ops)

    def _handle_marked_content(self, op: str, operands: list[Any]) -> None:
        if op != "EMC":
            self._budget.check(
                len(self._actual_hits) + 1,
                "max_nesting_depth",
                "region marked-content depth",
            )
            self._budget.check(
                len(self._actual_hits) + 1,
                "max_container_items",
                "region marked-content stack",
            )
            super()._handle_marked_content(op, operands)
            self._actual_hits.append(False)
            self._actual_excluded.append(False)
            return
        hit = self._actual_hits.pop() if self._actual_hits else False
        excluded = self._actual_excluded.pop() if self._actual_excluded else False
        if hit and self._actual_hits:
            self._actual_hits[-1] = True
        if excluded and self._actual_excluded:
            self._actual_excluded[-1] = True
        before = len(self._buffer)
        super()._handle_marked_content(op, operands)
        if len(self._buffer) > before:
            replacement = self._buffer.pop()
            if hit and not excluded:
                self._append_selected(replacement)
            elif excluded:
                self._pending_region_gap = True

    def _show_form_xobject(self, operand: Any) -> None:
        if self._artifact_depth:
            return
        placement = self._placements.get(self._pos)
        if placement is None or not isinstance(self.resource_scope, PdfDictionary):
            return
        name, ctm = placement
        xobjects = self.pdf._resolve(
            self.resource_scope.mapping.get(PdfName("XObject"))
        )
        if not isinstance(xobjects, PdfDictionary):
            return
        reference = xobjects.mapping.get(PdfName(name.lstrip("/")))
        form = self.pdf._resolve(reference)
        if not isinstance(form, PdfStream) or form.mapping.get(
            PdfName("Subtype")
        ) != PdfName("Form"):
            return
        if (
            self._oc_hidden_depth
            or not self.optional_content.is_visible(form.mapping.get(PdfName("OC")))
            or id(form) in self.active_forms
            or len(self.active_forms) >= _MAX_FORM_DEPTH
        ):
            return
        self._budget.check(
            len(self.active_forms) + 1, "max_nesting_depth", "region form depth"
        )
        matrix = self.pdf._resolve(form.mapping.get(PdfName("Matrix")))
        if matrix is not None:
            if not isinstance(matrix, PdfArray) or len(matrix.items) != 6:
                return
            try:
                values = tuple(
                    float(self.pdf._resolve(value).value) for value in matrix.items
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                return
            if not all(math.isfinite(value) for value in values):
                return
            ctm = _mat_mul(values, ctm)
        resources = self.pdf._resolve(form.mapping.get(PdfName("Resources")))
        if not isinstance(resources, PdfDictionary) or not resources.mapping:
            resources = self.resource_scope
        nested = type(self)(
            self.pdf,
            self.page_index,
            self.pdf._decode_cos_stream(form, reference),
            resources,
            self.clip,
            ctm=ctm,
            active_forms=self.active_forms | {id(form)},
            font_cache=self.font_cache,
            optional_content=self.optional_content,
            skip_artifacts=self._skip_artifacts,
            exclude_regions=self.exclude_regions,
            region_work=self.region_work,
        )
        text = nested.extract_text()
        if nested._had_excluded:
            self._had_excluded = True
            if self._actual_excluded:
                self._actual_excluded[-1] = True
        if nested._skipped_artifacts:
            self._note_skipped_artifact()
        if not text:
            if nested._had_excluded:
                self._pending_region_gap = True
            return
        self._note_visible_text()
        if self._inside_actual_text():
            if self._actual_hits:
                self._actual_hits[-1] = True
            return
        self._merge_form(nested)
        if self._buffer:
            self._buffer.append("\n")
        self._buffer.append(text)
        self._last_shown_y = self._last_shown_x = None
        self._force_break = True

    def _merge_form(self, nested: _RegionParser) -> None:
        """Allow structured extraction to consume an invoked form's results."""


def extract_region(
    pdf: Any, page_index: int, clip: Sequence[float] | None, *,
    skip_artifacts: bool = False, exclude_regions: tuple = (),
) -> str:
    """Extract only glyphs with reliable bounds overlapping the requested area."""
    rectangle = (-math.inf, -math.inf, math.inf, math.inf) if clip is None else validate_clip(clip)
    page = pdf._get_page_dict(page_index)
    resources = pdf._resolve_resources_cos(page) if page is not None else None
    return _RegionParser(
        pdf,
        page_index,
        pdf.get_page_content(page_index),
        resources,
        rectangle,
        skip_artifacts=skip_artifacts,
        exclude_regions=exclude_regions,
    ).extract_text()
