"""Remove alternate representations attached to redacted text operands."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import PdfDictionary, PdfDocument, PdfName, PdfNumber
from .pdf_parser_cos import _Tokenizer
from .pdf_writer_cos import PdfCosWriter

if TYPE_CHECKING:
    from .text_edit import _Token

_ALTERNATE_TEXT = (PdfName("ActualText"), PdfName("Alt"), PdfName("E"))


def clear_alternate_text(dictionary: PdfDictionary) -> None:
    """Drop replacement text, alternate descriptions and abbreviation expansions."""
    for key in _ALTERNATE_TEXT:
        dictionary.mapping.pop(key, None)


@dataclass
class RedactionContext:
    """Metadata associated with the operands changed in one content stream."""

    property_for_name: Callable[[str], Any] | None = None
    mcids: set[int] = field(default_factory=set)
    retired_properties: set[str] = field(default_factory=set)
    sanitized_properties: set[str] = field(default_factory=set)
    invoked_xobjects: set[str] = field(default_factory=set)

    def metadata_edits(
        self,
        content: bytes,
        tokens: list[_Token],
        replacements: list[tuple[int, int, bytes]],
        budget: _LoadBudget,
    ) -> list[tuple[int, int, bytes]]:
        """Inline sanitized property lists for marked scopes containing an edit.

        The whole alternate string is removed: its character positions need
        not correspond to the visible glyphs. Named properties are copied into
        the affected scope so other uses retain their original descriptions.
        """
        changed = {start for start, _end, _value in replacements}
        # Scopes contain a property operand's byte range, or None for BMC.
        stack: list[tuple[int, int] | None] = []
        affected: set[tuple[int, int]] = set()
        named_uses: Counter[str] = Counter()
        named_scopes: dict[tuple[int, int], str] = {}
        operands: list[_Token] = []
        depth = 0
        for token in tokens:
            if token.start in changed:
                affected.update(scope for scope in stack if scope is not None)
            if token.kind == "array_start" or (token.kind == "word" and token.value == "<<"):
                depth += 1
            elif token.kind == "array_end" or (token.kind == "word" and token.value == ">>"):
                depth -= 1
                if depth < 0:
                    raise PdfValidationException("Unbalanced redaction property list")
                operands.append(token)
                continue
            is_operator = token.kind == "word" and depth == 0
            if is_operator:
                try:
                    float(token.value)
                    is_operator = False
                except ValueError:
                    is_operator = token.value not in ("true", "false", "null")
            if not is_operator:
                operands.append(token)
                continue
            op = token.value
            if op in ("BDC", "DP"):
                if len(operands) < 2 or operands[0].kind != "name":
                    raise PdfValidationException("Invalid marked content during redaction")
                span = (operands[1].start, operands[-1].end)
                if operands[1].kind == "name":
                    name = self._read(content, span, budget)
                    if not isinstance(name, PdfName):
                        raise PdfValidationException("Invalid redaction property name")
                    key = name.name.lstrip("/")
                    named_uses[key] += 1
                    named_scopes[span] = key
                if op == "BDC":
                    stack.append(span)
            elif op == "BMC":
                stack.append(None)
            elif op == "EMC":
                if not stack:
                    raise PdfValidationException("Unbalanced marked content during redaction")
                stack.pop()
            elif op == "Do" and operands and operands[-1].kind == "name":
                name = self._read(content, (operands[-1].start, operands[-1].end), budget)
                self.invoked_xobjects.add(name.name.lstrip("/"))
            budget.check(len(stack), "max_nesting_depth", "redaction marked content depth")
            budget.check(len(affected), "max_container_items", "redaction marked scopes")
            operands.clear()
        if stack or depth:
            raise PdfValidationException("Unclosed marked content during redaction")

        edits = []
        writer = PdfCosWriter(PdfDocument())
        for span in sorted(affected):
            name = named_scopes.get(span)
            if name is None:
                properties = self._read(content, span, budget)
            else:
                properties = self.property_for_name(name) if self.property_for_name else None
            if not isinstance(properties, PdfDictionary):
                raise PdfValidationException(
                    "Cannot resolve marked-content properties for safe redaction"
                )
            mcid = properties.mapping.get(PdfName("MCID"))
            if isinstance(mcid, PdfNumber) and int(mcid.value) >= 0:
                self.mcids.add(int(mcid.value))
            if not any(key in properties.mapping for key in _ALTERNATE_TEXT):
                continue
            cleaned = PdfDictionary(dict(properties.mapping))
            clear_alternate_text(cleaned)
            edits.append((*span, writer.serialize_object(cleaned).encode("ascii")))
            if name is not None:
                self.sanitized_properties.add(name)
                named_uses[name] -= 1
                if not named_uses[name]:
                    self.retired_properties.add(name)
        return edits

    @staticmethod
    def _read(content: bytes, span: tuple[int, int], budget: _LoadBudget) -> Any:
        start, end = span
        return _Tokenizer(content[start:end].decode("latin-1"), budget.limits).read()
