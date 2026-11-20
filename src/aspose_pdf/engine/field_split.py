"""Find page boundaries from form field values before importing independent parts."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

if TYPE_CHECKING:
    from .simple_pdf import SimplePdf


def _get_field_value_for_page(pdf: SimplePdf, field_name: str, page_index: int) -> str | None:
    """Get the field's value as it appears on the given page.
    
    Returns the widget's value if the field has a widget on this page, otherwise None.
    Each widget can have its own value, which takes precedence over the field's value.

    Args:
        page_index: 1-based page number (1, 2, 3, ...)

    Returns:
        The field's value on this page, or None if no widget exists on this page.
    """
    from .form_fields import field_attribute, field_widgets, widget_dictionaries
    
    if pdf._cos_doc is None:
        return None
    
    # Convert 1-based page_index to 0-based for comparison with field_widgets
    page_index_0based = page_index - 1

    # Find the field by name
    for candidate, entry in pdf._iter_form_fields():
        if candidate == field_name:
            # Get all widgets for this field
            widgets = widget_dictionaries(pdf, entry)

            # Find the widget on the specified page
            for widget in widgets:
                widget_page_idx = field_widgets(pdf, widget)
                for idx, _ in widget_page_idx:
                    # field_widgets returns 0-based page indices
                    if idx == page_index_0based:
                        # Get the widget's own value first
                        value = field_attribute(pdf, widget, "V")
                        if value is None:
                            # Fall back to field value if widget doesn't have its own
                            value = field_attribute(pdf, entry, "V")
                        ft = field_attribute(pdf, entry, "FT")
                        kind = ft.name.lstrip("/") if hasattr(ft, "name") else "text"
                        from .form_fields import form_value
                        return form_value(pdf._resolve, pdf._load_budget, value, kind)
    
    return None


def _pattern(
    field_name: str,
    pattern: str | None,
    regex: bool,
    case_sensitive: bool,
) -> tuple[str, str | None]:
    """Validate options and compile the matcher before reading any page.

    Returns the field name and compiled pattern for matching field values.
    """
    for name, value in (
        ("regex", regex),
        ("case_sensitive", case_sensitive),
    ):
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")

    if pattern is None:
        # Default: match exact field value
        return field_name, None

    if isinstance(pattern, str):
        source = pattern
    else:
        raise TypeError("pattern must be a string or None")

    if not source:
        raise ValueError("pattern must not be empty")

    if not regex:
        import re
        source = re.escape(source)
    if not case_sensitive:
        import re
        source = source.lower()
    return field_name, source


def _check_result(pdf: SimplePdf, *, context: str = "field split") -> None:
    """Apply the source policy to the newly allocated graph and decoded content."""
    from .cos import PdfStream
    
    budget, objects = pdf._load_budget, pdf._cos_doc.objects
    budget.check_pages(len(pdf.pages))
    budget.check_objects(len(objects))
    budget.check_object_id(max(objects, default=0))
    
    for obj in objects.values():
        if isinstance(obj, PdfStream):
            budget.check(len(obj.content), "max_object_bytes", f"{context} stream")
    
    total = 0
    for content in pdf.page_contents:
        budget.check(len(content), "max_content_stream_bytes", f"{context} content")
        budget.check(len(content), "max_decoded_stream_bytes", f"{context} content")
        total += len(content)
    budget.check(total, "max_total_decoded_bytes", f"{context} content")


def _refresh_targets(pdf: SimplePdf) -> None:
    """Expose typed destinations from the imported references before a save/reload."""
    from .simple_pdf import (
        _UNRESOLVED_DESTINATION,
        action_from_cos,
        destination_from_cos,
    )
    
    stack = list(pdf._outlines_data)
    while stack:
        item = stack.pop()
        loaded = item.get("loaded_target")
        if loaded is not None:
            key, raw = loaded
            target = (
                action_from_cos(pdf._resolve(raw), pdf._resolve, pdf._page_index_of)
                if key == "A"
                else destination_from_cos(raw, pdf._resolve, pdf._page_index_of)
            )
            item["target"] = None if target is _UNRESOLVED_DESTINATION else target
        stack.extend(item.get("children", ()))


def _snapshot(pdf: SimplePdf, start_page: int, end_page: int) -> SimplePdf:
    """Create a new PDF with pages from start_page to end_page (1-indexed, inclusive).

    Note: This implementation does not preserve outlines from the source document.
    The returned PDF has an empty outlines structure.
    """
    # Convert to 0-indexed list of page indices
    pages = list(range(start_page - 1, end_page))
    return pdf.extract_pages(pages)


def split_by_field_value(
    pdf: SimplePdf,
    field_name: str,
    *,
    pattern: str | None = None,
    regex: bool = False,
    case_sensitive: bool = True,
    include_preamble: bool = True,
) -> Iterator[SimplePdf]:
    """Split the PDF into parts using the given field's value changes.
    
    Yields new SimplePdf instances for each part. Each part is an independent
    PDF with its own page and object counts, resource limits, and outlines.
    The source document and its files are not modified.

    When pattern is provided, boundaries are created at pages where the value
    does NOT match the pattern (i.e., where the value changes or differs from
    the expected pattern). When pattern is None, boundaries are created at
    pages where any value change is detected.
    """
    if pdf._cos_doc is None:
        return
    
    # Validate and compile pattern before reading any page
    field_name, compiled_pattern = _pattern(field_name, pattern, regex, case_sensitive)
    
    # Check that field exists
    field_found = False
    for candidate, _ in pdf._iter_form_fields():
        if candidate == field_name:
            field_found = True
            break
    
    if not field_found:
        raise PdfValidationException(f"Field '{field_name}' not found in document")
    
    # Build page -> value mapping
    page_count = len(pdf.pages)
    if page_count == 0:
        return
    
    page_values: list[str | None] = []
    for page_index in range(1, page_count + 1):
        value = _get_field_value_for_page(pdf, field_name, page_index)
        page_values.append(value)
    
    # Determine boundaries based on value changes or pattern matches
    boundaries: list[int] = []  # page indices where a new part starts (1-based, exclusive)
    
    for i, (prev_val, curr_val) in enumerate(pairwise(page_values)):
        page_num = i + 2  # 1-based page number
        
        if compiled_pattern is None:
            # Match any value change
            if prev_val != curr_val:
                boundaries.append(page_num)
        else:
            # Match specific pattern
            import re
            curr_text = str(curr_val) if curr_val is not None else ""
            prev_text = str(prev_val) if prev_val is not None else ""
            
            if not case_sensitive:
                curr_text = curr_text.lower()
                prev_text = prev_text.lower()
            
            curr_matches = re.search(compiled_pattern, curr_text) is not None
            
            # When matching pattern, do NOT create boundary (values are the same)
            # Create boundary only when value does NOT match pattern
            if not curr_matches:
                boundaries.append(page_num)
    
    # Initialize start_page before preamble handling
    start_page = 1

    # Add leading preamble handling
    if boundaries:
        first_boundary = boundaries[0]
        if not include_preamble and first_boundary > 1:
            # Skip leading pages before first boundary
            boundaries = boundaries[1:]
            start_page = first_boundary
    elif not include_preamble:
        # No boundaries and include_preamble=False means no parts to return
        return
    
    # Generate parts
    for boundary in boundaries:
        part = _snapshot(pdf, start_page, boundary - 1)
        try:
            _check_result(part)
            _refresh_targets(part)
            yield part
        except BaseException:
            part.dispose()
            raise
        start_page = boundary
    
    # Final part from last boundary to end (or from page 1 if no boundaries)
    if start_page <= page_count:
        part = _snapshot(pdf, start_page, page_count)
        try:
            _check_result(part)
            _refresh_targets(part)
            yield part
        except BaseException:
            part.dispose()
            raise
