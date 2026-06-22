"""Tests for XMP packet parsing and serialization."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf, _make_pdfa_xmp
from aspose_pdf.xmp import (
    XmpArray,
    XmpField,
    XmpPacket,
    XmpProperty,
    XmpStruct,
    parse,
    serialize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but fully parseable PDF (with proper xref)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n"
        b"210\n"
        b"%%EOF"
    )


DC = "http://purl.org/dc/elements/1.1/"
PDF_NS = "http://ns.adobe.com/pdf/1.3/"
PDFAID = "http://www.aiim.org/pdfa/ns/id/"

SAMPLE_XMP = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '    <rdf:Description rdf:about=""\n'
    '        xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
    '        xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
    "      <dc:format>application/pdf</dc:format>\n"
    "      <dc:title><rdf:Alt>"
    '<rdf:li xml:lang="x-default">Hello</rdf:li></rdf:Alt></dc:title>\n'
    "      <dc:creator><rdf:Seq>"
    "<rdf:li>Ada</rdf:li><rdf:li>Grace</rdf:li></rdf:Seq></dc:creator>\n"
    "      <pdf:Producer>Acme</pdf:Producer>\n"
    "    </rdf:Description>\n"
    "  </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_simple_property():
    packet = parse(SAMPLE_XMP)
    fmt = packet.get("dc", "format")
    assert fmt is not None
    assert fmt.value == "application/pdf"
    assert fmt.prefix == "dc"
    assert fmt.namespace_uri == DC


def test_parse_resolves_namespace_uri_by_prefix_or_uri():
    packet = parse(SAMPLE_XMP)
    assert packet.get("pdf", "Producer").value == "Acme"
    # Lookup by namespace URI works too.
    assert packet.get(PDF_NS, "Producer").value == "Acme"


def test_parse_alt_array_with_language():
    packet = parse(SAMPLE_XMP)
    title = packet.get("dc", "title")
    assert isinstance(title.value, XmpArray)
    assert title.value.kind == "Alt"
    assert len(title.value.items) == 1
    assert title.value.items[0].value == "Hello"
    assert title.value.items[0].language == "x-default"


def test_parse_seq_array_order_preserved():
    packet = parse(SAMPLE_XMP)
    creator = packet.get("dc", "creator")
    assert isinstance(creator.value, XmpArray)
    assert creator.value.kind == "Seq"
    assert [item.value for item in creator.value.items] == ["Ada", "Grace"]


def test_parse_accepts_bytes_with_bom():
    packet = parse(b"\xef\xbb\xbf" + SAMPLE_XMP.encode("utf-8"))
    assert packet.get("dc", "format").value == "application/pdf"


def test_parse_abbreviated_attribute_form():
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
        'pdf:Producer="Acme 1.0" pdf:Keywords="a,b"/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    packet = parse(xmp)
    assert packet.get("pdf", "Producer").value == "Acme 1.0"
    assert packet.get("pdf", "Keywords").value == "a,b"


def test_parse_empty_when_no_rdf():
    packet = parse('<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>')
    assert packet.fields == []


# ---------------------------------------------------------------------------
# Security — DTD / entity-expansion guard
# ---------------------------------------------------------------------------


def test_parse_rejects_doctype():
    hostile = (
        '<?xml version="1.0"?>'
        "<!DOCTYPE x [ <!ENTITY a \"boom\"> ]>"
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>'
    )
    with pytest.raises(ValueError):
        parse(hostile)


def test_parse_rejects_entity_declaration():
    hostile = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        "<!ENTITY lol \"ha\">"
        "</x:xmpmeta>"
    )
    with pytest.raises(ValueError):
        parse(hostile)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialize_wrapper_shape():
    packet = XmpPacket()
    packet.add(XmpField(prefix="pdf", name="Producer", namespace_uri=PDF_NS, value="X"))
    text = serialize(packet).decode("utf-8")
    assert text.startswith("<?xpacket")
    assert text.endswith('<?xpacket end="w"?>')
    assert 'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"' in text
    assert "<pdf:Producer>X</pdf:Producer>" in text


def test_serialize_escapes_special_characters():
    packet = XmpPacket()
    packet.add(XmpField(prefix="dc", name="rights", namespace_uri=DC, value="a & b <c>"))
    text = serialize(packet).decode("utf-8")
    assert "a &amp; b &lt;c&gt;" in text


def test_serialize_returns_bytes():
    assert isinstance(serialize(XmpPacket()), bytes)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def _representative_packet() -> XmpPacket:
    packet = XmpPacket()
    # Prefix-grouped (the serializer groups by prefix) for order-stable equality.
    packet.add(XmpField(prefix="dc", name="format", namespace_uri=DC, value="application/pdf"))
    packet.add(
        XmpField(
            prefix="dc",
            name="title",
            namespace_uri=DC,
            value=XmpArray(
                kind="Alt", items=[XmpField(value="Hello", language="x-default")]
            ),
        )
    )
    packet.add(
        XmpField(
            prefix="dc",
            name="creator",
            namespace_uri=DC,
            value=XmpArray(kind="Seq", items=[XmpField(value="Ada"), XmpField(value="Grace")]),
        )
    )
    packet.add(XmpField(prefix="pdf", name="Producer", namespace_uri=PDF_NS, value="Acme"))
    return packet


def test_round_trip_parse_serialize_parse():
    original = _representative_packet()
    reparsed = parse(serialize(original))
    assert reparsed.fields == original.fields


def test_round_trip_unicode_value():
    packet = XmpPacket()
    packet.add(XmpField(prefix="dc", name="title", namespace_uri=DC, value="Café — Привет"))
    reparsed = parse(serialize(packet))
    assert reparsed.get("dc", "title").value == "Café — Привет"


# ---------------------------------------------------------------------------
# Structured values (rdf:parseType="Resource" / nested rdf:Description)
# ---------------------------------------------------------------------------

XMPTPG = "http://ns.adobe.com/xap/1.0/t/pg/"
STDIM = "http://ns.adobe.com/xap/1.0/sType/Dimensions#"
XMPMM = "http://ns.adobe.com/xap/1.0/mm/"
STEVT = "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
STREF = "http://ns.adobe.com/xap/1.0/sType/ResourceRef#"


def _wrap_rdf(description_body: str) -> str:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{description_body}"
        "</rdf:RDF></x:xmpmeta>"
    )


def test_parse_struct_parse_type_resource():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpTPg="{XMPTPG}" xmlns:stDim="{STDIM}">'
        '<xmpTPg:MaxPageSize rdf:parseType="Resource">'
        "<stDim:w>210</stDim:w><stDim:h>297</stDim:h><stDim:unit>mm</stDim:unit>"
        "</xmpTPg:MaxPageSize>"
        "</rdf:Description>"
    )
    struct = parse(xmp).get("xmpTPg", "MaxPageSize").value
    assert isinstance(struct, XmpStruct)
    assert [(m.prefix, m.name, m.value) for m in struct.fields] == [
        ("stDim", "w", "210"),
        ("stDim", "h", "297"),
        ("stDim", "unit", "mm"),
    ]
    assert struct.get("unit").namespace_uri == STDIM


def test_parse_struct_nested_description_attribute_members():
    # The general struct form: a nested rdf:Description whose members are
    # carried as abbreviated attributes (stRef:*).
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpMM="{XMPMM}" xmlns:stRef="{STREF}">'
        "<xmpMM:DerivedFrom>"
        '<rdf:Description stRef:instanceID="xmp.iid:1"'
        ' stRef:documentID="xmp.did:2"/>'
        "</xmpMM:DerivedFrom>"
        "</rdf:Description>"
    )
    struct = parse(xmp).get("xmpMM", "DerivedFrom").value
    assert isinstance(struct, XmpStruct)
    assert struct.get("instanceID").value == "xmp.iid:1"
    assert struct.get("documentID").value == "xmp.did:2"


def test_parse_seq_of_structs_history():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpMM="{XMPMM}" xmlns:stEvt="{STEVT}">'
        "<xmpMM:History><rdf:Seq>"
        '<rdf:li rdf:parseType="Resource">'
        "<stEvt:action>created</stEvt:action><stEvt:when>2024-01-01</stEvt:when>"
        "</rdf:li>"
        '<rdf:li rdf:parseType="Resource">'
        "<stEvt:action>saved</stEvt:action><stEvt:when>2024-02-02</stEvt:when>"
        "</rdf:li>"
        "</rdf:Seq></xmpMM:History>"
        "</rdf:Description>"
    )
    history = parse(xmp).get("xmpMM", "History").value
    assert isinstance(history, XmpArray)
    assert history.kind == "Seq"
    assert len(history.items) == 2
    first = history.items[0].value
    assert isinstance(first, XmpStruct)
    assert first.get("action").value == "created"
    assert history.items[1].value.get("when").value == "2024-02-02"


def test_serialize_struct_emits_resource_and_member_namespaces():
    packet = XmpPacket()
    packet.add(
        XmpField(
            prefix="xmpTPg",
            name="MaxPageSize",
            namespace_uri=XMPTPG,
            value=XmpStruct(
                fields=[
                    XmpField(prefix="stDim", name="w", namespace_uri=STDIM, value="210"),
                    XmpField(prefix="stDim", name="h", namespace_uri=STDIM, value="297"),
                ]
            ),
        )
    )
    text = serialize(packet).decode("utf-8")
    assert 'rdf:parseType="Resource"' in text
    # Both the property prefix and the struct-member prefix are declared.
    assert f'xmlns:xmpTPg="{XMPTPG}"' in text
    assert f'xmlns:stDim="{STDIM}"' in text
    assert "<stDim:w>210</stDim:w>" in text


def test_round_trip_struct_from_xml():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpTPg="{XMPTPG}" xmlns:stDim="{STDIM}">'
        '<xmpTPg:MaxPageSize rdf:parseType="Resource">'
        "<stDim:w>210</stDim:w><stDim:h>297</stDim:h>"
        "</xmpTPg:MaxPageSize>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    assert (
        once.get("xmpTPg", "MaxPageSize").value
        == twice.get("xmpTPg", "MaxPageSize").value
    )


def test_round_trip_seq_of_structs_from_xml():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpMM="{XMPMM}" xmlns:stEvt="{STEVT}">'
        "<xmpMM:History><rdf:Seq>"
        '<rdf:li rdf:parseType="Resource"><stEvt:action>created</stEvt:action></rdf:li>'
        '<rdf:li rdf:parseType="Resource"><stEvt:action>saved</stEvt:action></rdf:li>'
        "</rdf:Seq></xmpMM:History>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    assert once.get("xmpMM", "History").value == twice.get("xmpMM", "History").value


def test_round_trip_nested_struct_is_serialize_stable():
    # A struct member whose own value is a struct.
    packet = XmpPacket()
    inner = XmpStruct(
        fields=[XmpField(prefix="stRef", name="instanceID", namespace_uri=STREF, value="iid:9")]
    )
    outer = XmpStruct(
        fields=[
            XmpField(prefix="stEvt", name="action", namespace_uri=STEVT, value="derived"),
            XmpField(prefix="stEvt", name="changed", namespace_uri=STEVT, value=inner),
        ]
    )
    packet.add(XmpField(prefix="xmpMM", name="Thing", namespace_uri=XMPMM, value=outer))

    serialized = serialize(packet)
    assert serialize(parse(serialized)) == serialized
    assert parse(serialized).get("xmpMM", "Thing").value == outer


def test_struct_persists_through_document_save():
    doc = Document()
    doc.load_from(io.BytesIO(_minimal_pdf_bytes()))
    packet = doc.xmp_metadata
    packet.add(
        XmpField(
            prefix="xmpTPg",
            name="MaxPageSize",
            namespace_uri=XMPTPG,
            value=XmpStruct(
                fields=[
                    XmpField(prefix="stDim", name="w", namespace_uri=STDIM, value="612"),
                    XmpField(prefix="stDim", name="unit", namespace_uri=STDIM, value="pt"),
                ]
            ),
        )
    )
    doc.xmp_metadata = packet

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    struct = reloaded.xmp_metadata.get("xmpTPg", "MaxPageSize").value
    assert isinstance(struct, XmpStruct)
    assert struct.get("w").value == "612"
    assert struct.get("unit").value == "pt"


# ---------------------------------------------------------------------------
# RDF qualifiers (rdf:value + sibling qualifier members)
# ---------------------------------------------------------------------------

XMP_NS = "http://ns.adobe.com/xap/1.0/"
XMPIDQ = "http://ns.adobe.com/xmp/Identifier/qual/1.0/"


def test_parse_qualified_property_element_form():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmp="{XMP_NS}" xmlns:xmpidq="{XMPIDQ}">'
        '<xmp:Identifier rdf:parseType="Resource">'
        "<rdf:value>12345</rdf:value>"
        "<xmpidq:Scheme>ISBN</xmpidq:Scheme>"
        "</xmp:Identifier>"
        "</rdf:Description>"
    )
    entry = parse(xmp).fields[0]
    assert isinstance(entry, XmpProperty)
    assert entry.field.prefix == "xmp"
    assert entry.field.name == "Identifier"
    assert entry.field.value == "12345"
    assert [(q.prefix, q.name, q.value) for q in entry.qualifiers] == [
        ("xmpidq", "Scheme", "ISBN")
    ]


def test_parse_qualified_property_attribute_form():
    # rdf:value as a child of a nested rdf:Description whose qualifier is an
    # abbreviated attribute.
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmp="{XMP_NS}" xmlns:xmpidq="{XMPIDQ}">'
        "<xmp:Identifier>"
        '<rdf:Description xmpidq:Scheme="DOI"><rdf:value>10.1/x</rdf:value>'
        "</rdf:Description>"
        "</xmp:Identifier>"
        "</rdf:Description>"
    )
    entry = parse(xmp).fields[0]
    assert isinstance(entry, XmpProperty)
    assert entry.field.value == "10.1/x"
    assert entry.qualifiers[0].name == "Scheme"
    assert entry.qualifiers[0].value == "DOI"


def test_struct_without_rdf_value_is_not_a_qualified_property():
    # A struct with no rdf:value must stay an XmpStruct, not become a property.
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpTPg="{XMPTPG}" xmlns:stDim="{STDIM}">'
        '<xmpTPg:MaxPageSize rdf:parseType="Resource">'
        "<stDim:w>210</stDim:w>"
        "</xmpTPg:MaxPageSize>"
        "</rdf:Description>"
    )
    entry = parse(xmp).fields[0]
    assert isinstance(entry, XmpField)
    assert isinstance(entry.value, XmpStruct)


def test_serialize_qualified_property_emits_rdf_value_and_qualifier():
    packet = XmpPacket()
    packet.add(
        XmpProperty(
            field=XmpField(
                prefix="xmp", name="Identifier", namespace_uri=XMP_NS, value="42"
            ),
            qualifiers=[
                XmpField(
                    prefix="xmpidq", name="Scheme", namespace_uri=XMPIDQ, value="ISSN"
                )
            ],
        )
    )
    text = serialize(packet).decode("utf-8")
    assert 'rdf:parseType="Resource"' in text
    assert "<rdf:value>42</rdf:value>" in text
    assert "<xmpidq:Scheme>ISSN</xmpidq:Scheme>" in text
    # The qualifier's namespace is declared on the rdf:Description.
    assert f'xmlns:xmpidq="{XMPIDQ}"' in text


def test_round_trip_qualified_property_from_xml():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmp="{XMP_NS}" xmlns:xmpidq="{XMPIDQ}">'
        '<xmp:Identifier rdf:parseType="Resource">'
        "<rdf:value>12345</rdf:value><xmpidq:Scheme>ISBN</xmpidq:Scheme>"
        "</xmp:Identifier>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    e1, e2 = once.fields[0], twice.fields[0]
    assert isinstance(e2, XmpProperty)
    assert e1.field == e2.field
    assert e1.qualifiers == e2.qualifiers


def test_round_trip_qualified_property_programmatic_serialize_stable():
    packet = XmpPacket()
    packet.add(
        XmpProperty(
            field=XmpField(
                prefix="xmp", name="Identifier", namespace_uri=XMP_NS, value="9"
            ),
            qualifiers=[
                XmpField(
                    prefix="xmpidq", name="Scheme", namespace_uri=XMPIDQ, value="ISBN"
                )
            ],
        )
    )
    serialized = serialize(packet)
    assert serialize(parse(serialized)) == serialized


# ---------------------------------------------------------------------------
# Qualifiers on values nested inside arrays / structs
# ---------------------------------------------------------------------------


def test_parse_struct_member_with_qualifier():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpMM="{XMPMM}" xmlns:stRef="{STREF}" xmlns:xmpidq="{XMPIDQ}">'
        '<xmpMM:DerivedFrom rdf:parseType="Resource">'
        '<stRef:instanceID rdf:parseType="Resource">'
        "<rdf:value>id-7</rdf:value><xmpidq:Scheme>uuid</xmpidq:Scheme>"
        "</stRef:instanceID>"
        "<stRef:renditionClass>default</stRef:renditionClass>"
        "</xmpMM:DerivedFrom>"
        "</rdf:Description>"
    )
    struct = parse(xmp).get("xmpMM", "DerivedFrom").value
    assert isinstance(struct, XmpStruct)
    iid = struct.get("instanceID")
    assert iid.value == "id-7"
    assert [(q.name, q.value) for q in iid.qualifiers] == [("Scheme", "uuid")]
    # A sibling member without qualifiers is unaffected.
    assert struct.get("renditionClass").qualifiers == []


def test_parse_array_item_with_qualifier():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:dc="{DC}" xmlns:xmpidq="{XMPIDQ}">'
        "<dc:identifier><rdf:Bag>"
        '<rdf:li rdf:parseType="Resource">'
        "<rdf:value>978-1</rdf:value><xmpidq:Scheme>ISBN</xmpidq:Scheme>"
        "</rdf:li>"
        "<rdf:li>plain</rdf:li>"
        "</rdf:Bag></dc:identifier>"
        "</rdf:Description>"
    )
    array = parse(xmp).get("dc", "identifier").value
    assert isinstance(array, XmpArray)
    assert array.items[0].value == "978-1"
    assert array.items[0].qualifiers[0].value == "ISBN"
    assert array.items[1].value == "plain"
    assert array.items[1].qualifiers == []


def test_round_trip_nested_qualifier_struct_member():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmpMM="{XMPMM}" xmlns:stRef="{STREF}" xmlns:xmpidq="{XMPIDQ}">'
        '<xmpMM:DerivedFrom rdf:parseType="Resource">'
        '<stRef:instanceID rdf:parseType="Resource">'
        "<rdf:value>id-7</rdf:value><xmpidq:Scheme>uuid</xmpidq:Scheme>"
        "</stRef:instanceID></xmpMM:DerivedFrom>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    assert (
        once.get("xmpMM", "DerivedFrom").value
        == twice.get("xmpMM", "DerivedFrom").value
    )


def test_round_trip_nested_qualifier_array_item():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:dc="{DC}" xmlns:xmpidq="{XMPIDQ}">'
        "<dc:identifier><rdf:Bag>"
        '<rdf:li rdf:parseType="Resource">'
        "<rdf:value>978-1</rdf:value><xmpidq:Scheme>ISBN</xmpidq:Scheme>"
        "</rdf:li></rdf:Bag></dc:identifier>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    assert once.get("dc", "identifier").value == twice.get("dc", "identifier").value


def test_serialize_nested_qualifier_declares_member_namespace():
    packet = XmpPacket()
    item = XmpField(
        value="42",
        qualifiers=[
            XmpField(prefix="xmpidq", name="Scheme", namespace_uri=XMPIDQ, value="ISSN")
        ],
    )
    packet.add(
        XmpField(
            prefix="dc",
            name="identifier",
            namespace_uri=DC,
            value=XmpArray(kind="Bag", items=[item]),
        )
    )
    text = serialize(packet).decode("utf-8")
    assert "<rdf:value>42</rdf:value>" in text
    assert "<xmpidq:Scheme>ISSN</xmpidq:Scheme>" in text
    assert f'xmlns:xmpidq="{XMPIDQ}"' in text
    # And it survives a parse/serialize round-trip unchanged.
    assert serialize(parse(text)) == serialize(packet)


def test_round_trip_recursive_qualification():
    # A qualifier that itself carries a qualifier (qualifier-on-qualifier).
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        f' xmlns:xmp="{XMP_NS}" xmlns:xmpidq="{XMPIDQ}" xmlns:dc="{DC}">'
        '<xmp:Identifier rdf:parseType="Resource">'
        "<rdf:value>42</rdf:value>"
        '<xmpidq:Scheme rdf:parseType="Resource">'
        "<rdf:value>ISBN</rdf:value><dc:source>registry</dc:source>"
        "</xmpidq:Scheme>"
        "</xmp:Identifier>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    entry = once.fields[0]
    assert isinstance(entry, XmpProperty)
    scheme = entry.qualifiers[0]
    assert scheme.value == "ISBN"
    assert [(q.name, q.value) for q in scheme.qualifiers] == [("source", "registry")]
    twice = parse(serialize(once))
    assert twice.fields[0].field == entry.field
    assert twice.fields[0].qualifiers == entry.qualifiers


# ---------------------------------------------------------------------------
# rdf:resource URI values
# ---------------------------------------------------------------------------


def test_parse_resource_sets_is_uri():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about="" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
        '<xmpMM:DerivedFrom rdf:resource="uuid:abc-123"/>'
        "</rdf:Description>"
    )
    fld = parse(xmp).get("xmpMM", "DerivedFrom")
    assert fld.value == "uuid:abc-123"
    assert fld.is_uri is True


def test_serialize_resource_emits_attribute_not_text():
    packet = XmpPacket()
    packet.add(
        XmpField(
            prefix="xmpMM",
            name="DerivedFrom",
            namespace_uri="http://ns.adobe.com/xap/1.0/mm/",
            value="uuid:xyz",
            is_uri=True,
        )
    )
    text = serialize(packet).decode("utf-8")
    assert 'rdf:resource="uuid:xyz"' in text
    # An empty element — no text content / closing tag for the property.
    assert "<xmpMM:DerivedFrom" in text
    assert "</xmpMM:DerivedFrom>" not in text
    assert ">uuid:xyz<" not in text


def test_round_trip_resource_simple():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about="" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
        '<xmpMM:DerivedFrom rdf:resource="uuid:abc-123"/>'
        "</rdf:Description>"
    )
    once = parse(xmp)
    twice = parse(serialize(once))
    assert once.get("xmpMM", "DerivedFrom") == twice.get("xmpMM", "DerivedFrom")


def test_round_trip_resource_array():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:relation><rdf:Bag>"
        '<rdf:li rdf:resource="http://a/"/><rdf:li rdf:resource="http://b/"/>'
        "</rdf:Bag></dc:relation>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    relation = once.get("dc", "relation").value
    assert isinstance(relation, XmpArray)
    assert all(item.is_uri for item in relation.items)
    twice = parse(serialize(once))
    assert twice.get("dc", "relation").value == relation


def test_round_trip_resource_struct_member():
    xmp = _wrap_rdf(
        '<rdf:Description rdf:about=""'
        ' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
        ' xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#">'
        '<xmpMM:DerivedFrom rdf:parseType="Resource">'
        '<stRef:instanceID rdf:resource="x:1"/>'
        "<stRef:renditionClass>default</stRef:renditionClass>"
        "</xmpMM:DerivedFrom>"
        "</rdf:Description>"
    )
    once = parse(xmp)
    struct = once.get("xmpMM", "DerivedFrom").value
    assert isinstance(struct, XmpStruct)
    assert struct.get("instanceID").is_uri is True
    assert struct.get("renditionClass").is_uri is False
    twice = parse(serialize(once))
    assert twice.get("xmpMM", "DerivedFrom").value == struct


# ---------------------------------------------------------------------------
# Typed convenience accessors (date / localized text / array)
# ---------------------------------------------------------------------------

XMP_NS_T = "http://ns.adobe.com/xap/1.0/"


def test_set_get_date_round_trip():
    packet = XmpPacket()
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    packet.set_date("xmp", "CreateDate", dt, uri=XMP_NS_T)
    assert packet.get("xmp", "CreateDate").value == "2024-01-02T03:04:05+00:00"
    assert packet.get_date("xmp", "CreateDate") == dt


def test_set_date_survives_serialize_round_trip():
    packet = XmpPacket()
    dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    packet.set_date("xmp", "ModifyDate", dt, uri=XMP_NS_T)
    reparsed = parse(serialize(packet))
    assert reparsed.get_date("xmp", "ModifyDate") == dt


def test_get_date_returns_none_for_missing_or_invalid():
    packet = XmpPacket()
    assert packet.get_date("xmp", "CreateDate") is None
    packet.set_value("xmp", "CreateDate", "not-a-date", uri=XMP_NS_T)
    assert packet.get_date("xmp", "CreateDate") is None


def test_set_get_localized_text():
    packet = XmpPacket()
    packet.set_localized_text("dc", "title", "Hello", uri=DC)
    value = packet.get("dc", "title").value
    assert isinstance(value, XmpArray)
    assert value.kind == "Alt"
    assert value.items[0].language == "x-default"
    assert packet.get_localized_text("dc", "title") == "Hello"
    # Lookup by namespace URI also works.
    assert packet.get_localized_text(DC, "title") == "Hello"


def test_get_localized_text_none_when_absent():
    assert XmpPacket().get_localized_text("dc", "title") is None


def test_set_get_array_seq_and_bag():
    packet = XmpPacket()
    packet.set_array("dc", "creator", ["Ada", "Grace"], uri=DC, kind="Seq")
    assert packet.get("dc", "creator").value.kind == "Seq"
    assert packet.get_array("dc", "creator") == ["Ada", "Grace"]

    packet.set_array("dc", "subject", ["k1", "k2"], uri=DC, kind="Bag")
    assert packet.get("dc", "subject").value.kind == "Bag"
    assert packet.get_array("dc", "subject") == ["k1", "k2"]
    assert packet.get_array("dc", "missing") is None


def test_set_array_accepts_prebuilt_fields():
    packet = XmpPacket()
    packet.set_array(
        "dc",
        "title",
        [XmpField(value="Bonjour", language="fr"), XmpField(value="Hello", language="en")],
        uri=DC,
        kind="Alt",
    )
    items = packet.get("dc", "title").value.items
    assert items[0].language == "fr"
    assert packet.get_array("dc", "title") == ["Bonjour", "Hello"]


RIGHTS_NS = "http://ns.adobe.com/xap/1.0/rights/"
EXIF_NS = "http://ns.adobe.com/exif/1.0/"


def test_set_get_bool():
    packet = XmpPacket()
    packet.set_bool("xmpRights", "Marked", True, uri=RIGHTS_NS)
    # XMP encodes booleans as the capitalized words.
    assert packet.get("xmpRights", "Marked").value == "True"
    assert packet.get_bool("xmpRights", "Marked") is True
    packet.set_bool("xmpRights", "Marked", False, uri=RIGHTS_NS)
    assert packet.get_bool("xmpRights", "Marked") is False


def test_set_get_int_and_real():
    packet = XmpPacket()
    packet.set_int("exif", "PixelXDimension", 1920, uri=EXIF_NS)
    packet.set_real("xmp", "Rating", 4.5, uri=XMP_NS_T)
    assert packet.get("exif", "PixelXDimension").value == "1920"
    assert packet.get_int("exif", "PixelXDimension") == 1920
    assert packet.get_real("xmp", "Rating") == 4.5


def test_scalar_accessors_survive_serialize_round_trip():
    packet = XmpPacket()
    packet.set_bool("xmpRights", "Marked", True, uri=RIGHTS_NS)
    packet.set_int("exif", "PixelXDimension", 1920, uri=EXIF_NS)
    packet.set_real("xmp", "Rating", 4.5, uri=XMP_NS_T)
    reparsed = parse(serialize(packet))
    assert reparsed.get_bool("xmpRights", "Marked") is True
    assert reparsed.get_int("exif", "PixelXDimension") == 1920
    assert reparsed.get_real("xmp", "Rating") == 4.5


def test_scalar_accessors_return_none_on_absence_or_mismatch():
    packet = XmpPacket()
    assert packet.get_bool("xmpRights", "Marked") is None
    assert packet.get_int("exif", "PixelXDimension") is None
    assert packet.get_real("xmp", "Rating") is None
    packet.set_value("xmp", "Note", "hello", uri=XMP_NS_T)
    assert packet.get_bool("xmp", "Note") is None
    assert packet.get_int("xmp", "Note") is None
    assert packet.get_real("xmp", "Note") is None


# ---------------------------------------------------------------------------
# Interop with the PDF/A packet builder
# ---------------------------------------------------------------------------


def test_make_pdfa_xmp_parses_back_to_pdfaid_fields():
    packet = parse(_make_pdfa_xmp("2b", "My Title"))
    assert packet.get("pdfaid", "part").value == "2"
    assert packet.get("pdfaid", "conformance").value == "B"
    title = packet.get("dc", "title")
    assert isinstance(title.value, XmpArray)
    assert title.value.items[0].value == "My Title"


# ---------------------------------------------------------------------------
# Document integration
# ---------------------------------------------------------------------------


def test_document_xmp_metadata_empty_by_default():
    doc = Document()
    doc.load_from(io.BytesIO(_minimal_pdf_bytes()))
    assert doc.xmp_metadata.fields == []


def test_document_xmp_metadata_set_persists_through_save():
    doc = Document()
    doc.load_from(io.BytesIO(_minimal_pdf_bytes()))
    packet = doc.xmp_metadata
    packet.set_value("dc", "title", "Round Trip", uri=DC)
    packet.add(XmpField(prefix="pdf", name="Producer", namespace_uri=PDF_NS, value="foss"))
    doc.xmp_metadata = packet

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    assert reloaded.xmp_metadata.get("dc", "title").value == "Round Trip"
    assert reloaded.xmp_metadata.get("pdf", "Producer").value == "foss"


def test_document_xmp_metadata_inplace_edit_persists():
    doc = Document()
    doc.load_from(io.BytesIO(_minimal_pdf_bytes()))
    doc.xmp_metadata.set_value("dc", "creator", "Me", uri=DC)  # no re-assignment

    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(io.BytesIO(buffer.getvalue()))
    assert reloaded.xmp_metadata.get("dc", "creator").value == "Me"


def test_engine_metadata_stream_written_to_catalog():
    pdf = SimplePdf.from_bytes(_minimal_pdf_bytes())
    packet = pdf.xmp_packet
    packet.set_value("dc", "title", "T", uri=DC)
    pdf.xmp_packet = packet
    out = pdf.to_bytes()
    assert b"/Type /Metadata" in out or b"/Type/Metadata" in out
    assert b"<dc:title>T</dc:title>" in out
