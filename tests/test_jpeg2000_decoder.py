"""The bundled JPEG 2000 decoder, and what a page does without one.

/JPXDecode used to need Pillow. Without it the filter raised, the stream
decoder fell back to handing the *raw codestream* to its caller, and the
rasterizer painted those compressed bytes as if they were samples -- a page of
noise where the scan should be. Silently wrong output is worse than none.

:mod: removes the dependency: a pure-Python decoder
covering the MQ arithmetic coder, EBCOT tier-1, the tier-2 packet structure and
both wavelets. The reversible (5/3) path is mathematically lossless, so every
fixture below decodes back to the exact pattern it was made from -- no stored
reference bytes, the assertion *is* the round trip. The irreversible (9/7) path
is floating point and compared with a tolerance.

The fixtures are embedded rather than generated so the suite covers the decoder
in the CI job that installs no optional dependencies -- the one where this code
is the only decoder there is. They were produced with Pillow/OpenJPEG, which
also cross-checked the decoder over a much wider matrix (tiles, precincts, all
five progression orders, code-block sizes, layers, bit depths) than is worth
embedding here.
"""

from __future__ import annotations

import hashlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.jpeg2000 import Jpeg2000Error, decode, extract_codestream
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits

_GRAY_8x8_NO_DWT = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000008000000080001070700000000000f636f"
    "6c7201000000000011000000ba6a703263ff4fff5100290000000000080000000800"
    "00000000000000000000080000000800000000000000000001070101ff52000c0000"
    "0001000004040001ff5c00044040ff640025000143726561746564206279204f7065"
    "6e4a5045472076657273696f6e20322e352e34ff90000a0000000000480001ff93df"
    "81b8122b670d0d1e039ad711de2b465786dd09f3b4d9e8ef2d9cf986b479393d5973"
    "979429bfe60b2a6f58d5eaf57abd4979e2d555555555a8ffd9"
)

_RGB_16x16_53 = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100003070700000000000f636f"
    "6c7201000000000010000001756a703263ff4fff51002f0000000000100000001000"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00000001000404040001ff5c00104040484850484850484850484850ff64"
    "0025000143726561746564206279204f70656e4a5045472076657273696f6e20322e"
    "352e34ff90000a0000000000f10001ff93c7d40406efc7d40209cfb408088fc3ea02"
    "8fb40a01f082008f002f07c7da0507ce0801c7001fc0f90143ea0200043f033fc3ea"
    "0387d4090fb40c0d9cdf0b3667ab10845bc3ea0381f204000cfaed0b37ff7fc07c21"
    "c1f382000cff7f0b3cc7da0d3f00887da0e024aa630226cf24beac699ab52a0f236c"
    "f320e5045bc1f38480f843002218b77b037e5fc03a140f90182219037fbfcfc04a7e"
    "0251f8040060a6d63011d07397c596618c71bc30150f1f580bc6ee69465c9d21faf8"
    "f9916e4cc85f3f52d35cc07243b3a7c0f902403a1836a19ac73da62ec011407c2200"
    "36a13da6342fffd9"
)

_RGB_16x16_97 = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100003070700000000000f636f"
    "6c7201000000000010000001ca6a703263ff4fff51002f0000000000100000001000"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00000001000404040000ff5c001d426f206f006f006ee067506750676850"
    "055005504757d357d35762ff640025000143726561746564206279204f70656e4a50"
    "45472076657273696f6e20322e352e34ff90000a0000000001390001ff93c1f98100"
    "09abc3f60209b8c7f2060879dfc1f98143f602803e704001db0348087fc3f60281f8"
    "010002cb031dc03ea0283f3020005f0439c0fb4160fc01a0fc01400d9679275f0b7f"
    "da7ff4bf10865694c3c1f802c07d40600d0048199f0b3a0ec03e70503ed0500d030b"
    "3c63053fc3ed0d9f987c3ed09024a4c6fc5324082e6fa7c89a3f24cc15d7410afb95"
    "d70e8ff3d6774f236f5da11a3c090253c1f505407c81402219c2e0cdeb34a70c5f03"
    "7f6690cbc01d0e07ce2022187f037fd84b0df38a37c7e0311f80dc3ed11060a6d61b"
    "35e919a51fcd43c62cae15c073b8b1663890007f58291415f06636b805623822a02b"
    "1b185c54fcd4554e6053fd447f52d35cd83861388331c323c8a1880663e3c07c8260"
    "0e8636a19728c3b59b70bf3da633a01f08a03da634527bffd9"
)

