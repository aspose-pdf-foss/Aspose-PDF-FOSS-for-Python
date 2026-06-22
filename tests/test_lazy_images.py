from aspose_pdf.engine.simple_pdf import LazyImageDict

def test_lazy_image_dict_loading():
    call_count = 0
    def loader():
        nonlocal call_count
        call_count += 1
        return b"decoded_data"

    d = LazyImageDict()
    d.add_loader("img1", loader)

    assert "img1" in d
    assert len(d) == 1
    assert call_count == 0  # Not loaded yet

    # Access it
    assert d["img1"] == b"decoded_data"
    assert call_count == 1

    # Access again - should be cached
    assert d["img1"] == b"decoded_data"
    assert call_count == 1

def test_lazy_image_dict_contains_and_keys():
    d = LazyImageDict()
    d.add_loader("img1", lambda: b"data1")
    d["img2"] = b"data2"

    assert "img1" in d
    assert "img2" in d
    assert "img3" not in d
    # Use set comparison for keys
    assert set(d.keys()) == {"img1", "img2"}
    assert len(d) == 2

def test_lazy_image_dict_pop():
    d = LazyImageDict()
    d.add_loader("img1", lambda: b"data1")
    
    assert "img1" in d
    val = d.pop("img1")
    assert val == b"data1"
    assert "img1" not in d
    assert len(d) == 0

def test_lazy_image_dict_setitem_clears_loader():
    call_count = 0
    def loader():
        nonlocal call_count
        call_count += 1
        return b"old"

    d = LazyImageDict()
    d.add_loader("img1", loader)
    
    d["img1"] = b"new"
    # Ensure it doesn't trigger loader
    assert d["img1"] == b"new"
    assert call_count == 0

def test_lazy_image_dict_items_iteration():
    call_count = 0
    def loader():
        nonlocal call_count
        call_count += 1
        return b"loaded"

    d = LazyImageDict()
    d.add_loader("img1", loader)
    
    # items() should trigger loading
    items = list(d.items())
    assert len(items) == 1
    assert items[0] == ("img1", b"loaded")
    assert call_count == 1

def test_lazy_image_dict_values_iteration():
    call_count = 0
    def loader():
        nonlocal call_count
        call_count += 1
        return b"loaded"

    d = LazyImageDict()
    d.add_loader("img1", loader)
    
    # values() should trigger loading
    vals = list(d.values())
    assert vals == [b"loaded"]
    assert call_count == 1

def test_lazy_image_dict_copy():
    call_count = 0
    def loader():
        nonlocal call_count
        call_count += 1
        return b"data"

    d1 = LazyImageDict()
    d1.add_loader("img1", loader)
    
    d2 = d1.copy()
    assert "img1" in d2
    assert d2["img1"] == b"data"
    assert call_count == 1
    
    # Check original after copy's access
    assert "img1" in d1._loaders  # Still there
    # It is not in the underlying dict yet
    assert not super(LazyImageDict, d1).__contains__("img1")
    
    assert d1["img1"] == b"data"
    assert call_count == 2 # Loader is called again for d1
