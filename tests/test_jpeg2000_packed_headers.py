"""Packed packet headers: a codestream that keeps them out of its packets.

A JPEG 2000 packet normally carries its own header in front of its body. It may
instead gather every header into a PPT segment in the tile header, or a
PPM segment in the main header (ISO 15444-1 A.7.4 and A.7.5), leaving only
the bodies among the packets. This decoder refused such a codestream outright,
so on a default install -- without the images extra and its OpenJPEG -- the
image did not decode at all.

The four codestreams below are the same 32x32 image. The first is what Pillow's
encoder wrote; the others were made from it by moving its packet headers, one
packet at a time, into a PPT segment, a PPM segment, and three PPT
segments numbered so that only their Zppt indices say how to put them back
together. The transformation was checked by rebuilding the original from the same
pieces, which came out byte for byte identical, and **OpenJPEG decodes all four
to the same pixels** -- so the fixtures are valid codestreams and not just ones
this decoder happens to accept.
"""

from __future__ import annotations

import pytest

from aspose_pdf.engine.jpeg2000 import Jpeg2000Error, decode

#: The image as Pillow's encoder wrote it: headers in front of their packets.
_PLAIN = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff90000a0000000000bf0001ff93df80c8116aa1"
    "f0207906f900434f6ddf95f6042402fdd57dc610b53fdf80d0128e2ad0f60adbc0d8"
    "deceead575fe6e6702165c16a1fd0b2a7fdf8140122b66d70f60e552e7471a4d35c4"
    "540af887c1ca4e42fae8bdd16291735b70fe5c41342c15d3603fc1f3870036a199ae"
    "63a35fa0f9c3803da6345b10dc57c0f90340f9030036a199ae639f3da6345b10dbc0"
    "f902805fa7c788b7a07c81409dac294107c07c22c07c23005fa7c7889f9dac2940ff"
    "7fffd9"
)

#: The same packets, with every header moved into one PPT segment.
_PPT = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff90000a0000000000c40001ff61002a00df80c8"
    "df80d0df8140c1f38700a0f9c380c0f90340f90300c0f90280a07c8140c07c22c07c"
    "2300ff93116aa1f0207906f900434f6ddf95f6042402fdd57dc610b53f128e2ad0f6"
    "0adbc0d8deceead575fe6e6702165c16a1fd0b2a7f122b66d70f60e552e7471a4d35"
    "c4540af887c1ca4e42fae8bdd16291735b70fe5c41342c15d3603f36a199ae63a35f"
    "3da6345b10dc5736a199ae639f3da6345b10db5fa7c788b79dac2941075fa7c7889f"
    "9dac2940ff7fffd9"
)

#: The same packets, with every header moved into a PPM segment in the main
#: header, preceded by the Nppm count that says how many bytes belong to the
#: one tile-part that follows.
_PPM = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff60002e0000000027df80c8df80d0df8140c1f3"
    "8700a0f9c380c0f90340f90300c0f90280a07c8140c07c22c07c2300ff90000a0000"
    "000000980001ff93116aa1f0207906f900434f6ddf95f6042402fdd57dc610b53f12"
    "8e2ad0f60adbc0d8deceead575fe6e6702165c16a1fd0b2a7f122b66d70f60e552e7"
    "471a4d35c4540af887c1ca4e42fae8bdd16291735b70fe5c41342c15d3603f36a199"
    "ae63a35f3da6345b10dc5736a199ae639f3da6345b10db5fa7c788b79dac2941075f"
    "a7c7889f9dac2940ff7fffd9"
)

#: The headers split across three PPT segments, written in reverse order so
#: that reassembling them in the order they appear would get it wrong.
_PPT_SPLIT = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff90000a0000000000ce0001ff610010020280a0"
    "7c8140c07c22c07c2300ff61001001a0f9c380c0f90340f90300c0f9ff61001000df"
    "80c8df80d0df8140c1f38700ff93116aa1f0207906f900434f6ddf95f6042402fdd5"
    "7dc610b53f128e2ad0f60adbc0d8deceead575fe6e6702165c16a1fd0b2a7f122b66"
    "d70f60e552e7471a4d35c4540af887c1ca4e42fae8bdd16291735b70fe5c41342c15"
    "d3603f36a199ae63a35f3da6345b10dc5736a199ae639f3da6345b10db5fa7c788b7"
    "9dac2941075fa7c7889f9dac2940ff7fffd9"
)