_RGB_23x17_TILED = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000011000000170003070700000000000f636f"
    "6c72010000000000100000031d6a703263ff4fff51002f0000000000170000001100"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00000001000404040001ff5c00104040484850484850484850484850ff64"
    "0025000143726561746564206279204f70656e4a5045472076657273696f6e20322e"
    "352e34ff90000a0000000000f10001ff93c7d40406efc7d40209cfb408088fc3ea02"
    "8fb40a01f082008f002f07c7da0507ce0801c7001fc0f90143ea0200043f033fc3ea"
    "0387d4090fb40c0d9cdf0b3667ab10845bc3ea0381f204000cfaed0b37ff7fc07c21"
    "c1f382000cff7f0b3cc7da0d3f00887da0e024aa630226cf24beac699ab52a0f236c"
    "f320e5045bc1f38480f843002218b77b037e5fc03a140f90182219037fbfcfc04a7e"
    "0251f8040060a6d63011d07397c596618c71bc30150f1f580bc6ee69465c9d21faf8"
    "f9916e4cc85f3f52d35cc07243b3a7c0f902403a1836a19ac73da62ec011407c2200"
    "36a13da6342fff90000a0001000001040001ff93c3e70401bfcfb4080437cfb40805"
    "bfc3ea02031bc0f90100057fc3ea02033fc7da073f00483ea03002f3370c3eaa7f01"
    "5c3fc3ea0383e7071f802002ce3f0bb73f0c18027fc07c2141f38200025f0b6fc7da"
    "113f00887da10018ca3d875ea4bc6f0991775b55d784cc077511004890fc7fdf9854"
    "1f3878fc024018d854aefbaf005a1ef0073fe4179aa18718c8b7baaca1325517c03a"
    "140f901017d70376c7da153f00f8fc01c020e57c0e23d2ab3e1e7f2c8cfc5a594d17"
    "25ea32f5f63b0ebb204e669e5b954fcfc0323ed0c8fc01c01ec2af6c1e31e1a2babe"
    "753f22dbb149f0d7af024a14f9012bcf72c6fcec2ea03e10c029210bff90000a0002"
    "000000600001ff93c7d40202c3e704097fc7d40405bfcfc008096fc7da0401c7c0f9"
    "0100043fcfc00c044dc2c3ea020b2bc07c21000b5fc7da0a0b3d86ff7fc1f3820f77"
    "c03a100fbfcfc01010a2ff0fc0f90100183fc0108017ff90000a0003000000440001"
    "ff93c7d40208c3e70203c3e704097f808080c3ea0104df9810077fc07c208003c0f9"
    "01000b0fc7da06030d3bc03a100b7f80cfc0080c5b80ffd9"
)

_GRAY_16x16_PRECINCTS = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100001070700000000000f636f"
    "6c72010000000000110000010a6a703263ff4fff5100290000000000100000001000"
    "00000000000000000000100000001000000000000000000001070101ff5200110100"
    "00010004020200011122334455ff5c00104040484850484850484850484850ff6400"
    "25000143726561746564206279204f70656e4a5045472076657273696f6e20322e35"
    "2e34ff90000a0000000000870001ff93c7d40406efc3ea028fb40a01f082008f002f"
    "07c3ea0387d4090fb40c0d9cdf0b3667ab10845bc7da0d3f00887da0e024aa630226"
    "cf24beac699ab52a0f236cf320e5045bcfc04a7e0251f8040060a6d63011d07397c5"
    "96618c71bc30150f1f580bc6ee69465c9d21faf8f9916e4cc85f3f52d35cc07243b3"
    "a7ffd9"
)

