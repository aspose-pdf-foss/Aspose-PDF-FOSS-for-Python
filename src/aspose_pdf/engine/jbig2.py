"""JBIG2 decoder implementation.

The original placeholder only stripped a static header.  This version parses
the JBIG2 file structure, extracts **Immediate Generic Region** segments and,
when required, delegates MMR (Group‑4) bitmap decoding to the ``ccitt``
module.  The public API remains compatible: a ``Decoder`` class exposing a
static ``decode`` method.
"""

import warnings
import struct
from typing import Dict, Optional

# The CCITT Group‑4 decoder is optional – the library can work without it.
try:
    from .ccitt import decode_group4
except Exception:  # pragma: no cover – fallback when the module is missing.
    decode_group4 = None


class Decoder:
    """JBIG2 decoder that parses segment structure and extracts bitmap data.

    The decoder focuses on **Immediate Generic Region** (segment type ``0x08``)
    which commonly contains a raw bitmap that may be compressed with MMR (Group‑4
    CCITT).  For other segment types the implementation currently returns an
    empty ``bytes`` object.
    """

    @staticmethod
    def _read_uint32(data: bytes, offset: int) -> int:
        """Read a big‑endian unsigned 32‑bit integer from *data* at *offset*."""
        return struct.unpack(">I", data[offset : offset + 4])[0]

    @staticmethod
    def _parse_segments(data: bytes):
        """Yield ``(seg_type, seg_data)`` tuples for each JBIG2 segment.

        The parser skips the optional 8‑byte file header and then reads each
        segment consisting of:
        * 4‑byte segment number (ignored)
        * 1‑byte flags
        * 1‑byte segment type
        * 4‑byte data length
        * *data length* bytes of payload
        """
        offset = 0
        header = b"\x97JBIG2\x00\r"
        if data.startswith(header):
            offset = len(header)
        while offset + 6 <= len(data):
            # Segment number (4 bytes) – not used for decoding.
            _ = Decoder._read_uint32(data, offset)
            offset += 4
            _ = data[offset]  # seg_flags
            seg_type = data[offset + 1]
            offset += 2
            if offset + 4 > len(data):
                break
            seg_len = Decoder._read_uint32(data, offset)
            offset += 4
            seg_data = data[offset : offset + seg_len]
            offset += seg_len
            yield seg_type, seg_data
            if offset > len(data):
                break

    @staticmethod
    def _decode_generic_region(seg_data: bytes) -> bytes:
        """Decode an Immediate Generic Region (type ``0x08``) segment.

        The first 13 bytes contain region information.  The least‑significant
        bit of the first byte signals MMR (Group‑4) compression.  When compression
        is indicated and the optional ``decode_group4`` function is available we
        decode the bitmap payload; otherwise the raw payload is returned.
        """
        # Ensure the segment contains the mandatory region header.
        if len(seg_data) < 13:
            return b""

        # Compression flag is the LSB of the first byte.
        mmr_flag = seg_data[0] & 0x01
        # The bitmap data follows the 13‑byte header.
        bitmap_payload = seg_data[13:]

        if mmr_flag and decode_group4:
            # Width and height are stored as big‑endian unsigned 32‑bit values
            # at offsets 5‑8 and 9‑12 respectively.
            width = Decoder._read_uint32(seg_data, 5)
            height = Decoder._read_uint32(seg_data, 9)
            try:
                return decode_group4(bitmap_payload, width, height)
            except Exception as exc:  # pragma: no cover
                warnings.warn(f"CCITT Group‑4 decode failed: {exc}", RuntimeWarning)
                return bitmap_payload
        return bitmap_payload

    @staticmethod
    def decode(data: bytes, params: Optional[Dict] = None) -> bytes:
        """Decode JBIG2 data and return a bitmap.

        Parameters
        ----------
        data : bytes
            Raw JBIG2 byte stream.
        params : dict, optional
            Reserved for future extensions.

        Returns
        -------
        bytes
            Decoded bitmap data. If no suitable segment is found an empty
            ``bytes`` object is returned.
        """
        try:
            for seg_type, seg_data in Decoder._parse_segments(data):
                if seg_type == 0x08:  # Immediate Generic Region
                    return Decoder._decode_generic_region(seg_data)
            warnings.warn(
                "JBIG2 stream contains no Immediate Generic Region segment",
                RuntimeWarning,
            )
            return b""
        except Exception as exc:  # pragma: no cover
            warnings.warn(f"JBIG2 decoding error: {exc}", RuntimeWarning)
            return b""
