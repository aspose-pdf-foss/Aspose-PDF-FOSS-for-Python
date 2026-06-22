import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.engine.simple_pdf import SimplePdf


def test_page_authoring_text_shapes_and_raw_image_roundtrip() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0.0, 0.0, 80.0, 60.0)], page_contents=[b""])
    page = doc.pages[0]

    page.draw_rectangle(5, 5, 12, 12, stroke_color=None, fill_color=(255, 0, 0))
    page.draw_line(0, 0, 20, 20, stroke_color=(0, 0, 255), line_width=2)
    page.add_text("Hello authoring", 5, 35, font_size=8)
    image_name = page.add_image(
        b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\x00",
        25,
        5,
        20,
        20,
        pixel_width=2,
        pixel_height=2,
        name="Logo",
    )

    assert image_name == "Logo"
    assert b"/Logo Do" in page.content

    buf = io.BytesIO()
    doc.save(buf)
    loaded = SimplePdf.from_bytes(buf.getvalue())

    assert "Hello authoring" in loaded.extract_text()
    assert "Logo" in loaded.images
    assert loaded._image_sizes["Logo"] == (2, 2)

    rendered_doc = Document()
    rendered_doc._engine_pdf = loaded
    # Exact-pixel placement check: disable anti-aliasing so edges stay crisp.
    raster = rendered_doc.pages[0].render(antialias=False)

    assert raster.get_pixel(8, 49) == (255, 0, 0)
    assert raster.get_pixel(30, 39) == (255, 0, 0)


def test_authoring_appends_to_loaded_pdf_contents_array() -> None:
    base = SimplePdf(
        pages=[(0.0, 0.0, 40.0, 30.0)],
        page_contents=[b"0 1 0 rg 0 0 10 10 re f"],
    )
    original = base.to_bytes()

    doc = Document()
    doc.load_from(original)
    doc.pages[0].add_text("Loaded edit", 12, 15, font_size=8)

    buf = io.BytesIO()
    doc.save(buf)
    loaded = SimplePdf.from_bytes(buf.getvalue())
    content = loaded.get_page_content(0)

    assert b"0 1 0 rg 0 0 10 10 re f" in content
    assert b"Loaded edit" in content
    assert "Loaded edit" in loaded.extract_text()

    page_dict = loaded._get_page_dict(0)
    contents = loaded._resolve(page_dict.mapping[PdfName("Contents")])
    assert isinstance(contents, PdfArray)
    assert len(contents.items) == 2


def test_page_add_image_accepts_png_bytes() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0.0, 0.0, 20.0, 20.0)], page_contents=[b""])
    png = write_png(1, 1, "RGB", b"\x00\x80\xff")

    name = doc.pages[0].add_image(png, 0, 0, 10, 10)
    data = doc._engine_pdf.to_bytes()
    loaded = SimplePdf.from_bytes(data)

    assert loaded._image_meta[name]["filter"] == "FlateDecode"

    rendered_doc = Document()
    rendered_doc._engine_pdf = loaded
    raster = rendered_doc.pages[0].render()
    assert raster.get_pixel(5, 15) == (0, 128, 255)


def test_tagged_authored_image_builds_pdfua_parent_tree_and_validates() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0.0, 0.0, 20.0, 20.0)], page_contents=[b""])
    page = doc.pages[0]

    page.add_image(
        b"\x00\x80\xff",
        2,
        2,
        10,
        10,
        pixel_width=1,
        pixel_height=1,
        alt="Blue sample",
    )
    assert b"/Figure << /MCID 0 >> BDC" in page.content

    doc.convert_to_pdfua(title="Tagged image")
    assert doc.validate_pdfua().is_valid

    engine = doc._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    struct_root = engine._resolve(catalog.mapping.get(PdfName("StructTreeRoot")))
    parent_tree = engine._resolve(struct_root.mapping.get(PdfName("ParentTree")))
    nums = engine._resolve(parent_tree.mapping.get(PdfName("Nums")))
    page_dict = engine._get_page_dict(0)
    struct_parent = engine._resolve(page_dict.mapping.get(PdfName("StructParents")))

    assert isinstance(struct_parent, PdfNumber)
    assert isinstance(nums, PdfArray)
    assert nums.items[0].value == struct_parent.value
    parent_array = engine._resolve(nums.items[1])
    elem = engine._resolve(parent_array.items[0])

    assert isinstance(elem, PdfDictionary)
    assert engine._get_name(elem.mapping.get(PdfName("S"))) == "Figure"
    assert engine._resolve(elem.mapping.get(PdfName("K"))).value == 0
    assert engine._resolve(elem.mapping.get(PdfName("Alt"))).value == b"Blue sample"

    buf = io.BytesIO()
    doc.save(buf)
    reloaded = Document()
    reloaded.load_from(buf.getvalue())
    assert reloaded.validate_pdfua().is_valid


def test_tagged_authored_text_records_actual_text_and_mcid() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0.0, 0.0, 80.0, 40.0)], page_contents=[b""])

    doc.pages[0].add_text(
        "Visible",
        5,
        20,
        font_size=8,
        tag="P",
        actual_text="Accessible Visible",
    )

    engine = doc._engine_pdf
    assert b"/P << /MCID 0 >> BDC" in doc.pages[0].content
    catalog = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    struct_root = engine._resolve(catalog.mapping.get(PdfName("StructTreeRoot")))
    kids = engine._resolve(struct_root.mapping.get(PdfName("K")))
    elem = engine._resolve(kids.items[0])

    assert engine._get_name(elem.mapping.get(PdfName("S"))) == "P"
    assert engine._resolve(elem.mapping.get(PdfName("K"))).value == 0
    actual = engine._resolve(elem.mapping.get(PdfName("ActualText")))
    assert isinstance(actual, PdfString)
    assert actual.value == b"Accessible Visible"

    doc.convert_to_pdfua(title="Tagged text")
    result = doc.validate_pdfua()
    assert not any("ParentTree" in issue for issue in result.errors)
    assert any("not embedded" in issue for issue in result.errors)