_RGB_16x16_RPCL = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100003070700000000000f636f"
    "6c7201000000000010000001756a703263ff4fff51002f0000000000100000001000"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00020001000404040001ff5c00104040484850484850484850484850ff64"
    "0025000143726561746564206279204f70656e4a5045472076657273696f6e20322e"
    "352e34ff90000a0000000000f10001ff93c7d40406efc7d40209cfb408088fc3ea02"
    "8fb40a01f082008f002f07c7da0507ce0801c7001fc0f90143ea0200043f033fc3ea"
    "0387d4090fb40c0d9cdf0b3667ab10845bc3ea0381f204000cfaed0b37ff7fc07c21"
    "c1f382000cff7f0b3cc7da0d3f00887da0e024aa630226cf24beac699ab52a0f236c"
    "f320e5045bc1f38480f843002218b77b037e5fc03a140f90182219037fbfcfc04a7e"
    "0251f8040060a6d63011d07397c596618c71bc30150f1f580bc6ee69465c9d21faf8"
    "f9916e4cc85f3f52d35cc07243b3a7c0f902403a1836a19ac73da62ec011407c2200"
    "36a13da6342fffd9"
)

_RAW_CODESTREAM_8x8 = bytes.fromhex(
    "ff4fff51002900000000000800000008000000000000000000000008000000080000"
    "0000000000000001070101ff52000c00000001000304040001ff5c000d4040484850"
    "484850484850ff640025000143726561746564206279204f70656e4a504547207665"
    "7273696f6e20322e352e34ff90000a0000000000350001ff93cfb4080567c3ea0187"
    "d40404005fc1f38283e7060d030b3800c07c21c0f9020022187f037e8abfffd9"
)

_RGB_16x16_RCT = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100003070700000000000f636f"
    "6c7201000000000010000001b16a703263ff4fff51002f0000000000100000001000"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00000001010404040001ff5c00104040484850484850484850484850ff64"
    "0025000143726561746564206279204f70656e4a5045472076657273696f6e20322e"
    "352e34ff90000a00000000012d0001ff93cfb408073fc7d40206c3e704017fc7da05"
    "0fa80a0042047f017f07c7da0507ce0805df043fc3ea028fb40a01f08206bf031f07"
    "c3ea0383e70703e7060d9bc10b37b31083bfc3ea0381f203000e09ad0b3d7fc3ea03"
    "87d4070fb40c109d030b814d10845fc1f38787d40f03e70c22092957c3454f24be89"
    "aa4d51e3236cf31eb97fc1f38480f84300216dd117037fbfc7da113f00987da0e024"
    "cc1cd077b154c524c0ed865c4a73a379236cf320e5045bc3ea0f87d41d07d41060a6"
    "d63011cdb7e66cf75abe51157f580bc6ee694ac03d03fd299a919f52d35cc07243b3"
    "9fc0f903403a1835f598ba54773da633cfc0427e0231f8040060aaed29c6eeb563dc"
    "3ec371822ac6f7580bc6ee69465c9d21faf8faf1adfac39352d35cc07243b3a7ffd9"
)

_RGB_16x16_ICT = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000010000000100003070700000000000f636f"
    "6c72010000000000100000023b6a703263ff4fff51002f0000000000100000001000"
    "00000000000000000000100000001000000000000000000003070101070101070101"
    "ff52000c00000001010404040000ff5c001d426f206f006f006ee067506750676850"
    "055005504757d357d35762ff640025000143726561746564206279204f70656e4a50"
    "45472076657273696f6e20322e352e34ff90000a0000000001aa0001ff93c3f60209"
    "72c1f98100086fc1f98100043fc1f98141f9814007c20800bf032309c0fc00a01f50"
    "14007c208009ef06ef04c07da0507e605003e4020587047d08c1f802c0fb41603ea0"
    "400d9bbbbc5f0b35d59eaf106ce6bbc0fb41600f901c03e7060e4689b67f0c29cf0f"
    "ab4bc07d40903ed0581f6828106b21970b92d1b95f10865694bfc1f505c3ed0c83ea"
    "0722208e410af552fccef72924c20373e082d663ef53505f2459675d24fd7fc0f9c5"
    "43ed0781f38621748bbc305cf6d2428f2477d56d152e1f24aef1fd587fc3ed098fc0"
    "320fa81c24cd0ca5e0330cd54d24c2add6050ddee0f1ef3638236f5da11a3c08c1f5"
    "0a43ed1683ea0e60a6d63011b991410548a62f005be15216f78bbf582e13504fae94"
    "119341512a555121ca049eb00eff7f6130556ed407295eadd4e8188b5fc0f9c741f5"
    "0640f9c580614d6176ada340ecd91e4f58d267576caff9dfc149646216c6bf580bb7"
    "20a990e50cbc06d3c3ed1087da2907d41e60a6d601b5ebe51ec1809a6057871efb58"
    "292e5685e8c65e0288ebdd8ded6bd6077b49ef52d35cd83861388331c323c8a18805"
    "ffd9"
)