def _samples(codestream: bytes):
    image = decode(codestream)
    assert (image.width, image.height, image.components) == (32, 32, 3)
    return image.samples


def test_the_plain_codestream_is_the_reference():
    samples = _samples(_PLAIN)
    assert len(samples) == 32 * 32 * 3
    # A gradient, so the corners differ: a blank decode would pass nothing here.
    assert samples[:3] != samples[-3:]


@pytest.mark.parametrize(
    ("name", "codestream"),
    [("PPT", _PPT), ("PPM", _PPM), ("PPT in three segments", _PPT_SPLIT)],
)
def test_headers_packed_away_decode_to_the_same_image(name, codestream):
    assert _samples(codestream) == _samples(_PLAIN), name


def _emptied(codestream: bytes, marker: bytes) -> bytes:
    """*codestream* with the first *marker* segment left with an empty body.

    The first byte of a packed-header segment is its index, so a body-less one
    says nothing about where its bytes belong.
    """
    at = codestream.index(marker)
    return codestream[:at] + marker + b"\x00\x02" + codestream[at + 4 :]


def test_a_ppt_segment_with_no_index_is_refused():
    with pytest.raises(Jpeg2000Error, match="PPT"):
        decode(_emptied(_PPT, b"\xff\x61"))


def test_a_ppm_segment_with_no_index_is_refused():
    with pytest.raises(Jpeg2000Error, match="PPM"):
        decode(_emptied(_PPM, b"\xff\x60"))


#: The PPM stream split over two segments, the second written first, so only
#: the Zppm indices say how to put it back together.
_PPM_SPLIT = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff60001901c0f90340f90300c0f90280a07c8140"
    "c07c22c07c2300ff6000180000000027df80c8df80d0df8140c1f38700a0f9c380ff"
    "90000a0000000000980001ff93116aa1f0207906f900434f6ddf95f6042402fdd57d"
    "c610b53f128e2ad0f60adbc0d8deceead575fe6e6702165c16a1fd0b2a7f122b66d7"
    "0f60e552e7471a4d35c4540af887c1ca4e42fae8bdd16291735b70fe5c41342c15d3"
    "603f36a199ae63a35f3da6345b10dc5736a199ae639f3da6345b10db5fa7c788b79d"
    "ac2941075fa7c7889f9dac2940ff7fffd9"
)

#: One tile in two tile-parts. The PPM stream then carries two Nppm runs,
#: one per tile-part, and the tile's headers are both of them in that order.
_PPM_PARTS = bytes.fromhex(
    "ff4fff51002f00000000002000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000202020001ff5c00"
    "0a4040484850484850ff640025000143726561746564206279204f70656e4a504547"
    "2076657273696f6e20322e352e34ff6000320000000011df80c8df80d0df8140c1f3"
    "8700a0f9c38000000016c0f90340f90300c0f90280a07c8140c07c22c07c2300ff90"
    "000a0000000000770002ff93116aa1f0207906f900434f6ddf95f6042402fdd57dc6"
    "10b53f128e2ad0f60adbc0d8deceead575fe6e6702165c16a1fd0b2a7f122b66d70f"
    "60e552e7471a4d35c4540af887c1ca4e42fae8bdd16291735b70fe5c41342c15d360"
    "3f36a199ae63a35f3da6345b10dc57ff90000a00000000002f0102ff9336a199ae63"
    "9f3da6345b10db5fa7c788b79dac2941075fa7c7889f9dac2940ff7fffd9"
)


def test_a_ppm_stream_in_several_segments_is_ordered_by_its_indices():
    assert _samples(_PPM_SPLIT) == _samples(_PLAIN)


