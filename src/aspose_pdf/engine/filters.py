# PDF Stream Filters
import zlib
from typing import Any

from aspose_pdf.exceptions import (
    PDF_STREAM_DECODE_ERRORS,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits

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
    def _effective_output_limit(
        limits: PdfLoadLimits, max_output_bytes: int | None
    ) -> int | None:
        candidates = [
            value
            for value in (limits.max_decoded_stream_bytes, max_output_bytes)
            if value is not None
        ]
        return min(candidates) if candidates else None

    @staticmethod
    def _check_output(
        data: bytes,
        *,
        input_size: int,
        original_input_size: int,
        limits: PdfLoadLimits,
        max_output_bytes: int | None,
        filter_name: str,
    ) -> None:
        output_limit = StreamDecoder._effective_output_limit(
            limits, max_output_bytes
        )
        if output_limit is not None and len(data) > output_limit:
            raise PdfResourceLimitException(
                f"Resource limit exceeded for {filter_name} output: {len(data)} "
                f"exceeds max_decoded_stream_bytes={output_limit}"
            )
        ratio = limits.max_compression_ratio
        if ratio is not None and input_size > 0 and len(data) > input_size * ratio:
            raise PdfResourceLimitException(
                f"Resource limit exceeded for {filter_name} compression ratio: "
                f"{len(data)} decoded bytes from {input_size} encoded bytes exceeds "
                f"max_compression_ratio={ratio}"
            )
        if (
            ratio is not None
            and original_input_size > 0
            and len(data) > original_input_size * ratio
        ):
            raise PdfResourceLimitException(
                f"Resource limit exceeded for {filter_name} filter-chain "
                f"compression ratio: {len(data)} decoded bytes from "
                f"{original_input_size} encoded bytes exceeds "
                f"max_compression_ratio={ratio}"
            )

    @staticmethod
    def _decode_flate(
        data: bytes,
        max_output_bytes: int | None,
        output_limit_name: str = "max_decoded_stream_bytes",
    ) -> bytes:
        """Decode one zlib stream without allocating beyond the output cap."""
        if max_output_bytes is None:
            return zlib.decompress(data)
        decoder = zlib.decompressobj()
        result = decoder.decompress(data, max_output_bytes + 1)
        if len(result) > max_output_bytes or decoder.unconsumed_tail:
            raise PdfResourceLimitException(
                "Resource limit exceeded for FlateDecode output: decoded data "
                f"exceeds {output_limit_name}={max_output_bytes}"
            )
        result += decoder.flush(max_output_bytes + 1 - len(result))
        if len(result) > max_output_bytes:
            raise PdfResourceLimitException(
                "Resource limit exceeded for FlateDecode output: decoded data "
                f"exceeds {output_limit_name}={max_output_bytes}"
            )
        if not decoder.eof:
            raise zlib.error("incomplete or truncated zlib stream")
        return result

    @staticmethod
    def _apply_predictor(
        data: bytes,
        parms: dict[str, Any] | None,
        max_output_bytes: int | None = None,
    ) -> bytes:
        """Apply predictor post-Flate decompression.
        Supports TIFF (Predictor 2) and PNG (Predictor 10-15) predictors.
        """
        if not parms:
            return data
        predictor = int(parms.get("Predictor", 1))
        if predictor == 1:
            return data

        columns = int(parms.get("Columns", 1))
        colors = int(parms.get("Colors", 1))
        bits_per_component = int(parms.get("BitsPerComponent", 8))
        if columns <= 0 or colors <= 0:
            raise PdfValidationException(
                "Predictor Columns and Colors must be positive integers"
            )
        if bits_per_component not in {1, 2, 4, 8, 16}:
            raise PdfValidationException(
                "Predictor BitsPerComponent must be one of 1, 2, 4, 8, or 16"
            )
        bytes_per_pixel = max(1, (colors * bits_per_component + 7) // 8)
        row_len = (columns * colors * bits_per_component + 7) // 8
        if max_output_bytes is not None and row_len > max_output_bytes:
            raise PdfResourceLimitException(
                "Resource limit exceeded for predictor row: "
                f"{row_len} exceeds max_decoded_stream_bytes={max_output_bytes}"
            )

        if predictor == 2:
            out = bytearray()
            for row_start in range(0, len(data), row_len):
                row = bytearray(data[row_start : row_start + row_len])
                for i in range(bytes_per_pixel, len(row)):
                    row[i] = (row[i] + row[i - bytes_per_pixel]) & 0xFF
                out.extend(row)
            return bytes(out)

        if 10 <= predictor <= 15:
            if data and row_len + 1 > len(data):
                raise PdfValidationException(
                    "PNG predictor row is larger than the decoded stream"
                )
            out = bytearray()
            prev_row = bytearray(row_len)
            pos = 0
            while pos < len(data):
                if len(data) - pos < row_len + 1:
                    raise PdfValidationException(
                        "PNG predictor stream ends in a partial row"
                    )
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
    def _decode_ascii85(
        data: bytes, max_output_bytes: int | None = None
    ) -> bytes:
        """Decode Adobe ASCII85 / Base85 data after bounding its output."""
        import base64

        start = 0
        end = len(data)
        whitespace = b" \n\r\t"
        while start < end and data[start] in whitespace:
            start += 1
        while end > start and data[end - 1] in whitespace:
            end -= 1
        if data[start : start + 2] == b"<~":
            start += 2
        if data[max(start, end - 2) : end] == b"~>":
            end -= 2

        regular_chars = 0
        zero_groups = 0
        for value in data[start:end]:
            if value in whitespace:
                continue
            if value == ord("z"):
                zero_groups += 1
            else:
                regular_chars += 1
        remainder = regular_chars % 5
        decoded_upper_bound = (
            zero_groups * 4
            + (regular_chars // 5) * 4
            + max(0, remainder - 1)
        )
        if (
            max_output_bytes is not None
            and decoded_upper_bound > max_output_bytes
        ):
            raise PdfResourceLimitException(
                "Resource limit exceeded for ASCII85Decode output: decoded data "
                f"exceeds max_decoded_stream_bytes={max_output_bytes}"
            )

        payload = data[start:end]
        return base64.a85decode(
            payload,
            adobe=False,
            ignorechars=whitespace,
        )

    @staticmethod
    def _decode_asciihex(
        data: bytes, max_output_bytes: int | None = None
    ) -> bytes:
        """Decode ASCIIHex data incrementally without crossing the output cap."""
        out = bytearray()
        high_nibble: int | None = None
        terminated = False
        whitespace = b" \n\r\t\f\x00"

        for value in data:
            if value in whitespace:
                continue
            if value == ord(">"):
                terminated = True
                continue
            if terminated:
                raise ValueError("non-whitespace data follows ASCIIHex end marker")
            if ord("0") <= value <= ord("9"):
                nibble = value - ord("0")
            elif ord("A") <= value <= ord("F"):
                nibble = value - ord("A") + 10
            elif ord("a") <= value <= ord("f"):
                nibble = value - ord("a") + 10
            else:
                raise ValueError(f"invalid ASCIIHex digit: {chr(value)!r}")

            if high_nibble is None:
                high_nibble = nibble
                continue
            if max_output_bytes is not None and len(out) >= max_output_bytes:
                raise PdfResourceLimitException(
                    "Resource limit exceeded for ASCIIHexDecode output: decoded "
                    f"data exceeds max_decoded_stream_bytes={max_output_bytes}"
                )
            out.append((high_nibble << 4) | nibble)
            high_nibble = None

        if high_nibble is not None:
            if max_output_bytes is not None and len(out) >= max_output_bytes:
                raise PdfResourceLimitException(
                    "Resource limit exceeded for ASCIIHexDecode output: decoded "
                    f"data exceeds max_decoded_stream_bytes={max_output_bytes}"
                )
            out.append(high_nibble << 4)
        return bytes(out)

    @staticmethod
    def _decode_dct(data: bytes) -> bytes:
        """Pass-through for DCTDecode at the stream-filter level.

        The DCT (JPEG) bytes are the canonical stored form of the image and are
        kept verbatim here so callers can re-emit them losslessly (e.g. export a
        ``.jpg``). To turn a baseline JPEG into raw pixels, use
        :func:`aspose_pdf.engine.dct.decode` (dependency-free) -- the image
        export path does this automatically when Pillow is unavailable.
        """
        return data

    @staticmethod
    def _decode_lzw(
        data: bytes,
        parms: dict[str, Any] | None = None,
        max_output_bytes: int | None = None,
    ) -> bytes:
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

            if (
                max_output_bytes is not None
                and len(result) + len(entry) > max_output_bytes
            ):
                raise PdfResourceLimitException(
                    "Resource limit exceeded for LZWDecode output: decoded data "
                    f"exceeds max_decoded_stream_bytes={max_output_bytes}"
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
            result = bytearray(
                StreamDecoder._apply_predictor(
                    bytes(result), parms, max_output_bytes
                )
            )

        return bytes(result)

    @staticmethod
    def _decode_ccitt(
        data: bytes,
        parms: dict[str, Any] | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> bytes:
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
            if limits is None:
                result = CCITTDecoder.decode(data, parms or {})
            else:
                result = CCITTDecoder.decode(data, parms or {}, limits=limits)
        except PdfResourceLimitException:
            raise
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
    def _decode_jbig2(
        data: bytes,
        parms: dict[str, Any] | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> bytes:
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
            if limits is None:
                result = JBIG2Decoder.decode(data, parms or {})
            else:
                result = JBIG2Decoder.decode(data, parms or {}, limits=limits)
        except PdfResourceLimitException:
            raise
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
    def _decode_run_length(
        data: bytes, max_output_bytes: int | None = None
    ) -> bytes:
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
                if (
                    max_output_bytes is not None
                    and len(result) + count > max_output_bytes
                ):
                    raise PdfResourceLimitException(
                        "Resource limit exceeded for RunLengthDecode output: "
                        f"decoded data exceeds max_decoded_stream_bytes={max_output_bytes}"
                    )
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
                if (
                    max_output_bytes is not None
                    and len(result) + count > max_output_bytes
                ):
                    raise PdfResourceLimitException(
                        "Resource limit exceeded for RunLengthDecode output: "
                        f"decoded data exceeds max_decoded_stream_bytes={max_output_bytes}"
                    )
                if pos >= len(data):
                    raise PdfValidationException(
                        "RunLengthDecode failed: missing byte after repeat-length "
                        f"({length}) at offset {pos - 1}"
                    )
                result.extend([data[pos]] * count)
                pos += 1

        return bytes(result)

    @staticmethod
    def _decode_jpx(
        data: bytes,
        parms: dict[str, Any] | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> bytes:
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

        if limits is None:
            return JPXDecoder.decode(data, parms or {})
        return JPXDecoder.decode(data, parms or {}, limits=limits)

    @staticmethod
    def decode(
        data: bytes,
        filters: Any,
        decode_parms: Any,
        *,
        limits: PdfLoadLimits | None = None,
        max_output_bytes: int | None = None,
    ) -> bytes:
        """Decode ``data`` using ``filters`` and optional ``decode_parms``.

        Supports: FlateDecode (with predictor), LZWDecode, ASCII85Decode,
        ASCIIHexDecode, DCTDecode, CCITTFaxDecode, JBIG2Decode, RunLengthDecode,
        JPXDecode. Unknown filters and /Crypt raise
        :class:`~aspose_pdf.exceptions.PdfValidationException` so
        callers do not get silently wrong bytes.
        ``filters`` may be a single name or a list of names.
        """
        resolved_limits = _coerce_limits(limits)
        original_input_size = len(data)
        if not filters:
            StreamDecoder._check_output(
                data,
                input_size=len(data),
                original_input_size=original_input_size,
                limits=resolved_limits,
                max_output_bytes=max_output_bytes,
                filter_name="unfiltered stream",
            )
            return data

        if isinstance(filters, (bytes, str)):
            filter_list: list[Any] = [filters]
        else:
            filter_list = list(filters)

        filter_limit = resolved_limits.max_stream_filters
        if filter_limit is not None and len(filter_list) > filter_limit:
            raise PdfResourceLimitException(
                f"Resource limit exceeded for stream filter chain: "
                f"{len(filter_list)} exceeds max_stream_filters={filter_limit}"
            )

        if isinstance(decode_parms, list):
            parms_list = list(decode_parms)
        else:
            parms_list = [decode_parms] * len(filter_list)
        if len(parms_list) < len(filter_list):
            parms_list.extend([None] * (len(filter_list) - len(parms_list)))

        result = data
        for f, p in zip(filter_list, parms_list):
            name = f.decode("latin1") if isinstance(f, (bytes, bytearray)) else str(f)
            name = name.strip().lstrip("/")
            input_size = len(result)
            output_limit = StreamDecoder._effective_output_limit(
                resolved_limits, max_output_bytes
            )
            output_limit_name = "max_decoded_stream_bytes"
            if max_output_bytes is not None and (
                resolved_limits.max_decoded_stream_bytes is None
                or max_output_bytes <= resolved_limits.max_decoded_stream_bytes
            ):
                output_limit_name = "max_output_bytes"
            ratio = resolved_limits.max_compression_ratio
            if ratio is not None and input_size > 0:
                ratio_limit = input_size * ratio
                if output_limit is None or ratio_limit <= output_limit:
                    output_limit_name = "max_compression_ratio"
                output_limit = (
                    ratio_limit
                    if output_limit is None
                    else min(output_limit, ratio_limit)
                )
            if ratio is not None and original_input_size > 0:
                chain_ratio_limit = original_input_size * ratio
                if output_limit is None or chain_ratio_limit <= output_limit:
                    output_limit_name = "max_compression_ratio"
                output_limit = (
                    chain_ratio_limit
                    if output_limit is None
                    else min(output_limit, chain_ratio_limit)
                )
            if name == "FlateDecode" or name == "Fl":
                result = StreamDecoder._decode_flate(
                    result, output_limit, output_limit_name
                )
                result = StreamDecoder._apply_predictor(result, p, output_limit)
            elif name == "LZWDecode" or name == "LZW":
                result = StreamDecoder._decode_lzw(result, p, output_limit)
            elif name == "ASCII85Decode" or name == "A85":
                result = StreamDecoder._decode_ascii85(result, output_limit)
            elif name == "ASCIIHexDecode" or name == "AHx":
                result = StreamDecoder._decode_asciihex(result, output_limit)
            elif name == "DCTDecode" or name == "DCT":
                result = StreamDecoder._decode_dct(result)
            elif name == "CCITTFaxDecode" or name == "CCF":
                result = StreamDecoder._decode_ccitt(result, p, resolved_limits)
            elif name == "JBIG2Decode":
                result = StreamDecoder._decode_jbig2(result, p, resolved_limits)
            elif name == "RunLengthDecode" or name == "RL":
                result = StreamDecoder._decode_run_length(result, output_limit)
            elif name == "JPXDecode":
                result = StreamDecoder._decode_jpx(result, p, resolved_limits)
            elif name == "Crypt":
                raise PdfValidationException(
                    "Crypt filter cannot be decoded here: the security handler must "
                    "decrypt the stream before StreamDecoder.decode runs"
                )
            else:
                raise PdfValidationException(
                    f"Unsupported or unknown PDF stream filter: {name!r}"
                )
            StreamDecoder._check_output(
                result,
                input_size=input_size,
                original_input_size=original_input_size,
                limits=resolved_limits,
                max_output_bytes=max_output_bytes,
                filter_name=name,
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
        data: bytes, parms: dict[str, Any] | None = None
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
            filter_list: list[Any] = [filters]
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
