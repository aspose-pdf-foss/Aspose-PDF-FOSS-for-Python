
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfArray, PdfIndirectReference

def test_gc_literal_cycle():
    """Verify that GC handles literal cycles without infinite looping."""
    pdf = SimplePdf()
    pdf._ensure_cos()
    
    # Create literal cycle: d1 -> d2 -> d1
    d1 = PdfDictionary()
    d2 = PdfDictionary()
    d1.mapping[PdfName("X")] = d2
    d2.mapping[PdfName("Y")] = d1
    
    # Link d1 to Root
    root_ref = pdf._cos_doc.trailer.mapping[PdfName("Root")]
    root = pdf._resolve(root_ref)
    root.mapping[PdfName("Test")] = d1
    
    # This should not hang
    removed = pdf.garbage_collect()
    assert removed == 0

def test_gc_unreachable_indirect_cycle():
    """Verify that unreachable indirect cycles are removed."""
    pdf = SimplePdf()
    pdf._ensure_cos()
    
    # Create unreachable cycle obj 10 <-> obj 11
    d10 = PdfDictionary({PdfName("Next"): PdfIndirectReference(11)})
    d11 = PdfDictionary({PdfName("Prev"): PdfIndirectReference(10)})
    
    pdf._cos_doc.objects[10] = d10
    pdf._cos_doc.objects[11] = d11
    
    assert 10 in pdf._cos_doc.objects
    assert 11 in pdf._cos_doc.objects
    
    removed = pdf.garbage_collect()
    assert removed >= 2
    assert 10 not in pdf._cos_doc.objects
    assert 11 not in pdf._cos_doc.objects

def test_gc_all_trailer_roots():
    """Verify that objects reachable from ANY trailer entry are preserved."""
    pdf = SimplePdf()
    pdf._ensure_cos()
    
    # Custom trailer entry
    custom_obj = PdfDictionary({PdfName("Data"): PdfName("Important")})
    custom_ref = pdf._cos_doc.register_object(custom_obj)
    
    pdf._cos_doc.trailer.mapping[PdfName("MyCustomRoot")] = custom_ref
    
    assert custom_ref.object_number in pdf._cos_doc.objects
    
    pdf.garbage_collect()
    # custom_obj should NOT be removed because it's in the trailer
    assert custom_ref.object_number in pdf._cos_doc.objects

def test_gc_nested_literals():
    """Verify that nested literal containers are correctly traversed."""
    pdf = SimplePdf()
    pdf._ensure_cos()
    
    # Root -> [ { "Ref": 10 0 R } ]
    target_obj = PdfDictionary({PdfName("Val"): PdfName("Target")})
    target_ref = pdf._cos_doc.register_object(target_obj)
    
    nested = PdfArray([PdfDictionary({PdfName("Ref"): target_ref})])
    
    root_ref = pdf._cos_doc.trailer.mapping[PdfName("Root")]
    root = pdf._resolve(root_ref)
    root.mapping[PdfName("Nested")] = nested
    
    assert target_ref.object_number in pdf._cos_doc.objects
    
    pdf.garbage_collect()
    assert target_ref.object_number in pdf._cos_doc.objects
