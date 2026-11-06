"""High-level PDF document comparison API.

This module provides the ``DocumentComparison`` class and supporting types
for comparing PDF documents and working with the results.

"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aspose_pdf import Document
    from aspose_pdf.text_comparison import PageTextComparison
    from aspose_pdf.visual_comparison import PageVisualComparison


__all__ = ["ComparisonOptions", "DocumentComparison", "ContentChange", "ChangeType"]


class ChangeType:
    """Constants for change types returned by DocumentComparison.

    Each constant represents a category of change detected during comparison:

    - ``ADDED``: Content present in the second document but not the first
    - ``DELETED``: Content present in the first document but not the second
    - ``MODIFIED``: Content present in both documents but with differences
    - ``ADDED_PAGE``: A page exists only in the second document
    - ``DELETED_PAGE``: A page exists only in the first document
    - ``MODIFIED_PAGE``: A page exists in both but differs (text or visual)
    """
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    ADDED_PAGE = "added_page"
    DELETED_PAGE = "deleted_page"
    MODIFIED_PAGE = "modified_page"


@dataclass(frozen=True, slots=True)
class ContentChange:
    """A single change detected between two documents.

    Attributes:
        change_type: One of the ChangeType constants describing the kind of change
        page_number: The one-based page number where the change occurred,
            or None if the change affects the whole document
        description: A human-readable description of the change
        page_index: The zero-based page index (for internal use)
        other_page_index: The zero-based page index in the other document
    """
    change_type: str
    page_number: int | None
    description: str
    page_index: int | None = None
    other_page_index: int | None = None


@dataclass
class DocumentComparison:
    """Results of comparing two PDF documents.

    This class wraps the low-level page comparison results and provides
    convenient methods for querying, filtering, and exporting comparison data.

    The comparison can be based on text content, visual appearance, or both.
    Use the factory methods :meth:`from_text_comparison` and
    :meth:`from_visual_comparison` to create instances from the engine-level
    comparison functions.

    Attributes:
        left_document: The first document compared (original)
        right_document: The second document compared (modified)
        text_comparisons: List of PageTextComparison objects from text comparison
        visual_comparisons: List of PageVisualComparison objects from visual comparison
        changes: List of ContentChange objects describing detected differences
    """

    left_document: Document
    right_document: Document
    text_comparisons: list[PageTextComparison] = field(default_factory=list)
    visual_comparisons: list[PageVisualComparison] = field(default_factory=list)
    changes: list[ContentChange] = field(default_factory=list)

    def __post_init__(self):
        """Populate changes list if not explicitly provided."""
        if not self.changes:
            self._compute_changes()

    def _compute_changes(self) -> None:
        """Derive ContentChange entries from comparison results."""
        changes = []
        text_by_page: dict[int | None, PageTextComparison] = {}
        visual_by_page: dict[int | None, PageVisualComparison] = {}

        for comp in self.text_comparisons:
            text_by_page[comp.page_index] = comp
        for comp in self.visual_comparisons:
            visual_by_page[comp.page_index] = comp

        all_pages = set(text_by_page.keys()) | set(visual_by_page.keys())

        for page_idx in sorted(all_pages, key=lambda x: (x is None, x or 0)):
            text_comp = text_by_page.get(page_idx)
            visual_comp = visual_by_page.get(page_idx)

            if page_idx is None:
                continue

            other_page_idx = None
            page_number = page_idx + 1

            if text_comp:
                other_page_idx = text_comp.other_page_index
                page_number = page_idx + 1

            # Determine change type
            if text_comp:
                if other_page_idx is None and page_idx is not None:
                    changes.append(ContentChange(
                        change_type=ChangeType.DELETED_PAGE,
                        page_number=page_number,
                        description=f"Page {page_number} exists only in the first document",
                        page_index=page_idx,
                        other_page_index=None
                    ))
                elif not text_comp.matches:
                    # Find the other page index
                    for vc in self.visual_comparisons:
                        if vc.page_index == page_idx:
                            other_page_idx = vc.other_page_index
                            break
                    changes.append(ContentChange(
                        change_type=ChangeType.MODIFIED_PAGE,
                        page_number=page_number,
                        description=f"Page {page_number} content differs",
                        page_index=page_idx,
                        other_page_index=other_page_idx
                    ))

            elif visual_comp:
                if other_page_idx is None and page_idx is not None:
                    changes.append(ContentChange(
                        change_type=ChangeType.DELETED_PAGE,
                        page_number=page_number,
                        description=f"Page {page_number} exists only in the first document",
                        page_index=page_idx,
                        other_page_index=None
                    ))
                elif not visual_comp.matches:
                    for tc in self.text_comparisons:
                        if tc.page_index == page_idx:
                            other_page_idx = tc.other_page_index
                            break
                    changes.append(ContentChange(
                        change_type=ChangeType.MODIFIED_PAGE,
                        page_number=page_number,
                        description=f"Page {page_number} visual appearance differs",
                        page_index=page_idx,
                        other_page_index=other_page_idx
                    ))

        # Add added pages (pages in right but not in left)
        left_pages = {tc.page_index for tc in self.text_comparisons if tc.page_index is not None}
        right_pages = {tc.other_page_index for tc in self.text_comparisons if tc.other_page_index is not None}

        for page_idx in sorted(right_pages - left_pages, key=lambda x: x or 0):
            if page_idx is not None:
                changes.append(ContentChange(
                    change_type=ChangeType.ADDED_PAGE,
                    page_number=page_idx + 1,
                    description=f"Page {page_idx + 1} exists only in the second document",
                    page_index=None,
                    other_page_index=page_idx
                ))

        object.__setattr__(self, 'changes', changes)

    def get_changed_pages(self) -> list[int]:
        """Return a list of page numbers that have changes.

        Returns:
            List of one-based page numbers that differ between the documents.
        """
        return sorted({
            change.page_number
            for change in self.changes
            if change.page_number is not None
            and change.change_type in (ChangeType.MODIFIED_PAGE, ChangeType.ADDED_PAGE, ChangeType.DELETED_PAGE)
        })

    def get_additions(self) -> list[ContentChange]:
        """Return changes where content was added in the right document.

        Returns:
            List of ContentChange objects with change_type = 'added_page' or 'added'
        """
        return [
            change for change in self.changes
            if change.change_type in (ChangeType.ADDED_PAGE, ChangeType.ADDED)
        ]

    def get_deletions(self) -> list[ContentChange]:
        """Return changes where content was removed from the left document.

        Returns:
            List of ContentChange objects with change_type = 'deleted_page' or 'deleted'
        """
        return [
            change for change in self.changes
            if change.change_type in (ChangeType.DELETED_PAGE, ChangeType.DELETED)
        ]

    def get_modifications(self) -> list[ContentChange]:
        """Return changes where content was modified.

        Returns:
            List of ContentChange objects with change_type = 'modified_page' or 'modified'
        """
        return [
            change for change in self.changes
            if change.change_type in (ChangeType.MODIFIED_PAGE, ChangeType.MODIFIED)
        ]

    def has_changes(self) -> bool:
        """Check if any changes were detected.

        Returns:
            True if changes were found, False otherwise.
        """
        return len(self.changes) > 0

    def export_json(self) -> str:
        """Export comparison results as JSON.

        Returns:
            A JSON string with comparison metadata and change details.
        """
        import json

        def serialize_change(change: ContentChange) -> dict:
            return {
                "change_type": change.change_type,
                "page_number": change.page_number,
                "description": change.description,
                "page_index": change.page_index,
                "other_page_index": change.other_page_index,
            }

        def serialize_text_comp(comp: PageTextComparison) -> dict:
            return {
                "page_index": comp.page_index,
                "other_page_index": comp.other_page_index,
                "text": comp.text,
                "other_text": comp.other_text,
                "matches": comp.matches,
                "unified_diff": comp.unified_diff,
            }

        def serialize_visual_comp(comp: PageVisualComparison) -> dict:
            return {
                "page_index": comp.page_index,
                "other_page_index": comp.other_page_index,
                "size": comp.size,
                "other_size": comp.other_size,
                "changed_pixels": comp.changed_pixels,
                "total_pixels": comp.total_pixels,
                "bbox": comp.bbox,
                "matches": comp.matches,
                "difference_ratio": comp.difference_ratio,
                "ignored_pixels": comp.ignored_pixels,
            }

        result = {
            "left_document": {
                "page_count": len(self.left_document.pages) if self.left_document else 0,
            },
            "right_document": {
                "page_count": len(self.right_document.pages) if self.right_document else 0,
            },
            "summary": {
                "total_changes": len(self.changes),
                "added_pages": len(self.get_additions()),
                "deleted_pages": len(self.get_deletions()),
                "modified_pages": len(self.get_modifications()),
            },
            "changes": [serialize_change(c) for c in self.changes],
        }

        if self.text_comparisons:
            result["text_comparisons"] = [serialize_text_comp(c) for c in self.text_comparisons]
        if self.visual_comparisons:
            result["visual_comparisons"] = [serialize_visual_comp(c) for c in self.visual_comparisons]

        return json.dumps(result, indent=2, default=str)

    def export_html(self, path: str | None = None) -> str:
        """Export comparison results as an HTML report.

        Args:
            path: Optional file path to save the HTML report. If None, returns
                the HTML string without writing to a file.

        Returns:
            The HTML report as a string.
        """
        from datetime import datetime
        import html

        html_parts = [
            "<!DOCTYPE html>\n",
            "<html><head>\n",
            "<meta charset='utf-8'>\n",
            "<title>PDF Comparison Report</title>\n",
            "<style>\n",
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; background: #f5f5f5; }\n",
            ".header { background: #003366; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }\n",
            ".header h1 { margin: 0 0 10px 0; }\n",
            ".summary { display: flex; gap: 20px; margin-bottom: 20px; }\n",
            ".card { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex: 1; }\n",
            ".card h3 { margin: 0 0 10px 0; color: #333; border-bottom: 2px solid #003366; padding-bottom: 5px; }\n",
            ".count { font-size: 2em; color: #003366; font-weight: bold; }\n",
            ".changes-list { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }\n",
            ".changes-list h2 { margin-top: 0; color: #333; }\n",
            ".change { padding: 10px 15px; margin: 10px 0; border-radius: 6px; border-left: 4px solid; }\n",
            ".added { background: #e6f7e6; border-color: #28a745; }\n",
            ".deleted { background: #f8d7da; border-color: #dc3545; }\n",
            ".modified { background: #fff3cd; border-color: #ffc107; }\n",
            ".change-type { font-weight: bold; text-transform: uppercase; font-size: 0.85em; }\n",
            ".added .change-type { color: #155724; }\n",
            ".deleted .change-type { color: #721c24; }\n",
            ".modified .change-type { color: #856404; }\n",
            ".page-info { color: #666; font-size: 0.9em; margin-top: 5px; }\n",
            ".diff { background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-family: 'Consolas', 'Monaco', monospace; font-size: 0.85em; }\n",
            ".diff add { background: #d4edda; }\n",
            ".diff del { background: #f8d7da; text-decoration: line-through; }\n",
            ".diff mod { background: #fff3cd; }\n",
            "</style>\n",
            "</head>\n",
            "<body>\n",
            "<div class='header'>\n",
            "<h1>PDF Comparison Report</h1>\n",
            "<p>Generated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "</p>\n",
            "</div>\n",
            "<div class='summary'>\n",
            "<div class='card'>\n",
            "<h3>Documents Compared</h3>\n",
            f"<p><strong>Left:</strong> {len(self.left_document.pages) if self.left_document else 0} pages</p>\n",
            f"<p><strong>Right:</strong> {len(self.right_document.pages) if self.right_document else 0} pages</p>\n",
            "</div>\n",
            "<div class='card'>\n",
            "<h3>Summary</h3>\n",
            f"<p>Total Changes: <span class='count'>{len(self.changes)}</span></p>\n",
            "</div>\n",
            "<div class='card'>\n",
            "<h3>By Type</h3>\n",
            f"<p><span class='count' style='color:#28a745'>{len(self.get_additions())}</span> Added</p>\n",
            f"<p><span class='count' style='color:#dc3545'>{len(self.get_deletions())}</span> Deleted</p>\n",
            f"<p><span class='count' style='color:#ffc107'>{len(self.get_modifications())}</span> Modified</p>\n",
            "</div>\n",
            "</div>\n",
            "<div class='changes-list'>\n",
            "<h2>Change Details</h2>\n",
        ]

        if not self.changes:
            html_parts.append("<p>No changes detected. Documents are identical.</p>\n")
        else:
            for change in self.changes:
                change_class = "added" if "added" in change.change_type else "deleted" if "deleted" in change.change_type else "modified"
                html_parts.append(f"<div class='change {change_class}'>\n")
                html_parts.append(f"<span class='change-type'>{change.change_type}</span>\n")
                html_parts.append(f"<p>{html.escape(change.description)}</p>\n")
                if change.page_number:
                    html_parts.append(f"<p class='page-info'>Page {change.page_number}</p>\n")
                html_parts.append("</div>\n")

        if self.text_comparisons:
            html_parts.append("<div class='changes-list'>\n")
            html_parts.append("<h2>Text Diff Summary</h2>\n")
            for comp in self.text_comparisons:
                if not comp.matches and comp.unified_diff:
                    page_num = (comp.page_index or 0) + 1
                    other_num = (comp.other_page_index or 0) + 1
                    diff_preview = comp.unified_diff[:200] + "..." if len(comp.unified_diff) > 200 else comp.unified_diff
                    diff_escaped = html.escape(diff_preview).replace("\n", "<br>")
                    html_parts.append(f"<div class='change modified'>\n")
                    html_parts.append(f"<span class='change-type'>text</span>\n")
                    html_parts.append(f"<p>Page {page_num} vs Page {other_num}</p>\n")
                    html_parts.append(f"<div class='diff'><pre>{diff_escaped}</pre></div>\n")
                    html_parts.append("</div>\n")

        html_parts.extend([
            "</div>\n",
            "<div class='footer'>\n",
            "<p>Generated by Aspose.PDF FOSS for Python</p>\n",
            "</div>\n",
            "</body>\n",
            "</html>",
        ])

        result = "".join(html_parts)

        if path:
            from pathlib import Path
            Path(path).write_text(result, encoding="utf-8")

        return result

    @classmethod
    def from_text_comparison(
        cls,
        left: Document,
        right: Document,
        results: list[PageTextComparison],
    ) -> DocumentComparison:
        """Create a DocumentComparison from text comparison results.

        Args:
            left: The first (left) document.
            right: The second (right) document.
            results: List of PageTextComparison objects from compare_text().

        Returns:
            A new DocumentComparison instance.
        """
        return cls(
            left_document=left,
            right_document=right,
            text_comparisons=results,
        )

    @classmethod
    def from_visual_comparison(
        cls,
        left: Document,
        right: Document,
        results: list[PageVisualComparison],
    ) -> DocumentComparison:
        """Create a DocumentComparison from visual comparison results.

        Args:
            left: The first (left) document.
            right: The second (right) document.
            results: List of PageVisualComparison objects from compare_visual().

        Returns:
            A new DocumentComparison instance.
        """
        return cls(
            left_document=left,
            right_document=right,
            visual_comparisons=results,
        )

    @classmethod
    def compare(
        cls,
        left: Document,
        right: Document,
        *,
        compare_text: bool = True,
        compare_visual: bool = False,
        **kwargs,
    ) -> DocumentComparison:
        """Compare two documents using text and/or visual comparison.

        This is a convenience method that wraps compare_text() and/or
        compare_visual() and returns a DocumentComparison instance.

        Args:
            left: The first document.
            right: The second document.
            compare_text: If True, perform text-based comparison.
            compare_visual: If True, perform visual (raster) comparison.
            **kwargs: Additional arguments passed to the comparison methods
                (pages, case_sensitive, dpi, tolerance, etc.)

        Returns:
            A DocumentComparison instance with the results.
        """
        text_results = []
        visual_results = []

        if compare_text:
            text_results = left.compare_text(right, **kwargs)

        if compare_visual:
            visual_results = left.compare_visual(right, **kwargs)

        comparison = cls(
            left_document=left,
            right_document=right,
            text_comparisons=text_results,
            visual_comparisons=visual_results,
        )

        return comparison