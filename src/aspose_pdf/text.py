"""Text extraction utilities.

This module provides TextFragmentAbsorber, TextFragmentCollection and related
classes for searching and managing text fragments in PDF documents. Font
classes for searching and managing text fragments in PDF documents.
PictureClausesFormatter for number formatting is also included.
Paragraph alignment functionality is also included.

Supports three search modes:
- No phrase/regex: collect all text fragments line by line.
- phrase search: find all occurrences of an exact substring (optionally
  case-insensitive via TextSearchOptions.case_sensitive=False).
- regex search: find all regex matches; results available via both
  text_fragments and regex_results (set is_regular_expression=True on
  the TextSearchOptions passed to the constructor, or pass a regex=...
  keyword argument directly).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional

    
__all__ = [
    "TextFormattingMode",
    "TextExtractionOptions",
    "TextFragment",
    "TextSearchOptions",
    "RegexResult",
    "TextFragmentAbsorber",
    "TextAbsorber",
]


# ---------------------------------------------------------------------------
# TextExtractionOptions
# ---------------------------------------------------------------------------


class TextFormattingMode:
    """Text formatting mode for text extraction."""
    
    Flatten = "Flatten"
    """Flatten text into a single stream without preserving layout."""
    
    Pure = "Pure"
    """Preserve text layout and formatting."""


    
class TextExtractionOptions:
    """Options for text extraction from PDF pages.
    
    Attributes:
        text_formatting_mode: The formatting mode to use for extraction.
    """
    
    def __init__(self, text_formatting_mode: str = TextFormattingMode.Flatten):
        self.text_formatting_mode = text_formatting_mode


# Font repository support


# ---------------------------------------------------------------------------
# TextFragment
# ---------------------------------------------------------------------------


@dataclass
class TextFragment:
    """A text fragment found inside a PDF page.

    Attributes
    ----------
    page_index : int
        Zero-based index of the page where the fragment was found.
    text : str
        The matched (or extracted) text.
    start : int
        Character offset inside the page text where the match starts.
    end : int
        Character offset where the match ends (exclusive).
    """

    page_index: int
    text: str
    start: int = 0
    end: int = field(default=-1)

    def __post_init__(self) -> None:
        if self.end == -1:
            self.end = self.start + len(self.text)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TextFragment):
            return NotImplemented
        return (
            self.page_index == other.page_index
            and self.text == other.text
            and self.start == other.start
            and self.end == other.end
        )

    def __repr__(self) -> str:
        return (
            f"TextFragment(page={self.page_index}, text={self.text!r}, "
            f"start={self.start}, end={self.end})"
        )


# ---------------------------------------------------------------------------
# TextSearchOptions
# ---------------------------------------------------------------------------


class TextSearchOptions:
    """Options controlling how text search is performed.

    Parameters
    ----------
    is_regular_expression : bool
        When ``True`` the phrase is treated as a regular expression.
    case_sensitive : bool
        When ``False`` the search ignores letter case.  Defaults to ``True``.
    """

    def __init__(
        self,
        is_regular_expression: bool = False,
        *,
        case_sensitive: bool = True,
    ) -> None:
        self.is_regular_expression: bool = is_regular_expression
        self.case_sensitive: bool = case_sensitive

    def __repr__(self) -> str:
        return (
            f"TextSearchOptions(is_regular_expression={self.is_regular_expression}, "
            f"case_sensitive={self.case_sensitive})"
        )


# ---------------------------------------------------------------------------
# RegexResult
# ---------------------------------------------------------------------------


class RegexResult:
    """Wraps a single regular-expression match found on a PDF page.

    Attributes
    ----------
    page_index : int
        Zero-based page index.
    text : str
        Full matched string (``match.group(0)``).
    start : int
        Start offset inside the page text.
    end : int
        End offset inside the page text (exclusive).
    groups : tuple
        Captured groups from the regex (``match.groups()``).
    match : re.Match
        The underlying :class:`re.Match` object for full access.
    """

    def __init__(self, match: re.Match, page_index: int) -> None:
        self.page_index: int = page_index
        self.match: re.Match = match
        self.text: str = match.group(0)
        self.start: int = match.start()
        self.end: int = match.end()
        self.groups: tuple = match.groups()

    def __repr__(self) -> str:
        return (
            f"RegexResult(page={self.page_index}, text={self.text!r}, "
            f"start={self.start}, end={self.end})"
        )


# ---------------------------------------------------------------------------
# TextFragmentAbsorber
# ---------------------------------------------------------------------------


class TextFragmentAbsorber:
    """Absorbs text fragments from a PDF page or document.

    Usage
    -----
    *Collect all text (no filter):*

    .. code-block:: python

        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        for frag in absorber.text_fragments:
            print(frag.text)

    *Phrase search:*

    .. code-block:: python

        absorber = TextFragmentAbsorber("Hello")
        absorber.visit(page)

    *Case-insensitive phrase search:*

    .. code-block:: python

        opts = TextSearchOptions(case_sensitive=False)
        absorber = TextFragmentAbsorber("hello", text_search_options=opts)

    *Regex search:*

    .. code-block:: python

        opts = TextSearchOptions(is_regular_expression=True)
        absorber = TextFragmentAbsorber(r"\\d{4}-\\d{2}-\\d{2}", text_search_options=opts)
        absorber.visit(page)
        for result in absorber.regex_results:
            print(result.text, result.start)

    Parameters
    ----------
    phrase : str, optional
        Text phrase to search for.  When *None* all text is collected.
    text_search_options : TextSearchOptions, optional
        Search options.  If *is_regular_expression* is True the phrase is
        compiled as a regex pattern.
    """

    def __init__(
        self,
        phrase: Optional[str] = None,
        *,
        text_search_options: Optional[TextSearchOptions] = None,
        text_replace_options: Optional[Any] = None,
    ) -> None:
        self.phrase: Optional[str] = phrase
        self.text_search_options: TextSearchOptions = (
            text_search_options
            if text_search_options is not None
            else TextSearchOptions()
        )
        self.text_replace_options: Optional[Any] = text_replace_options

        self._text_fragments: List[TextFragment] = []
        self._regex_results: List[RegexResult] = []
        self._errors: List[Any] = []
        self.has_errors: bool = False

        # Pre-compile regex pattern once if needed.
        self._compiled_regex: Optional[re.Pattern] = None
        if phrase is not None and self.text_search_options.is_regular_expression:
            flags = 0 if self.text_search_options.case_sensitive else re.IGNORECASE
            self._compiled_regex = re.compile(phrase, flags)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def text_fragments(self) -> List[TextFragment]:
        """List of :class:`TextFragment` objects found by the last visit."""
        return self._text_fragments

    @property
    def regex_results(self) -> List[RegexResult]:
        """List of :class:`RegexResult` objects (populated on regex search)."""
        return self._regex_results

    @property
    def errors(self) -> List[Any]:
        """Errors collected during the last visit, if any."""
        return self._errors

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def visit(self, page_or_doc: Any) -> None:
        """Process *page_or_doc* and collect matching text fragments.

        The method supports objects that expose ``extract_text()`` (pages,
        :class:`~aspose_pdf.engine.simple_pdf.SimplePdf`) as well as
        document-like objects with an iterable ``pages`` attribute.
        """
        if hasattr(page_or_doc, "extract_text"):
            text = page_or_doc.extract_text()
            if isinstance(text, list):
                for page_idx, item in enumerate(text):
                    self._process_page_text(str(item), page_idx)
            elif isinstance(text, str):
                self._process_page_text(text, 0)
        elif hasattr(page_or_doc, "pages"):
            pages = page_or_doc.pages
            try:
                page_iter = iter(pages)
            except TypeError:
                return
            for page_idx, page in enumerate(page_iter):
                if hasattr(page, "extract_text"):
                    raw = page.extract_text()
                    if isinstance(raw, str):
                        self._process_page_text(raw, page_idx)

    def reset(self) -> None:
        """Clear all collected fragments, regex results and errors."""
        self._text_fragments.clear()
        self._regex_results.clear()
        self._errors.clear()
        self.has_errors = False

    def remove_all_text(self) -> None:
        """Remove all collected text fragments (alias for reset without clearing errors)."""
        self._text_fragments.clear()
        self._regex_results.clear()

    def apply_for_all_fragments(
        self, action: Callable[[TextFragment], Any]
    ) -> List[Any]:
        """Apply *action* to every collected fragment and return the results.

        Parameters
        ----------
        action : Callable
            Called once per fragment; the return value is collected.

        Returns
        -------
        list
            Results of each *action* call in order.
        """
        return [action(frag) for frag in self._text_fragments]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_page_text(self, text: str, page_index: int) -> None:
        """Apply phrase/regex/no-filter search to *text* from *page_index*."""
        if self._compiled_regex is not None:
            # Regex search mode
            for match in self._compiled_regex.finditer(text):
                frag = TextFragment(
                    page_index=page_index,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
                self._text_fragments.append(frag)
                self._regex_results.append(RegexResult(match, page_index))

        elif self.phrase is not None:
            # Exact phrase search mode
            if self.text_search_options.case_sensitive:
                search_text = text
                search_phrase = self.phrase
            else:
                search_text = text.lower()
                search_phrase = self.phrase.lower()

            pos = 0
            while True:
                idx = search_text.find(search_phrase, pos)
                if idx == -1:
                    break
                matched_text = text[idx : idx + len(self.phrase)]
                self._text_fragments.append(
                    TextFragment(
                        page_index=page_index,
                        text=matched_text,
                        start=idx,
                        end=idx + len(matched_text),
                    )
                )
                pos = idx + 1  # allow overlapping matches

        else:
            # No-filter mode: split into non-empty lines
            offset = 0
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped:
                    # Find where the stripped text starts in the original line.
                    line_start = text.find(stripped, offset)
                    if line_start == -1:
                        line_start = offset
                    self._text_fragments.append(
                        TextFragment(
                            page_index=page_index,
                            text=stripped,
                            start=line_start,
                            end=line_start + len(stripped),
                        )
                    )
                offset += len(line) + 1  # +1 for '\n'

    def __repr__(self) -> str:
        return (
            f"TextFragmentAbsorber(phrase={self.phrase!r}, "
            f"fragments={len(self._text_fragments)})"
        )


# ---------------------------------------------------------------------------
# TextAbsorber (legacy alias for TextFragmentAbsorber)
# ---------------------------------------------------------------------------


class TextAbsorber:
    """Absorbs text from PDF pages (legacy class, alias for TextFragmentAbsorber).

    This class is provided for backward compatibility with older code that
    used TextAbsorber. It's now an alias for TextFragmentAbsorber.
    """

    def __init__(
        self,
        phrase: Optional[str] = None,
        *,
        text_search_options: Optional[TextSearchOptions] = None,
        text_replace_options: Optional[Any] = None,
    ) -> None:
        """Initialize text absorber.

        Parameters
        ----------
        phrase : str, optional
            Text phrase to search for. When None all text is collected.
        text_search_options : TextSearchOptions, optional
            Search options.
        text_replace_options : Any, optional
            Text replace options (not used in this implementation).
        """
        # Use TextFragmentAbsorber internally for compatibility
        self._absorber = TextFragmentAbsorber(
            phrase=phrase,
            text_search_options=text_search_options,
            text_replace_options=text_replace_options,
        )

    @property
    def text(self) -> str:
        """Get all collected text as a single string."""
        fragments = self._absorber.text_fragments
        if not fragments:
            return ""
        return "\n".join(f.text for f in fragments)

    @property
    def text_fragments(self) -> List[TextFragment]:
        """List of text fragments found."""
        return self._absorber.text_fragments

    def visit(self, page_or_doc: Any) -> None:
        """Process page or document and collect text.

        Parameters
        ----------
        page_or_doc
            A page object or document to extract text from.
        """
        self._absorber.visit(page_or_doc)

    def reset(self) -> None:
        """Clear all collected text fragments."""
        self._absorber.reset()
        
    def remove_all_text(self) -> None:
        """Remove all collected text fragments."""
        self._absorber.remove_all_text()

    def apply_for_all_fragments(
        self, action: Callable[[TextFragment], Any]
    ) -> List[Any]:
        """Apply action to every collected fragment."""
        return self._absorber.apply_for_all_fragments(action)

    def __repr__(self) -> str:
        return (
            f"TextAbsorber(phrase={self._absorber.phrase!r}, "
            f"fragments={len(self._absorber.text_fragments)})"
        )


# ---------------------------------------------------------------------------
# TextFragmentCollection
# ---------------------------------------------------------------------------


class TextFragmentCollection:
    """A mutable ordered collection of :class:`TextFragment` objects."""

    def __init__(self) -> None:
        self._fragments: List[TextFragment] = []

    def add(self, fragment: Any) -> None:
        """Add *fragment* to the collection; ``None`` is silently ignored."""
        if fragment is None:
            return
        self._fragments.append(fragment)

    def remove(self, fragment: Any) -> bool:
        """Remove *fragment* from the collection.

        Returns
        -------
        bool
            ``True`` if a matching fragment was found and removed, else ``False``.
            ``None`` is never considered present.
        """
        if fragment is None:
            return False
        for i, existing in enumerate(self._fragments):
            if fragment == existing:
                del self._fragments[i]
                return True
        return False

    def clear(self) -> None:
        """Remove all fragments from the collection."""
        self._fragments.clear()

    def contains(self, fragment: Any) -> bool:
        """Return ``True`` if *fragment* is present in the collection."""
        if fragment is None:
            return False
        return any(fragment == existing for existing in self._fragments)

    def item(self, index: int) -> Any:
        """Return the fragment at *index* (0-based).

        Raises
        ------
        IndexError
            If *index* is out of range.
        """
        if index < 0 or index >= len(self._fragments):
            raise IndexError("index out of range")
        return self._fragments[index]

    def get_enumerator(self) -> Iterator[Any]:
        """Return an iterator over the collection."""
        return iter(self._fragments)

    def __len__(self) -> int:
        return len(self._fragments)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._fragments)

    def __getitem__(self, index: int) -> Any:
        return self._fragments[index]

    def __repr__(self) -> str:
        return f"TextFragmentCollection(count={len(self._fragments)})"