def test_a_tile_in_two_parts_uses_the_run_of_each():
    # Two Nppm runs, and the tile's packets are read from both: taking only the
    # last run would leave the first tile-part's packets without headers.
    assert _samples(_PPM_PARTS) == _samples(_PLAIN)


#: Two tiles, each in one tile-part, with a PPM stream whose two runs follow
#: the tile-parts. 64x32, so the tiles are side by side.
_TILED_PPM = bytes.fromhex(
    "ff4fff51002f00000000004000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000102020001ff5c00"
    "074040484850ff640025000143726561746564206279204f70656e4a504547207665"
    "7273696f6e20322e352e34ff60003e0000000019df8378df8268df85a4c07c2280a0"
    "7c8140cfc12e7e0993f32f0000001acfb5a8df8268df859ac07c2280a07c8140c7da"
    "d33f07b8fc10c0ff90000a00000000026c0001ff93116b32aee5be5c0d89b52a3650"
    "605a52a539b0ff445f361ff117cd87fe130a56cd078922a83924b5d5c251ad17a05a"
    "eae13adf81abc4eb7d6d2ec8cf6015b75f37cdf37f4ae3baf9be6f9be6fe95c775f3"
    "7cdf37cdfd2b8eebe6f9be6f9be7d1dfd523f0e1c008ecb91c4afcf6e3df128eb9aa"
    "89ff47216c8d4e72063f09099c9a4c5f82d3637ddf6a52739ce739ce73dec90d14aa"
    "00000000009fe5410c631ee600005de8c95000000000271d7fff7fc96934d2435708"
    "de066ea23f1150723c6bbf3b2cbbfaf0bec169c0ef179f068b93b29be2ab1ebe02f0"
    "735dc748b5189d6a91d97fa959a23f8c2271f331a27cd90e051fe983341903880846"
    "a2276dc60d5784664e82ca603c7f5a7a883fde5e8ce2bbbe0802ebc64346ff40df60"
    "2b0c9411bef524291e452ba2184ce0daf3ce1af4f2eea365cf6edb0d967f4623ee90"
    "efff1f1ffbbbbb5094ff6faf7f904d632c7be3ebd7effafcf1a986bd452681d262f6"
    "7a23941b149bb26f8bb46907edf2cc6d91b137024495d3dfe30af322b7576912e4ea"
    "2575d09c5121f11a56668c5fa7c7889f9dac29410791f6bf5e38985fbd6faf7f8129"
    "dd0f16dfb07a0e29b76f917ea05888a639587910ecc9b2a08d3c523a1a88e2ef125b"
    "13a3af7a438feff3484110378841b8c355121c0d0ddfd7be3a85ff7f84205cb87fbe"
    "84b520096449e09e6dff5195693705e17b12c4e9c6d0764ed10b7f667737fb98d3f3"
    "6d2a39cb5b1725974af6fc20192872b0b35e0773078805f0dbd3dc6c51a4f91fded7"
    "a2bf9c453daeff07437e552328a3a5cb99a3ce6384b6b5ff865bd090f75d07ab5c6e"
    "df51df7c285f7fff7fff7fff7ff42fff90000a0001000002c30001ff933bb90f2bfb"
    "cc9b9c418168fb11cd87e6c3f361f9b0ff24fdbfe567b7d0c5db341568c6d2fefada"
    "4b4b5914e9756d25a5ac8a74bab6a1aabebc9f8c1b0d86c3618a7f8c1b0d86c3618a"
    "7f8c1b0d86c3618a7f8c1b0d86c3622421cf6c4bbc6beef9c03d89446cdbb7921012"
    "8eb9aa89ff47216c8d4e72063f09099c9a4c5f82d3637ddf6a52739ce739ce73dec9"
    "0d14aa00000000009fe5410c631ee600005de8c95000000000271d7fff7fc96934d2"
    "435708de066ea23f128eb878d63aaa8855e3186812f47afcf1983d315a70c39ff1eb"
    "2016a0c59c7988b7143de11b9d25d6e7608827b20780a68ede7a5681e8eb1eb29259"
    "815653ea2ae3b7aaa364532d3d9c18b6d7c43bd1bf8a218dfdbc919b8b5aebb24bc9"
    "f630bf5b7c67cc010546bb00bdd0c736539b4a4aab2fbda374d93f506ad71637e17f"
    "a9e7466981fdad1aa4d6f21f01d7e13aec9d74e1572bdb7da1eb1a6ea5743adaedba"
    "a19f75b15e7ecec88c51820ad8302db4b71e2c542e0c9640a64728ea3e1c0b9ac6d4"
    "cc51dffa89d695e03f5fa7c7889f9dac2941072f149579e29b39c2aa2345a861c248"
    "59e2d6edb8d123cb2afc1b15c7fde38081ae68a55b1a370504b189a2b0fbc2549eff"
    "5ba09dd987829d8eae3af07b3684e79cd4e0b331a2a08f113d1025598a7597db2ea6"
    "e12f54c191b7a97121a5b131a27247e5ac428354887f034e462b933deca0ddb79cc2"
    "09e5d674eb70bc957441a7aa61cc11ac722712b789cc44741f3839f3f34eec523f86"
    "5a609f888a23d257bfaaa0f2135cc461a391a7692513f85503e8fe9185468828d8ed"
    "27fccb6ba1a37d0b152548eaf43f0ab66892aaac5bee8d405ca092bfb71c73b43f16"
    "8987f9c351683195e31fa9a67f92fa52962e133e476b63f80450cf499d4cdc60d955"
    "c9decbd5c7f2df7f8e791d7f44d5e015b56b6885190ddb287e59e9ada7ff7fff7fff"
    "7fff7fff7fff7d67ffd9"
)