_RGB_32x24_LOSSY = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000018000000200003070700000000000f636f"
    "6c7201000000000010000000bd6a703263ff4fff51002f0000000000200000001800"
    "00000000000000000000200000001800000000000000000003070101070101070101"
    "ff52000c00000001010404040000ff5c001d426f206f006f006ee067506750676850"
    "055005504757d357d35762ff640025000143726561746564206279204f70656e4a50"
    "45472076657273696f6e20322e352e34ff90000a00000000002c0001ff93c70407d0"
    "c7c6100e5f042480c2200e0dc1100cf680808080808080808080ffd9"
)

_RGB_32x24_LAYERS = bytes.fromhex(
    "0000000c6a5020200d0a870a00000014667479706a703220000000006a7032200000"
    "002d6a703268000000166968647200000018000000200003070700000000000f636f"
    "6c72010000000000100000046b6a703263ff4fff51002f0000000000200000001800"
    "00000000000000000000200000001800000000000000000003070101070101070101"
    "ff52000c00000003010404040001ff5c00104040484850484850484850484850ff64"
    "0025000143726561746564206279204f70656e4a5045472076657273696f6e20322e"
    "352e34ff90000a0000000003e70001ff93dc1007e6d1000e44d1000b7ec4400e0fc8"
    "800d0b80808080808080808080fc0080cff4103980808080808080808080808080fc"
    "e080793ffd606028be2ffde0606df20efd80907ce0e1f681809f8cff7f0b086f0c35"
    "67fde0703e40503e7040fa46210c1b0b06cfc01a3ed039f982800cfcfc97687f0b5f"
    "fd0c4b06507fc7da170fa82e0fa82c15626ebf19dba5c65d033f0dcc83c6c9b9920c"
    "1c2a300e46156594131668f1364fcfc02e1f504c1f5048167cbf48587ccf73f16587"
    "19ce98ec9fd48114d1184c93d9f4db9b4cf7cfc03afcc3e3f00f16cbcce915c8b902"
    "752adeaf003f0e62adc2c8304791b54efd5d5dc4a20dcc57078a9ddfcf6360e3f8f2"
    "f247c7da410fa87e0fa8802c60875fdc99c57e2985dbaa09b66e32d486ba96af0034"
    "ca6b5000b58e7c147f1ebda136443e8f15843b870728a864051d52ab9db007f389db"
    "0c85d0e3d1f5282e126c89bb529f3b50695f154f3b41af149e584311de0fa8ad0572"
    "950ec460cfc06a1f50c43ed1502bb4855fde5e720f6bddb5db35bf0b82157b9e2c98"
    "1bb28b6dcf2d3617fbe0975553cf4c67ccde69d34d433e24ab86fcd0fd2ae657d619"
    "e669e82ca5db35472b7cd4330acbce87cfc09e7e04d1f81300299b4042b164807013"
    "d4e5e1f35b4543c81c045ecffc9fc63f5b97c376a848f85fb972999f49bf1eb96706"
    "1a9444a5413bea021ca28ee37c2e6c348249245005cc38af85a3354cf412b0192d67"
    "2b1f4e8918949514d5139007af811838c6345aec4d82da8a53194e946a335a28b16d"
    "c3f204a5c7da7b0fa8ea3f025047b97dc7ab7007f83bacdeb147b17524efd2465c0e"
    "345403a5ea2f94e9c1715642e5b09e1c1db613b82b5682105ad65b7ca7ccf272b346"
    "95b2499f11a74b8bbcd6bb4e56132ea074b8fc5e3867cd0fad671e63ef0d8d652982"
    "9bc583ca1c6939dd5fe0d57302307e271e7533ff84116fdb95f94e285f0f9517872f"
    "d4f075db30d1e7cd681a3108a262ad28908488e9389e84cd221ab7ff7fff7fff7fcf"
    "c07e3ed1f9f98780486246e7dab39fd3c9afde5f5f051446e1d0c1ff8481bf94225a"
    "287113903f4ca6411c2bd60fa8d9751d1712094c5259bcd65f72d078664f19e94b08"
    "ff7f952d079ce4a940d5e07073290fff7fcfc0f67e0813f324486246e7dab39f52ed"
    "12e769ef6a1d771b381c538c935b6132dd45131e23cc609fa093ab89b234e3f4e5f3"
    "5dd7847d7fff50f7882bc4a416f99f9fff7f4d0d7b41dae231cf11dc5c73a374d991"
    "125d4fb8d4cec303f082ea97eb263ecde8b9d5cae5811403ab8486661f2e6c17bc47"
    "6c559f37eccd901664de147e303b5deb6a8004ca94f9e7daec610b02315f63ff02b8"
    "8763bc954465bcb0a5bf8619ff7fff7fffd9"
)


