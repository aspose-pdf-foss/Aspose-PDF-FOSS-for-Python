"""AUDIT issue #20: ContentStreamParser must not materialize the full token list.

``extract_text`` and ``best_effort_extract_text`` consume ``_tokenize()`` lazily so huge
content streams do not allocate one Python object per token up front.
"""

from unittest.mock import patch

import pytest

from aspose_pdf.engine.content_stream_parser import ContentStreamParser


def test_extract_text_large_stream_only_graphics_operators():
    """Many operator tokens should complete without building a list of all tokens."""
    # ~200k tokens (q, Q); materializing as list would hold hundreds of thousands of str objects.
    chunk = b"q\nQ\n"
    data = chunk * 100_000
    parser = ContentStreamParser(data, {})
    assert parser.extract_text() == ""


def test_extract_text_text_surrounded_by_many_operators():
    """Regression: streaming iteration must still bind Tj operands after deep stacks."""
    noise = b"q\nQ\n" * 5_000
    stream = noise + b"BT /F1 12 Tf (Hello) Tj ET\n" + noise
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "Hello"


def test_best_effort_many_string_tokens():
    """best_effort walks the tokenizer once without list(self._tokenize())."""
    part = b"(z) "
    data = part * 25_000
    parser = ContentStreamParser(data, {})
    assert parser.best_effort_extract_text() == "z" * 25_000


def test_extract_text_never_wraps_tokenize_in_list():
    """Guard: ``list(self._tokenize())`` in extract_text causes a huge temporary list."""
    import builtins

    real_list = builtins.list

    def list_except_tokenize_materialization(iterable):
        if inspect_is_tokenize_generator(iterable):
            pytest.fail(
                "extract_text must not call list() on the _tokenize() generator "
                "(AUDIT issue #20)."
            )
        return real_list(iterable)

    builtins.list = list_except_tokenize_materialization
    try:
        noise = b"w\n" * 500  # arity-1 path op; harmless for extract_text
        stream = b"BT /F1 12 Tf (Hi) Tj ET\n" + noise
        parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
        assert parser.extract_text() == "Hi"
    finally:
        builtins.list = real_list


def inspect_is_tokenize_generator(iterable) -> bool:
    import inspect

    if not inspect.isgenerator(iterable):
        return False
    gen = iterable
    return (
        getattr(gen, "gi_code", None) is not None and gen.gi_code.co_name == "_tokenize"
    )


def test_best_effort_never_wraps_tokenize_in_list():
    import builtins

    real_list = builtins.list

    def list_except_tokenize_materialization(iterable):
        if inspect_is_tokenize_generator(iterable):
            pytest.fail(
                "best_effort_extract_text must not call list() on the _tokenize() generator."
            )
        return real_list(iterable)

    builtins.list = list_except_tokenize_materialization
    try:
        parser = ContentStreamParser(b"(a)(b)", {})
        assert parser.best_effort_extract_text() == "ab"
    finally:
        builtins.list = real_list


def test_extract_text_with_mocked_list_allows_internal_list_usage():
    """Sanity: patching list only fails when given the tokenizer generator."""
    with patch("builtins.list") as m_list:
        real = __import__("builtins").list

        def side_effect(iterable):
            if inspect_is_tokenize_generator(iterable):
                raise AssertionError("tokenize materialized")
            return real(iterable)

        m_list.side_effect = side_effect
        parser = ContentStreamParser(b"BT (X) Tj ET", {})
        # Operand slice stack[-needed:] returns a new list in CPython — must still work
        assert parser.extract_text() == "X"
