"""Extract word snapshots from measured glyph units in their actual resource scope."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from aspose_pdf.text import TextWord

from .cos import decode_pdf_name
from .text_locate import _apply
from .text_region import _RegionParser, validate_clip
from .text_selection import page_indexes, validate_skip_artifacts

if TYPE_CHECKING:
    from aspose_pdf.document import Document


class _WordParser(_RegionParser):
    def __init__(self, *args, **kwargs):
        self.words: list[TextWord] = []
        self._units: dict = {}
        self._run_count = 0
        self._word_text: list[str] = []
        self._word_key = None
        self._word_box = None
        self._word_matrix = None
        self._inline_actual = False
        super().__init__(*args, **kwargs)

    def _record_run(self, run, infos, starts, geometry) -> None:
        self._run_count += 1
        gap = False
        for segment, (kind, text, units), start in zip(run.segments, infos, starts):
            if segment.token is None:
                gap = gap or bool(text)
                continue
            if kind != "cid":
                units = [(offset, 1, value, False) for offset, value in enumerate(text)]
            measured = []
            character = start
            for offset, length, value, opaque in units:
                if value:
                    self._budget.check(
                        len(measured) + 1, "max_container_items", "word glyph units"
                    )
                    measured.append((offset, length, geometry[character], opaque))
                character += len(value)
            self._budget.check(
                len(self._units) + 1, "max_container_items", "word operands"
            )
            self._units[segment.token.start] = (
                self._run_count,
                run.origin_trm,
                gap,
                measured,
            )
            gap = False

    def _finish_word(self) -> None:
        if self._word_text:
            x0, y0, x1, y1 = self._word_box
            quad = tuple(
                _apply(self._word_matrix, x, y)
                for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            )
            if all(math.isfinite(value) for point in quad for value in point):
                xs, ys = zip(*quad)
                rect = (min(xs), min(ys), max(xs), max(ys))
                self._budget.check(
                    len(self._word_text), "max_object_bytes", "word text"
                )
                text = "".join(self._word_text)
                self._budget.check(
                    len(text.encode("utf-8")), "max_object_bytes", "word text"
                )
                self._budget.check(
                    len(self.words) + 1, "max_container_items", "extracted words"
                )
                self.words.append(TextWord(self.page_index, text, rect, quad))
        self._word_text = []
        self._word_key = self._word_box = self._word_matrix = None

    def _emit_text(self, raw: bytes) -> None:
        placement = self._units.get(getattr(raw, "offset", -1))
        if placement is None:
            self._finish_word()
        else:
            run, matrix, gap, units = placement
            if gap:
                self._finish_word()
            for offset, length, box, opaque in units:
                if opaque:
                    self._finish_word()
                    continue
                text = self._decode_bytes(raw[offset : offset + length])
                key = (run, box[4])
                for character in text:
                    if character.isspace():
                        self._finish_word()
                        continue
                    if self._word_key != key:
                        self._finish_word()
                    self._word_key, self._word_matrix = key, matrix
                    x0, x1, y0, y1, _line = box
                    bounds = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                    if not all(math.isfinite(value) for value in bounds):
                        self._finish_word()
                        continue
                    if self._word_box is not None:
                        a, b, c, d = self._word_box
                        bounds = (
                            min(a, bounds[0]),
                            min(b, bounds[1]),
                            max(c, bounds[2]),
                            max(d, bounds[3]),
                        )
                    self._word_box = bounds
                    self._budget.check(
                        len(self._word_text) + 1,
                        "max_container_items",
                        "word characters",
                    )
                    self._word_text.append(character)
        super()._emit_text(raw)

    def _emit_gap(self) -> None:
        self._finish_word()
        super()._emit_gap()

    def _handle_operator(self, op, ops) -> None:
        if op in ("BT", "ET"):
            self._finish_word()
        super()._handle_operator(op, ops)

    def _marked_operands(self, stack):
        self._inline_actual = False
        if stack and stack[-1] == ">>":
            depth = 0
            for index in range(len(stack) - 1, -1, -1):
                token = stack[index]
                if token == ">>":
                    depth += 1
                elif token == "<<":
                    depth -= 1
                    if depth == 0:
                        break
                elif (
                    depth == 1
                    and isinstance(token, str)
                    and decode_pdf_name(token).lstrip("/") == "ActualText"
                    and index + 1 < len(stack)
                    and isinstance(stack[index + 1], bytes)
                ):
                    self._inline_actual = True
        return super()._marked_operands(stack)

    def _handle_marked_content(self, op, operands) -> None:
        self._finish_word()
        super()._handle_marked_content(op, operands)
        if op == "BDC" and self._inline_actual and self._marked_content:
            # Logical replacement text has no reliable per-word glyph mapping.
            # Suppress its glyphs without altering ordinary text extraction.
            self._marked_content[-1].actual_text = ""

    def _show_form_xobject(self, operand) -> None:
        self._finish_word()
        if self._inside_actual_text():
            return
        super()._show_form_xobject(operand)

    def _merge_form(self, nested) -> None:
        self._budget.check(
            len(self.words) + len(nested.words),
            "max_container_items",
            "extracted form words",
        )
        self.words.extend(nested.words)

    def extract_text(self) -> str:
        text = super().extract_text()
        self._finish_word()
        return text


def _overlaps(rect, clip) -> bool:
    x0, y0, x1, y1 = rect
    a, b, c, d = clip
    epsilon = 1e-7
    horizontal = a <= x0 <= c if x0 == x1 else x0 < c - epsilon and x1 > a + epsilon
    return horizontal and y0 < d - epsilon and y1 > b + epsilon


def extract_words(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    clip: Sequence[float] | None = None,
    skip_artifacts: bool = False,
) -> list[TextWord]:
    """Validate the entire request, then inspect only selected page streams."""
    skip_artifacts = validate_skip_artifacts(skip_artifacts)
    rectangle = None if clip is None else validate_clip(clip)
    count = document.page_count
    indexes = page_indexes(pages, count, limits=document.load_limits)
    indexes = range(count) if indexes is None else indexes
    pdf = document._engine_pdf
    pdf._load_budget.check(len(indexes), "max_container_items", "word page selection")
    words = []
    for index in indexes:
        page = pdf._get_page_dict(index)
        resources = pdf._resolve_resources_cos(page) if page is not None else None
        parser = _WordParser(
            pdf,
            index,
            pdf.get_page_content(index),
            resources,
            (-math.inf, -math.inf, math.inf, math.inf),
            skip_artifacts=skip_artifacts,
        )
        parser.extract_text()
        selected = (
            parser.words
            if rectangle is None
            else [word for word in parser.words if _overlaps(word.rect, rectangle)]
        )
        pdf._load_budget.check(
            len(words) + len(selected),
            "max_container_items",
            "extracted document words",
        )
        words.extend(selected)
        del selected, parser
    return words
