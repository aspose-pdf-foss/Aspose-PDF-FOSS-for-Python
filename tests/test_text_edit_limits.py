"""Resource-limit coverage for auxiliary text editing content parsers."""

from __future__ import annotations

from dataclasses import fields

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.text_edit import (
    _lex,
    redact_text_in_content,
    replace_text_in_content,
)
from aspose_pdf.engine.text_locate import locate_matches
from aspose_pdf.load_limits import _LoadBudget


def _limits(**overrides: int | None) -> PdfLoadLimits:
    values = {item.name: None for item in fields(PdfLoadLimits)}
    values.update(overrides)
    return PdfLoadLimits(**values)


def test_lexer_checks_content_bytes_before_tokenizing() -> None:
    with pytest.raises(
        PdfResourceLimitException,
        match="max_content_stream_bytes",
    ):
        _lex(b"BT ET", limits=_limits(max_content_stream_bytes=4))


def test_lexer_checks_limit_before_each_token_append() -> None:
    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        _lex(b"BT ET", limits=_limits(max_content_tokens=1))


def test_lexer_bounds_token_list_materialization() -> None:
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        _lex(b"BT ET", limits=_limits(max_container_items=1))


def test_lexer_makes_progress_on_unmatched_delimiters() -> None:
    assert _lex(b"){ }") == []


@pytest.mark.parametrize(
    "content",
    [
        b"((((x))))",
        b"[[[[]]]]",
        b"<< << << << >> >> >> >>",
        b"[ << ((x)) >> ]",
    ],
    ids=["literal", "array", "dictionary", "mixed"],
)
def test_lexer_bounds_nested_content_structures(content: bytes) -> None:
    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        _lex(content, limits=_limits(max_nesting_depth=3))


def test_replace_does_not_swallow_graphics_stack_limit() -> None:
    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        replace_text_in_content(
            b"q q BT (x) Tj ET Q Q",
            "x",
            "y",
            limits=_limits(max_nesting_depth=1),
        )


@pytest.mark.parametrize(
    "operation",
    [
        lambda content, limits: replace_text_in_content(
            content,
            "x",
            "y",
            limits=limits,
        ),
        lambda content, limits: redact_text_in_content(
            content,
            "x",
            limits=limits,
        ),
        lambda content, limits: locate_matches(
            content,
            "x",
            lambda _name: None,
            limits=limits,
        ),
    ],
    ids=["replace", "redact", "locate"],
)
def test_entry_points_propagate_token_limit(operation) -> None:
    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        operation(b"BT (x) Tj ET", _limits(max_content_tokens=2))


def test_entry_points_accept_shared_load_budget() -> None:
    limits = _limits(max_content_tokens=8, max_container_items=8)
    budget = _LoadBudget(limits)

    updated, count = replace_text_in_content(
        b"BT (x) Tj ET",
        "x",
        "y",
        limits=limits,
        budget=budget,
    )

    assert count == 1
    assert updated == b"BT (y) Tj ET"


def test_entry_points_reject_mismatched_limits_and_budget() -> None:
    budget = _LoadBudget(_limits(max_content_tokens=8))

    with pytest.raises(ValueError, match="limits must match budget.limits"):
        redact_text_in_content(
            b"BT (x) Tj ET",
            "x",
            limits=_limits(max_content_tokens=7),
            budget=budget,
        )
