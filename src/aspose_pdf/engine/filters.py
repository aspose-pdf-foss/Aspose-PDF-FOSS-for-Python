# PDF Stream Filters
import zlib
from typing import Any, Dict, List, Union

from aspose_pdf.exceptions import PDF_STREAM_DECODE_ERRORS, PdfValidationException


try:
    from aspose_pdf.engine.ccitt import Decoder as CCITTDecoder
except ImportError:
    CCITTDecoder = None

try:
    from aspose_pdf.engine.jbig2 import Decoder as JBIG2Decoder
except ImportError:
    JBIG2Decoder = None

try:
    from aspose_pdf.engine.jpx import Decoder as JPXDecoder
except ImportError:
    JPXDecoder = None


class StreamDecoder:
    """Decode PDF stream data using supported filters."""

    @staticmethod
    def _apply_predictor(data: bytes, parms: Union[Dict[str, Any], None]) -> bytes:
        """Apply predictor post‑Flate decompression.
        Supports TIFF (Predictor 2) and PNG (Predictor 10‑15) predictors.
        """
        if not parms:
            return data
        predictor = int(parms.get("Predictor", 1))
        if predictor == 1:
            return data

        columns = int(parms.get("Columns", 1))
        colors = int(parms.get("Colors", 1))
        bits_per_component = int(parms.get("BitsPerComponent", 8))
        bytes_per_pixel = max(1, (colors * bits_per_component + 7) // 8)
        row_len = columns * bytes_per_pixel

        if predictor == 2:
            out = bytearray()
            for row_start in range(0, len(data), row_len):
                row = bytearray(data[row_start : row_start + row_len])
                for i in range(bytes_per_pixel, len(row)):
                    row[i] = (row[i] + row[i - bytes_per_pixel]) & 0xFF
                out.extend(row)
            return bytes(out)

        if 10 <= predictor <= 15:
            out = bytearray()
            prev_row = bytearray([0] * row_len)
            pos = 0
            while pos < len(data):
                filter_type = data[pos]
                pos += 1
                cur_row = bytearray(data[pos : pos + row_len])
                pos += row_len
                if filter_type == 0:  # None
                    recon = cur_row
                elif filter_type == 1:  # Sub
                    recon = bytearray(row_len)
                    for i in range(row_len):
                        left = recon[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                        recon[i] = (cur_row[i] + left) & 0xFF
                elif filter_type == 2:  # Up
                    recon = bytearray(row_len)
                    for i in range(row_len):
                        up = prev_row[i]
                        recon[i] = (cur_row[i] + up) & 0xFF
                elif filter_type == 3:  # Average
                    recon = bytearray(row_len)
                    for i in range(row_len):
                        left = recon[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                        up = prev_row[i]
                        recon[i] = (cur_row[i] + ((left + up) // 2)) & 0xFF
                elif filter_type == 4:  # Paeth
                    recon = bytearray(row_len)
                    for i in range(row_len):
                        a = recon[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                        b = prev_row[i]
                        c = prev_row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                        p = a + b - c
                        pa = abs(p - a)
                        pb = abs(p - b)
                        pc = abs(p - c)
                        if pa <= pb and pa <= pc:
                            pr = a
                        elif pb <= pc:
                            pr = b
                        else:
                            pr = c
                        recon[i] = (cur_row[i] + pr) & 0xFF
                else:
                    recon = cur_row
                out.extend(recon)
                prev_row = recon
            return bytes(out)

        return data

    @staticmethod
    def _decode_ascii85(data: bytes) -> bytes:
        """Decode Adobe ASCII85 / Base85 encoded data."""
        import base64

        s = data.strip()
        if not s.startswith(b"<~"):
            s = b"<~" + s
        if not s.endswith(b"~>"):
            s = s + b"~>"
        return base64.a85decode(s, adobe=True, ignorechars=b" \n\r\t")

    @staticmethod
    def _decode_asciihex(data: bytes) -> bytes:
        """Decode ASCIIHex encoded data."""
        s = data.strip()
        if s.endswith(b">"):
            s = s[:-1]
        s = b"".join(s.split())
        if len(s) % 2:
            s += b"0"
        return bytes.fromhex(s.decode("ascii"))

    @staticmethod
    def _decode_dct(data: bytes) -> bytes:
        """Pass‑through for DCTDecode at the stream-filter level.

        The DCT (JPEG) bytes are the canonical stored form of the image and are
        kept verbatim here so callers can re-emit them losslessly (e.g. export a
        ``.jpg``). To turn a baseline JPEG into raw pixels, use
        :func:`aspose_pdf.engine.dct.decode` (dependency-free) -- the image
        export path does this automatically when Pillow is unavailable.
        """
        return data

    @staticmethod
    def _decode_lzw(data: bytes, parms: Union[Dict[str, Any], None] = None) -> bytes:
        """Decode LZW compressed data per PDF specification.

        LZW is used in older PDFs (PDF 1.0-1.4) for stream compression.
        Implements variable-width codes from 9-12 bits with clear/EOD codes.

        Args:
            data: LZW compressed bytes
            parms: Optional DecodeParms dict with EarlyChange (default 1)

        Returns:
            Decompressed bytes
        """
        if not data:
            return b""

        # EarlyChange: 1 means code length increases one code early (PDF default)
        early_change = 1
        if parms:
            early_change = int(parms.get("EarlyChange", 1))

        # LZW constants
        CLEAR_CODE = 256
        EOD_CODE = 257

        result = bytearray()

        # Bit reader state
        bit_pos = 0
        total_bits = len(data) * 8

        def read_bits(n: int) -> int:
            nonlocal bit_pos
            if bit_pos + n > total_bits:
                return EOD_CODE
            value = 0
            for _ in range(n):
                byte_idx = bit_pos // 8
                bit_idx = 7 - (bit_pos % 8)  # MSB first
                if byte_idx < len(data):
                    value = (value << 1) | ((data[byte_idx] >> bit_idx) & 1)
                bit_pos += 1
            return value

        # Initialize dictionary with single-byte entries
        def init_dict():
            return {i: bytes([i]) for i in range(256)}

        dictionary = init_dict()
        next_code = 258  # First available code after CLEAR and EOD
        code_len = 9
        prev_entry = b""

        while True:
            code = read_bits(code_len)

            if code == EOD_CODE:
                break

            if code == CLEAR_CODE:
                dictionary = init_dict()
                next_code = 258
                code_len = 9
                prev_entry = b""
                continue

            # Get entry for this code
            if code in dictionary:
                entry = dictionary[code]
            elif code == next_code and prev_entry:
                # Special case: code not yet in dictionary
                entry = prev_entry + prev_entry[0:1]
            else:
                raise PdfValidationException(
                    "LZWDecode failed: invalid LZW code in bitstream "
                    "(truncated stream or corrupt compression)"
                )

            result.extend(entry)

            # Add new entry to dictionary
            if prev_entry and next_code < 4096:
                dictionary[next_code] = prev_entry + entry[0:1]
                next_code += 1

                # Increase code length when dictionary reaches threshold
                # EarlyChange=1: increase before reaching 2^n
                threshold = (1 << code_len) - early_change
                if next_code > threshold and code_len < 12:
                    code_len += 1

            prev_entry = entry

        # Apply predictor if specified
        if parms:
            result = bytearray(StreamDecoder._apply_predictor(bytes(result), parms))

        return bytes(result)

    @staticmethod
    def _decode_ccitt(data: bytes, parms: Union[Dict[str, Any], None] = None) -> bytes:
        """Decode CCITTFaxDecode (Group 3/4 fax) encoded data.

        Delegates to aspose_pdf.engine.ccitt.Decoder.
        Falls back to pass-through when decoding fails or produces empty output.
        """
        if not data:
            return b""

        if not CCITTDecoder:
            raise PdfValidationException(
                "CCITTFaxDecode is not available (CCITT decoder module could not be loaded)"
            )

        try:
            result = CCITTDecoder.decode(data, parms or {})
        except PDF_STREAM_DECODE_ERRORS as exc:
            raise PdfValidationException(
                "CCITTFaxDecode failed while decoding the image stream"
            ) from exc

        parms = parms or {}
        k = int(parms.get("K", 0))
        if k >= 0:
            return result

        if not result:
            raise PdfValidationException(
                "CCITTFaxDecode could not produce bitmap data (truncated stream, "
                "invalid Group 4 bitstream, or missing row/column parameters)"
            )
        return result

    @staticmethod
    def _decode_jbig2(data: bytes, parms: Union[Dict[str, Any], None] = None) -> bytes:
        """Decode JBIG2 encoded data.

        Delegates to ``aspose_pdf.engine.jbig2.Decoder``. On failure, raises
        :class:`~aspose_pdf.exceptions.PdfValidationException` so direct callers
        get a predictable error. :meth:`CosExtractor._decode_stream`
        catches stream-decode errors and returns the stream's raw bytes instead.
        """
        if not data:
            return b""

        if not JBIG2Decoder:
            raise PdfValidationException(
                "JBIG2Decode is not available (JBIG2 decoder module could not be loaded)"
            )

        try:
            result = JBIG2Decoder.decode(data, parms or {})
        except PDF_STREAM_DECODE_ERRORS as exc:
            raise PdfValidationException(
                "JBIG2Decode failed while decoding the image stream"
            ) from exc

        if not result:
            raise PdfValidationException(
                "JBIG2Decode could not produce bitmap data (unsupported segments, "
                "truncated stream, or missing optional CCITT support)"
            )
        return result

    @staticmethod
    def _decode_run_length(data: bytes) -> bytes:
        """Decode RunLengthDecode encoded data.

        Run-length encoding uses the following scheme:
        - Length byte 0-127: Copy next (length+1) bytes literally
        - Length byte 129-255: Repeat next byte (257-length) times
        - Length byte 128: End of data

        Args:
            data: RLE encoded bytes

        Returns:
            Decoded bytes
        """
        if not data:
            return b""

        result = bytearray()
        pos = 0

        while pos < len(data):
            length = data[pos]
            pos += 1

            if length == 128:  # EOD marker
                break
            elif length < 128:  # Literal run
                count = length + 1
                end = pos + count
                if end > len(data):
                    raise PdfValidationException(
                        "RunLengthDecode failed: truncated literal run "
                        f"(need {count} bytes at offset {pos - 1})"
                    )
                result.extend(data[pos:end])
                pos = end
            else:  # Repeated byte
                count = 257 - length
                if pos >= len(data):
                    raise PdfValidationException(
                        "RunLengthDecode failed: missing byte after repeat-length "
                        f"({length}) at offset {pos - 1}"
                    )
                result.extend([data[pos]] * count)
                pos += 1

        return bytes(result)

    @staticmethod
    def _decode_jpx(data: bytes, parms: Union[Dict[str, Any], None] = None) -> bytes:
        """Decode JPXDecode (JPEG 2000) stream bytes to raw pixels.

        Failures raise :class:`~aspose_pdf.exceptions.PdfValidationException` for
        direct decode. :meth:`CosExtractor._decode_stream` catches
        stream-decode errors and returns the stream's raw bytes instead.
        """
        if not data:
            return b""

        if not JPXDecoder:
            raise PdfValidationException(
                "JPXDecode is not available (JPX decoder module could not be loaded)"
            )

        return JPXDecoder.decode(data, parms or {})

    @staticmethod
    def decode(data: bytes, filters: Any, decode_parms: Any) -> bytes:
        """Decode ``data`` using ``filters`` and optional ``decode_parms``.

        Supports: FlateDecode (with predictor), LZWDecode, ASCII85Decode,
        ASCIIHexDecode, DCTDecode, CCITTFaxDecode, JBIG2Decode, RunLengthDecode,
        JPXDecode. Unknown filters and /Crypt raise
        :class:`~aspose_pdf.exceptions.PdfValidationException` so
        callers do not get silently wrong bytes.
        ``filters`` may be a single name or a list of names.
        """
        if not filters:
            return data

        if isinstance(filters, (bytes, str)):
            filter_list: List[Any] = [filters]
        else:
            filter_list = list(filters)

        if isinstance(decode_parms, list):
            parms_list = decode_parms
        else:
            parms_list = [decode_parms] * len(filter_list)

        result = data
        for f, p in zip(filter_list, parms_list):
            name = f.decode("latin1") if isinstance(f, (bytes, bytearray)) else str(f)
            name = name.strip().lstrip("/")
            if name == "FlateDecode" or name == "Fl":
                result = zlib.decompress(result)
                result = StreamDecoder._apply_predictor(result, p)
            elif name == "LZWDecode" or name == "LZW":
                result = StreamDecoder._decode_lzw(result, p)
            elif name == "ASCII85Decode" or name == "A85":
                result = StreamDecoder._decode_ascii85(result)
            elif name == "ASCIIHexDecode" or name == "AHx":
                result = StreamDecoder._decode_asciihex(result)
            elif name == "DCTDecode" or name == "DCT":
                result = StreamDecoder._decode_dct(result)
            elif name == "CCITTFaxDecode" or name == "CCF":
                result = StreamDecoder._decode_ccitt(result, p)
            elif name == "JBIG2Decode":
                result = StreamDecoder._decode_jbig2(result, p)
            elif name == "RunLengthDecode" or name == "RL":
                result = StreamDecoder._decode_run_length(result)
            elif name == "JPXDecode":
                result = StreamDecoder._decode_jpx(result, p)
            elif name == "Crypt":
                raise PdfValidationException(
                    "Crypt filter cannot be decoded here: the security handler must "
                    "decrypt the stream before StreamDecoder.decode runs"
                )
            else:
                raise PdfValidationException(
                    f"Unsupported or unknown PDF stream filter: {name!r}"
                )
        return result


# Image / opaque codecs whose *encoding* needs raster (re)compression we do not
# implement here. Callers that already hold encoded image bytes should store
# them verbatim rather than asking StreamEncoder to produce them.
_UNENCODABLE_FILTERS = {
    "DCTDecode": "DCTDecode",
    "DCT": "DCTDecode",
    "CCITTFaxDecode": "CCITTFaxDecode",
    "CCF": "CCITTFaxDecode",
    "JBIG2Decode": "JBIG2Decode",
    "JPXDecode": "JPXDecode",
}


class StreamEncoder:
    """Encode raw bytes into PDF stream data, the inverse of :class:`StreamDecoder`.

    Implements the general-purpose, dependency-free filters: FlateDecode,
    LZWDecode, ASCII85Decode, ASCIIHexDecode and RunLengthDecode. For any filter
    list, ``StreamDecoder.decode(StreamEncoder.encode(data, f, p), f, p)`` returns
    the original ``data``.

    Image codecs (DCTDecode/CCITTFaxDecode/JBIG2Decode/JPXDecode) and ``/Crypt``
    are not encodable here and raise :class:`PdfValidationException` -- callers
    that already hold encoded image bytes should store them verbatim.
    """

    @staticmethod
    def _encode_flate(data: bytes, level: int = 6) -> bytes:
        """Compress with zlib (FlateDecode). Predictors are not applied."""
        return zlib.compress(data, level)

    @staticmethod
    def _encode_ascii85(data: bytes) -> bytes:
        """Encode as Adobe ASCII85 (``<~ ... ~>``)."""
        import base64

        return base64.a85encode(data, adobe=True)

    @staticmethod
    def _encode_asciihex(data: bytes) -> bytes:
        """Encode as ASCIIHex with the ``>`` end-of-data marker."""
        return data.hex().upper().encode("ascii") + b">"

    @staticmethod
    def _encode_run_length(data: bytes) -> bytes:
        """Encode with RunLengthDecode (PackBits-style runs + EOD marker)."""
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            # Length of the run of identical bytes starting at i (cap at 128).
            run = 1
            while i + run < n and run < 128 and data[i + run] == data[i]:
                run += 1
            if run >= 2:
                out.append(257 - run)  # 129..255 -> repeat (257-len) times
                out.append(data[i])
                i += run
                continue
            # Otherwise gather a literal run, stopping before the next >=2 run.
            start = i
            i += 1
            while i < n and (i - start) < 128:
                if i + 1 < n and data[i] == data[i + 1]:
                    break
                i += 1
            out.append((i - start) - 1)  # 0..127 -> copy len+1 literal bytes
            out.extend(data[start:i])
        out.append(128)  # EOD
        return bytes(out)

    @staticmethod
    def _encode_lzw(
        data: bytes, parms: Union[Dict[str, Any], None] = None
    ) -> bytes:
        """Compress with LZWDecode (variable 9-12 bit codes, MSB-first).

        Mirrors :meth:`StreamDecoder._decode_lzw`: ``CLEAR``/``EOD`` markers and
        the same ``EarlyChange`` code-width threshold, so the two round-trip.
        """
        early_change = 1
        if parms:
            early_change = int(parms.get("EarlyChange", 1))

        CLEAR_CODE = 256
        EOD_CODE = 257

        out = bytearray()
        bit_buffer = 0
        bit_count = 0

        def write_code(code: int, width: int) -> None:
            nonlocal bit_buffer, bit_count
            bit_buffer = (bit_buffer << width) | code
            bit_count += width
            while bit_count >= 8:
                bit_count -= 8
                out.append((bit_buffer >> bit_count) & 0xFF)

        def fresh_table():
            return {bytes([i]): i for i in range(256)}

        table = fresh_table()
        next_code = 258
        code_len = 9

        write_code(CLEAR_CODE, code_len)
        current = b""
        for byte in data:
            combined = current + bytes([byte])
            if combined in table:
                current = combined
                continue
            write_code(table[current], code_len)
            if next_code < 4096:
                table[combined] = next_code
                next_code += 1
                # The decoder builds its table one code behind the encoder, so
                # it bumps the code width one step later; widen one code later
                # here (the EarlyChange ``+1``) to stay in sync.
                if next_code > (1 << code_len) - early_change + 1 and code_len < 12:
                    code_len += 1
            if next_code == 4096:
                # Dictionary full: restart so codes stay within 12 bits.
                write_code(CLEAR_CODE, code_len)
                table = fresh_table()
                next_code = 258
                code_len = 9
            current = bytes([byte])

        if current:
            write_code(table[current], code_len)
        write_code(EOD_CODE, code_len)
        if bit_count > 0:
            out.append((bit_buffer << (8 - bit_count)) & 0xFF)
        return bytes(out)

    @staticmethod
    def encode(data: bytes, filters: Any, decode_parms: Any = None) -> bytes:
        """Encode ``data`` so that :meth:`StreamDecoder.decode` reverses it.

        ``filters``/``decode_parms`` use the same shapes as
        :meth:`StreamDecoder.decode`. For a multi-filter list the encoders run in
        reverse order (the decode order is left-to-right), keeping the round-trip
        exact. Unknown filters, image codecs and ``/Crypt`` raise
        :class:`~aspose_pdf.exceptions.PdfValidationException`.
        """
        if not filters:
            return data

        if isinstance(filters, (bytes, str)):
            filter_list: List[Any] = [filters]
        else:
            filter_list = list(filters)

        if isinstance(decode_parms, list):
            parms_list = decode_parms
        else:
            parms_list = [decode_parms] * len(filter_list)

        result = data
        for f, p in zip(reversed(filter_list), reversed(parms_list)):
            name = f.decode("latin1") if isinstance(f, (bytes, bytearray)) else str(f)
            name = name.strip().lstrip("/")
            if name == "FlateDecode" or name == "Fl":
                if p and int(p.get("Predictor", 1)) != 1:
                    raise PdfValidationException(
                        "FlateDecode encoding with a predictor is not supported; "
                        "encode without a predictor (Predictor 1)"
                    )
                result = StreamEncoder._encode_flate(result)
            elif name == "LZWDecode" or name == "LZW":
                if p and int(p.get("Predictor", 1)) != 1:
                    raise PdfValidationException(
                        "LZWDecode encoding with a predictor is not supported; "
                        "encode without a predictor (Predictor 1)"
                    )
                result = StreamEncoder._encode_lzw(result, p)
            elif name == "ASCII85Decode" or name == "A85":
                result = StreamEncoder._encode_ascii85(result)
            elif name == "ASCIIHexDecode" or name == "AHx":
                result = StreamEncoder._encode_asciihex(result)
            elif name == "RunLengthDecode" or name == "RL":
                result = StreamEncoder._encode_run_length(result)
            elif name in _UNENCODABLE_FILTERS:
                canonical = _UNENCODABLE_FILTERS[name]
                raise PdfValidationException(
                    f"{canonical} encoding is not supported "
                    "(store the already-encoded image bytes verbatim instead)"
                )
            elif name == "Crypt":
                raise PdfValidationException(
                    "Crypt filter cannot be encoded here: the security handler "
                    "must encrypt the stream after StreamEncoder.encode runs"
                )
            else:
                raise PdfValidationException(
                    f"Unsupported or unknown PDF stream filter: {name!r}"
                )
        return result
