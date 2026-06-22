"""Tests for the XMP namespace provider implementation."""

from __future__ import annotations

import pytest

from aspose_pdf.engine.data.xmp import (
    STANDARD_XMP_NAMESPACES,
    XmpNamespaceProvider,
)
from aspose_pdf.xmp import (
    NamespaceProvider,
    XmpArray,
    XmpField,
    XmpProperty,
)


# ---------------------------------------------------------------------------
# Standard namespace table
# ---------------------------------------------------------------------------


def test_standard_namespaces_have_unique_uris():
    uris = list(STANDARD_XMP_NAMESPACES.values())
    assert len(uris) == len(set(uris))


@pytest.mark.parametrize("prefix", ["dc", "xmp", "pdf", "pdfaid", "xmpMM", "tiff"])
def test_standard_prefixes_round_trip(prefix):
    provider = XmpNamespaceProvider()
    uri = provider.get_uri(prefix)
    assert uri == STANDARD_XMP_NAMESPACES[prefix]
    assert provider.get_prefix(uri) == prefix


# ---------------------------------------------------------------------------
# Internal XmpNamespaceProvider
# ---------------------------------------------------------------------------


def test_default_provider_resolves_known_namespaces():
    provider = XmpNamespaceProvider()
    assert provider.get_uri("dc") == "http://purl.org/dc/elements/1.1/"
    assert provider.get_prefix("http://ns.adobe.com/pdf/1.3/") == "pdf"


def test_unknown_lookups_return_none():
    provider = XmpNamespaceProvider()
    assert provider.get_uri("nope") is None
    assert provider.get_prefix("http://example.com/unknown/") is None
    assert provider.get_uri("") is None
    assert provider.get_prefix("") is None


def test_trailing_colon_on_prefix_is_ignored():
    provider = XmpNamespaceProvider()
    assert provider.get_uri("dc:") == provider.get_uri("dc")


def test_include_defaults_false_starts_empty():
    provider = XmpNamespaceProvider(include_defaults=False)
    assert provider.prefixes() == []
    assert provider.get_uri("dc") is None
    provider.register("dc", "http://purl.org/dc/elements/1.1/")
    assert provider.get_uri("dc") == "http://purl.org/dc/elements/1.1/"


def test_constructor_accepts_initial_mapping():
    provider = XmpNamespaceProvider(
        {"acme": "http://acme.example/ns/"}, include_defaults=False
    )
    assert provider.get_uri("acme") == "http://acme.example/ns/"
    assert provider.get_prefix("http://acme.example/ns/") == "acme"


def test_register_is_bidirectional_and_chainable():
    provider = XmpNamespaceProvider(include_defaults=False)
    result = provider.register("foo", "http://foo/").register("bar", "http://bar/")
    assert result is provider
    assert provider.get_uri("foo") == "http://foo/"
    assert provider.get_prefix("http://bar/") == "bar"


def test_register_empty_values_raise():
    provider = XmpNamespaceProvider(include_defaults=False)
    with pytest.raises(ValueError):
        provider.register("", "http://x/")
    with pytest.raises(ValueError):
        provider.register("x", "")


def test_register_overrides_prefix_and_cleans_stale_reverse():
    provider = XmpNamespaceProvider(include_defaults=False)
    provider.register("p", "http://old/")
    provider.register("p", "http://new/")
    # Forward mapping points to the new URI.
    assert provider.get_uri("p") == "http://new/"
    assert provider.get_prefix("http://new/") == "p"
    # The stale reverse entry for the old URI is gone.
    assert provider.get_prefix("http://old/") is None


def test_register_new_prefix_for_existing_uri_last_wins():
    provider = XmpNamespaceProvider(include_defaults=False)
    provider.register("a", "http://shared/")
    provider.register("b", "http://shared/")
    # Both prefixes resolve forward; the reverse points at the latest.
    assert provider.get_uri("a") == "http://shared/"
    assert provider.get_uri("b") == "http://shared/"
    assert provider.get_prefix("http://shared/") == "b"


def test_contains_and_introspection():
    provider = XmpNamespaceProvider(include_defaults=False)
    provider.register("dc", "http://purl.org/dc/elements/1.1/")
    assert "dc" in provider
    assert "dc:" in provider
    assert "missing" not in provider
    assert 123 not in provider  # non-str is safely False
    assert provider.prefixes() == ["dc"]
    assert provider.uris() == ["http://purl.org/dc/elements/1.1/"]
    assert provider.items() == [("dc", "http://purl.org/dc/elements/1.1/")]


# ---------------------------------------------------------------------------
# Public NamespaceProvider
# ---------------------------------------------------------------------------


def test_public_provider_is_concrete_and_resolves():
    provider = NamespaceProvider()
    # No longer raises NotImplementedError -- it actually resolves.
    assert provider.get_namespace_uri("dc") == "http://purl.org/dc/elements/1.1/"
    assert provider.get_uri("dc") == "http://purl.org/dc/elements/1.1/"
    assert provider.get_prefix("http://ns.adobe.com/xap/1.0/") == "xmp"


def test_public_provider_get_namespace_uri_unknown():
    provider = NamespaceProvider()
    assert provider.get_namespace_uri("does-not-exist") is None


def test_public_provider_register_custom():
    provider = NamespaceProvider()
    provider.register("acme", "http://acme.example/ns/")
    assert provider.get_namespace_uri("acme") == "http://acme.example/ns/"
    assert provider.get_prefix("http://acme.example/ns/") == "acme"


def test_public_provider_is_subclass_of_internal():
    assert issubclass(NamespaceProvider, XmpNamespaceProvider)


def test_provider_plugs_into_xmp_containers():
    provider = NamespaceProvider()
    field = XmpField(prefix="dc", name="title", value="Hello")
    array = XmpArray(namespace_provider=provider)
    array.add(field)
    prop = XmpProperty(field=field, namespace_provider=provider)
    assert array.namespace_provider.get_namespace_uri("dc") is not None
    assert prop.namespace_provider.get_prefix(
        "http://purl.org/dc/elements/1.1/"
    ) == "dc"


def test_custom_subclass_can_still_override():
    class FixedProvider(NamespaceProvider):
        def get_namespace_uri(self, prefix: str) -> str | None:
            return "urn:fixed"

    provider = FixedProvider()
    assert provider.get_namespace_uri("anything") == "urn:fixed"
    # Inherited reverse lookup still works.
    assert provider.get_prefix("http://purl.org/dc/elements/1.1/") == "dc"