#: The same two tiles with their tile-parts written the other way round -- tile 1
#: first -- and the PPM runs in that same order. A run belongs to the
#: tile-part it follows in the codestream, not to the tile with the lower number.
_TILED_PPM_REVERSED = bytes.fromhex(
    "ff4fff51002f00000000004000000020000000000000000000000020000000200000"
    "0000000000000003070101070101070101ff52000c00000001000102020001ff5c00"
    "074040484850ff640025000143726561746564206279204f70656e4a504547207665"
    "7273696f6e20322e352e34ff60003e000000001acfb5a8df8268df859ac07c2280a0"
    "7c8140c7dad33f07b8fc10c000000019df8378df8268df85a4c07c2280a07c8140cf"
    "c12e7e0993f32fff90000a0001000002c30001ff933bb90f2bfbcc9b9c418168fb11"
    "cd87e6c3f361f9b0ff24fdbfe567b7d0c5db341568c6d2fefada4b4b5914e9756d25"
    "a5ac8a74bab6a1aabebc9f8c1b0d86c3618a7f8c1b0d86c3618a7f8c1b0d86c3618a"
    "7f8c1b0d86c3622421cf6c4bbc6beef9c03d89446cdbb79210128eb9aa89ff47216c"
    "8d4e72063f09099c9a4c5f82d3637ddf6a52739ce739ce73dec90d14aa0000000000"
    "9fe5410c631ee600005de8c95000000000271d7fff7fc96934d2435708de066ea23f"
    "128eb878d63aaa8855e3186812f47afcf1983d315a70c39ff1eb2016a0c59c7988b7"
    "143de11b9d25d6e7608827b20780a68ede7a5681e8eb1eb29259815653ea2ae3b7aa"
    "a364532d3d9c18b6d7c43bd1bf8a218dfdbc919b8b5aebb24bc9f630bf5b7c67cc01"
    "0546bb00bdd0c736539b4a4aab2fbda374d93f506ad71637e17fa9e7466981fdad1a"
    "a4d6f21f01d7e13aec9d74e1572bdb7da1eb1a6ea5743adaedbaa19f75b15e7ecec8"
    "8c51820ad8302db4b71e2c542e0c9640a64728ea3e1c0b9ac6d4cc51dffa89d695e0"
    "3f5fa7c7889f9dac2941072f149579e29b39c2aa2345a861c24859e2d6edb8d123cb"
    "2afc1b15c7fde38081ae68a55b1a370504b189a2b0fbc2549eff5ba09dd987829d8e"
    "ae3af07b3684e79cd4e0b331a2a08f113d1025598a7597db2ea6e12f54c191b7a971"
    "21a5b131a27247e5ac428354887f034e462b933deca0ddb79cc209e5d674eb70bc95"
    "7441a7aa61cc11ac722712b789cc44741f3839f3f34eec523f865a609f888a23d257"
    "bfaaa0f2135cc461a391a7692513f85503e8fe9185468828d8ed27fccb6ba1a37d0b"
    "152548eaf43f0ab66892aaac5bee8d405ca092bfb71c73b43f168987f9c351683195"
    "e31fa9a67f92fa52962e133e476b63f80450cf499d4cdc60d955c9decbd5c7f2df7f"
    "8e791d7f44d5e015b56b6885190ddb287e59e9ada7ff7fff7fff7fff7fff7fff7d67"
    "ff90000a00000000026c0001ff93116b32aee5be5c0d89b52a3650605a52a539b0ff"
    "445f361ff117cd87fe130a56cd078922a83924b5d5c251ad17a05aeae13adf81abc4"
    "eb7d6d2ec8cf6015b75f37cdf37f4ae3baf9be6f9be6fe95c775f37cdf37cdfd2b8e"
    "ebe6f9be6f9be7d1dfd523f0e1c008ecb91c4afcf6e3df128eb9aa89ff47216c8d4e"
    "72063f09099c9a4c5f82d3637ddf6a52739ce739ce73dec90d14aa00000000009fe5"
    "410c631ee600005de8c95000000000271d7fff7fc96934d2435708de066ea23f1150"
    "723c6bbf3b2cbbfaf0bec169c0ef179f068b93b29be2ab1ebe02f0735dc748b5189d"
    "6a91d97fa959a23f8c2271f331a27cd90e051fe983341903880846a2276dc60d5784"
    "664e82ca603c7f5a7a883fde5e8ce2bbbe0802ebc64346ff40df602b0c9411bef524"
    "291e452ba2184ce0daf3ce1af4f2eea365cf6edb0d967f4623ee90efff1f1ffbbbbb"
    "5094ff6faf7f904d632c7be3ebd7effafcf1a986bd452681d262f67a23941b149bb2"
    "6f8bb46907edf2cc6d91b137024495d3dfe30af322b7576912e4ea2575d09c5121f1"
    "1a56668c5fa7c7889f9dac29410791f6bf5e38985fbd6faf7f8129dd0f16dfb07a0e"
    "29b76f917ea05888a639587910ecc9b2a08d3c523a1a88e2ef125b13a3af7a438fef"
    "f3484110378841b8c355121c0d0ddfd7be3a85ff7f84205cb87fbe84b520096449e0"
    "9e6dff5195693705e17b12c4e9c6d0764ed10b7f667737fb98d3f36d2a39cb5b1725"
    "974af6fc20192872b0b35e0773078805f0dbd3dc6c51a4f91fded7a2bf9c453daeff"
    "07437e552328a3a5cb99a3ce6384b6b5ff865bd090f75d07ab5c6edf51df7c285f7f"
    "ff7fff7fff7ff42fffd9"
)


def _tiled_samples(codestream: bytes):
    image = decode(codestream)
    assert (image.width, image.height, image.components) == (64, 32, 3)
    return image.samples


def test_two_tiles_each_get_their_own_run():
    assert _tiled_samples(_TILED_PPM) == _tiled_samples(_TILED_PPM)
    # The tiles differ, so mixing their headers up would be visible.
    left = _tiled_samples(_TILED_PPM)
    assert left[: 3 * 32] != left[3 * 32 : 6 * 32]


def test_a_run_belongs_to_the_tile_part_it_follows():
    # Tile 1 is written first here. Handing the runs out by tile number instead
    # of by the order the tile-parts appear would give each tile the other's
    # packet headers.
    assert _tiled_samples(_TILED_PPM_REVERSED) == _tiled_samples(_TILED_PPM)
