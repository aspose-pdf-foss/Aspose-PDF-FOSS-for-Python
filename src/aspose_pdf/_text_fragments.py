"""Write-through edits for text fragments collected from a real PDF page."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise
from typing import TYPE_CHECKING

from aspose_pdf.engine.text_edit import (
    _aligned_spans,
    _apply_replacements,
    _edit_run,
    _lex,
    _run_char_data,
    _walk_show_runs,
)
from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException

if TYPE_CHECKING:
    from aspose_pdf.pages import Page
    from aspose_pdf.text import TextFragment


class FragmentEditSession:
    """Rebuild edits against one visit's snapshot, keeping siblings independent.

    Character offsets in extracted text cannot be used as byte offsets in a
    content stream. Map an occurrence only when extraction and editable show
    runs contain the same sequence of occurrences. Refuse ambiguous matches
    and stale content instead of replacing another occurrence by accident.
    """

    def __init__(self, page: Page, text: str, fragments: list[TextFragment]):
        self.page = page
        self.text = text
        self.fragments = fragments
        self.positions = {id(item): (item.start, item.end) for item in fragments}
        self.original = page.content
        self.current = self.original
        self.engine = page._document._engine_pdf
        self.page_dictionary = self.engine._get_page_dict(page._index)
        self.changes: dict[int, str] = {}
        self.targets: dict[int, tuple[int, int, int]] = {}
        self.runs = None
        for fragment in fragments:
            fragment._editor = self

    def _target(self, fragment: TextFragment) -> tuple[int, int, int]:
        key = id(fragment)
        if key in self.targets:
            return self.targets[key]
        engine = self.page._document._engine_pdf
        budget = engine._load_budget
        if self.runs is None:
            metric = engine._build_simple_font_metrics(self.page._index)
            codec = engine._build_text_codecs(
                self.page._index, metric_for_name=metric
            )
            self.runs = _walk_show_runs(
                _lex(self.original, budget=budget),
                metric_for_name=metric,
                codec_for_name=codec,
                budget=budget,
            )

        original_start, original_end = self.positions[key]
        phrase = self.text[original_start:original_end]
        if not phrase:
            raise UnsupportedFeatureException("Cannot edit an empty text match")
        occurrences = []
        for match in re.finditer(re.escape(phrase), self.text):
            budget.check(
                len(occurrences) + 1, "max_container_items", "text fragment occurrences"
            )
            occurrences.append(match)
        ordinal = next(
            (i for i, match in enumerate(occurrences)
             if (match.start(), match.end()) == (original_start, original_end)),
            None,
        )
        found = []
        for run_index, run in enumerate(self.runs):
            _infos, full, entries, _starts = _run_char_data(run, budget=budget)
            for start, end, _match in _aligned_spans(
                full, entries, phrase, True, 0, budget=budget
            ):
                budget.check(
                    len(found) + 1, "max_container_items", "editable text fragments"
                )
                found.append((run_index, start, end))
        if ordinal is None or len(found) != len(occurrences):
            raise UnsupportedFeatureException(
                "The fragment cannot be mapped unambiguously to page content; "
                "use Page.replace_text() for supported content-stream searches"
            )
        self.targets[key] = found[ordinal]
        return found[ordinal]

    def replace(self, fragment: TextFragment, value: str) -> None:
        """Commit exactly this fragment's edit after validating the whole plan."""
        self.page._document._ensure_not_disposed()
        if not isinstance(value, str):
            raise TypeError("fragment.text must be a string")
        engine = self.page._document._engine_pdf
        if (
            engine is not self.engine
            or not 0 <= self.page._index < len(engine.pages)
            or engine._get_page_dict(self.page._index) is not self.page_dictionary
            or self.page.content != self.current
        ):
            raise PdfValidationException(
                "Page content changed after text collection; collect fragments again"
            )
        if value == fragment.text:
            return
        changes = dict(self.changes)
        changes[id(fragment)] = value
        self._target(fragment)
        grouped = defaultdict(dict)
        for key, replacement in changes.items():
            run_index, start, end = self.targets[key]
            grouped[run_index][start, end] = replacement

        engine = self.page._document._engine_pdf
        budget = engine._load_budget
        edits = []
        for run_index, replacements in grouped.items():
            spans = sorted(replacements)
            if any(end > start for (_, end), (start, _) in pairwise(spans)):
                raise UnsupportedFeatureException("Cannot edit overlapping text fragments")
            run_edits, count = _edit_run(
                self.runs[run_index], "", "",
                case_sensitive=True,
                max_count=0,
                budget=budget,
                _spans=[(start, end, None) for start, end in spans],
                _replacement_for_span=lambda start, end: replacements[start, end],
            )
            if count != len(spans):
                raise UnsupportedFeatureException("The fragment is not editable")
            budget.check(
                len(edits) + len(run_edits),
                "max_container_items",
                "text fragment edits",
            )
            edits.extend(run_edits)
        updated = _apply_replacements(self.original, edits, budget=budget)
        engine._set_page_content(self.page._index, updated)
        engine._extracted_text = None
        self.current = updated
        self.changes = changes
        for sibling in self.fragments:
            sibling.rect = None
            sibling.quads = None