def _pattern(mode: str, width: int, height: int) -> bytes:
    """The deterministic image every fixture was encoded from."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            red = (x * 7 + y * 13) % 256
            if mode == "L":
                out.append(red)
            else:
                out.extend((red, (x * 11 + y * 3) % 256, (y * 5 + x) % 256))
    return bytes(out)


def _worst(a: bytes, b: bytes) -> int:
    assert len(a) == len(b), f"length {len(a)} != {len(b)}"
    return max((abs(x - y) for x, y in zip(a, b)), default=0)


# ---------------------------------------------------------------------------
# The decoder itself
# ---------------------------------------------------------------------------


def test_tier1_only_image_is_lossless():
    """One resolution level: no wavelet, so this exercises EBCOT alone."""
    image = decode(_GRAY_8x8_NO_DWT)

    assert (image.width, image.height, image.components) == (8, 8, 1)
    assert image.samples == _pattern("L", 8, 8)


def test_reversible_rgb_round_trips_exactly():
    """5/3 wavelet plus the reversible colour transform, end to end."""
    image = decode(_RGB_16x16_53)

    assert (image.width, image.height, image.components) == (16, 16, 3)
    assert image.mode == "RGB"
    assert image.samples == _pattern("RGB", 16, 16)


def test_irreversible_rgb_matches_within_rounding():
    """9/7 wavelet and the irreversible colour transform are lossy by design."""
    image = decode(_RGB_16x16_97)

    assert (image.width, image.height) == (16, 16)
    assert _worst(image.samples, _pattern("RGB", 16, 16)) <= 3


def test_tiled_image_with_partial_edge_tiles():
    """23x17 in 16x16 tiles: the right and bottom tiles are partial."""
    image = decode(_RGB_23x17_TILED)

    assert (image.width, image.height) == (23, 17)
    assert image.samples == _pattern("RGB", 23, 17)


def test_explicit_precincts_and_small_code_blocks():
    image = decode(_GRAY_16x16_PRECINCTS)

    assert image.samples == _pattern("L", 16, 16)


def test_position_progression_with_sop_and_eph_markers():
    """RPCL orders packets by position, and the markers must be stepped over."""
    image = decode(_RGB_16x16_RPCL)

    assert image.samples == _pattern("RGB", 16, 16)


def _with_sop_and_eph(data: bytes) -> bytes:
    """Re-emit a single-packet codestream with SOP and EPH markers added.

    Pillow's encoder cannot produce them, but Kakadu and other producers do, so
    the decoder has to step over them. The markers are inserted around the one
    packet this fixture contains and the ``Scod`` flags are set to declare them.
    """
    codestream = bytearray(extract_codestream(data))
    cod = codestream.find(b"\xff\x52")
    assert cod > 0, "no COD marker"
    codestream[cod + 4] |= 0x06  # Scod: SOP used | EPH used

    sod = codestream.find(b"\xff\x93")
    assert sod > 0, "no SOD marker"
    body_start = sod + 2
    body = bytes(codestream[body_start:])
    if body.endswith(b"\xff\xd9"):
        body, tail = body[:-2], b"\xff\xd9"
    else:
        tail = b""

    header_end = _header_end(data, body)
    sop = b"\xff\x91\x00\x04\x00\x00"
    patched = sop + body[:header_end] + b"\xff\x92" + body[header_end:] + tail

    new_stream = bytes(codestream[:body_start]) + patched
    # Psot in the SOT segment counts from the marker to the end of the tile part.
    sot = new_stream.find(b"\xff\x90")
    assert sot > 0, "no SOT marker"
    length = len(new_stream) - sot - (2 if tail else 0)
    return (
        new_stream[: sot + 6]
        + length.to_bytes(4, "big")
        + new_stream[sot + 10 :]
    )


def _header_end(data: bytes, body: bytes) -> int:
    """Where the single packet's header stops, i.e. where its body begins.

    The fixture holds one code-block in one packet, so the codeword segment the
    decoder read is the tail of the tile data and the header is everything
    before it.
    """
    from aspose_pdf.engine.jpeg2000 import (
        _build_tile,
        _decode_packets,
        extract_codestream,
        parse_codestream,
    )

    cs = parse_codestream(extract_codestream(data))
    tile = _build_tile(cs, 0)
    _decode_packets(tile, cs.cod, b"".join(cs.tile_parts[0]))
    band = tile.components[0].resolutions[0].subbands[0]
    (block,) = band.precincts[0].blocks
    assert len(block.data) == 1, "expected a single codeword segment"
    return len(body) - len(block.data[0])


def test_sop_and_eph_markers_are_stepped_over():
    plain = decode(_GRAY_8x8_NO_DWT)
    marked = decode(_with_sop_and_eph(_GRAY_8x8_NO_DWT))

    assert marked.samples == plain.samples == _pattern("L", 8, 8)


def test_bare_codestream_without_a_jp2_wrapper():
    """A PDF may embed the raw codestream rather than a JP2 file."""
    assert _RAW_CODESTREAM_8x8[:2] == b"\xff\x4f"
    image = decode(_RAW_CODESTREAM_8x8)

    assert image.samples == _pattern("L", 8, 8)


def test_jp2_container_is_unwrapped_to_its_codestream():
    codestream = extract_codestream(_RGB_16x16_53)

    assert codestream[:2] == b"\xff\x4f"
    assert len(codestream) < len(_RGB_16x16_53)


def test_reversible_colour_transform_round_trips_exactly():
    """RCT: the three components are decorrelated losslessly before coding."""
    image = decode(_RGB_16x16_RCT)

    assert image.samples == _pattern("RGB", 16, 16)


def test_irreversible_colour_transform_matches_within_rounding():
    """ICT is the YCbCr transform in floating point, so it is lossy."""
    image = decode(_RGB_16x16_ICT)

    assert _worst(image.samples, _pattern("RGB", 16, 16)) <= 6


def test_a_truncated_code_block_is_reconstructed_at_the_interval_midpoint():
    """Rate control stops a code-block part-way down its bit-planes.

    The remaining value is only known to lie in an interval, and how wide that
    interval is depends on whether decoding stopped after a cleanup pass or
    part-way through a plane. Placing the coefficient at the floor instead of
    the midpoint costs real fidelity: this fixture keeps roughly 3 of 31 coding
    passes on its lowest band, and the floor reconstruction is visibly darker.
    """
    image = decode(_RGB_32x24_LOSSY)

    assert (image.width, image.height) == (32, 24)
    # Fidelity against the *original* is not the test -- at this compression
    # both a floor and a midpoint reconstruction score plausibly against a
    # synthetic pattern. Agreement with an independent decoder is. This digest
    # is the output that matched OpenJPEG to an RMS of 0.04 over the whole
    # image; the floor reconstruction diverges from it by an RMS near 6.
    assert (
        hashlib.sha256(image.samples).hexdigest()
        == "f5f6e74f82b8c6b39b32e0d4f74705841bb1d65baebc50167f978473fba31b5b"
    )


def test_multiple_quality_layers_accumulate_into_one_image():
    """Three layers: a code-block joins in one layer and is refined in later ones.

    Inclusion is signalled through a tag tree whose "not yet" answer has to be
    distinguished from a real value, so a decoder that conflates them drops
    whole code-blocks.
    """
    image = decode(_RGB_32x24_LAYERS)

    assert (image.width, image.height) == (32, 24)
    assert (
        hashlib.sha256(image.samples).hexdigest()
        == "7349d4edf81514b3dba03890b8e2fdc313a5c28800206d24e3ebc902e7d6992e"
    )


# ---------------------------------------------------------------------------
# Refusing what it cannot do, instead of guessing
# ---------------------------------------------------------------------------


def test_input_that_is_not_jpeg_2000_is_rejected():
    with pytest.raises(Jpeg2000Error):
        decode(b"not a codestream at all")


def test_truncated_codestream_is_rejected():
    with pytest.raises(Jpeg2000Error):
        decode(_RGB_16x16_53[:40])


def test_resource_limits_are_enforced_before_decoding():
    limits = PdfLoadLimits(max_image_pixels=4)

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
        decode(_RGB_16x16_53, limits=limits)


def test_decoded_sample_limit_is_enforced():
    limits = PdfLoadLimits(max_decoded_stream_bytes=16)

    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        decode(_RGB_16x16_53, limits=limits)


# ---------------------------------------------------------------------------
# What the page does
# ---------------------------------------------------------------------------


def _page_with_jpx(data: bytes, width: int, height: int) -> SimplePdf:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 32, 32)]
    pdf.page_contents = [b"q 32 0 0 32 0 0 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    xobject = cos.register_object(
        PdfStream(
            data,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(width),
                PdfName("Height"): PdfNumber(height),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("Filter"): PdfName("JPXDecode"),
                PdfName("Length"): PdfNumber(len(data)),
            },
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): xobject})}
    )
    return pdf


def _render(pdf: SimplePdf):
    document = Document()
    document._engine_pdf = pdf
    return document.pages[0].render(antialias=False)


def test_a_jpx_image_is_painted_on_the_page(monkeypatch):
    """With no Pillow at all, the page still shows the picture."""
    import aspose_pdf.engine.jpx as jpx

    monkeypatch.setattr(jpx, "HAS_PILLOW", False)
    raster = _render(_page_with_jpx(_RGB_16x16_53, 16, 16))

    expected = _pattern("RGB", 16, 16)
    # The page is the 16x16 image scaled to 32x32, so sample the middle of a cell.
    for (sx, sy), (px, py) in (((1, 1), (2, 2)), ((8, 4), (17, 9))):
        source = (sy * 16 + sx) * 3
        assert raster.get_pixel(px, py) == tuple(expected[source : source + 3])


def test_an_undecodable_jpx_image_paints_nothing(monkeypatch):
    """The regression that matters: compressed bytes must never become pixels.

    An undecodable filter leaves the raw stream in place, and the rasterizer
    used to run those bytes through the plain sample path -- painting a page of
    noise. It now decodes the codestream itself or draws nothing.
    """
    import aspose_pdf.engine.jpx as jpx

    monkeypatch.setattr(jpx, "HAS_PILLOW", False)
    broken = bytes.fromhex("0000000c6a5020200d0a870a") + bytes(range(256)) * 4
    raster = _render(_page_with_jpx(broken, 16, 16))

    painted = {
        raster.get_pixel(x, y) for y in range(0, 32, 4) for x in range(0, 32, 4)
    }
    assert painted == {(255, 255, 255)}


def test_the_filter_decodes_jpx_without_pillow(monkeypatch):
    """StreamDecoder no longer needs an optional dependency for JPXDecode."""
    import aspose_pdf.engine.jpx as jpx
    from aspose_pdf.engine.filters import StreamDecoder

    monkeypatch.setattr(jpx, "HAS_PILLOW", False)
    samples = StreamDecoder.decode(_GRAY_8x8_NO_DWT, "JPXDecode", None)

    assert samples == _pattern("L", 8, 8)
