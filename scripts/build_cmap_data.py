#!/usr/bin/env python3
"""Build the bundled Adobe predefined CMap tables.

The input repositories are build-time sources only. The generated package data
is deterministic and the library never downloads CMap data at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zlib
from pathlib import Path
from typing import Any

CMAP_REVISION = "f5cf3bca7fdfeaceb77aa82847e974f2306c20b4"
MAPPING_REVISION = "2dd5e53fb74a01718b9dfd448a0d1cce6fff2aa5"

# Every encoding CMap each collection defines. The Adobe-<Ordering>-<N>
# CMaps are excluded: they map codes that already are CIDs, not an
# encoding. Each set is closed under usecmap, so a collection file is
# self-contained.
COLLECTIONS = {
    "CNS1": {
        "directory": "Adobe-CNS1-7",
        "mapping": "Adobe-CNS1-UCS2",
        "cmaps": (
            "B5-H",
            "B5-V",
            "B5pc-H",
            "B5pc-V",
            "CNS-EUC-H",
            "CNS-EUC-V",
            "CNS1-H",
            "CNS1-V",
            "CNS2-H",
            "CNS2-V",
            "ETHK-B5-H",
            "ETHK-B5-V",
            "ETen-B5-H",
            "ETen-B5-V",
            "ETenms-B5-H",
            "ETenms-B5-V",
            "HKdla-B5-H",
            "HKdla-B5-V",
            "HKdlb-B5-H",
            "HKdlb-B5-V",
            "HKgccs-B5-H",
            "HKgccs-B5-V",
            "HKm314-B5-H",
            "HKm314-B5-V",
            "HKm471-B5-H",
            "HKm471-B5-V",
            "HKscs-B5-H",
            "HKscs-B5-V",
            "UniCNS-UCS2-H",
            "UniCNS-UCS2-V",
            "UniCNS-UTF16-H",
            "UniCNS-UTF16-V",
            "UniCNS-UTF32-H",
            "UniCNS-UTF32-V",
            "UniCNS-UTF8-H",
            "UniCNS-UTF8-V",
        ),
    },
    "GB1": {
        "directory": "Adobe-GB1-6",
        "mapping": "Adobe-GB1-UCS2",
        "cmaps": (
            "GB-EUC-H",
            "GB-EUC-V",
            "GB-H",
            "GB-V",
            "GBK-EUC-H",
            "GBK-EUC-V",
            "GBK2K-H",
            "GBK2K-V",
            "GBKp-EUC-H",
            "GBKp-EUC-V",
            "GBT-EUC-H",
            "GBT-EUC-V",
            "GBT-H",
            "GBT-V",
            "GBTpc-EUC-H",
            "GBTpc-EUC-V",
            "GBpc-EUC-H",
            "GBpc-EUC-V",
            "UniGB-UCS2-H",
            "UniGB-UCS2-V",
            "UniGB-UTF16-H",
            "UniGB-UTF16-V",
            "UniGB-UTF32-H",
            "UniGB-UTF32-V",
            "UniGB-UTF8-H",
            "UniGB-UTF8-V",
        ),
    },
    "Japan1": {
        "directory": "Adobe-Japan1-7",
        "mapping": "Adobe-Japan1-UCS2",
        "cmaps": (
            "78-EUC-H",
            "78-EUC-V",
            "78-H",
            "78-RKSJ-H",
            "78-RKSJ-V",
            "78-V",
            "78ms-RKSJ-H",
            "78ms-RKSJ-V",
            "83pv-RKSJ-H",
            "90ms-RKSJ-H",
            "90ms-RKSJ-V",
            "90msp-RKSJ-H",
            "90msp-RKSJ-V",
            "90pv-RKSJ-H",
            "90pv-RKSJ-V",
            "Add-H",
            "Add-RKSJ-H",
            "Add-RKSJ-V",
            "Add-V",
            "EUC-H",
            "EUC-V",
            "Ext-H",
            "Ext-RKSJ-H",
            "Ext-RKSJ-V",
            "Ext-V",
            "H",
            "Hankaku",
            "Hiragana",
            "Katakana",
            "NWP-H",
            "NWP-V",
            "RKSJ-H",
            "RKSJ-V",
            "Roman",
            "UniJIS-UCS2-H",
            "UniJIS-UCS2-HW-H",
            "UniJIS-UCS2-HW-V",
            "UniJIS-UCS2-V",
            "UniJIS-UTF16-H",
            "UniJIS-UTF16-V",
            "UniJIS-UTF32-H",
            "UniJIS-UTF32-V",
            "UniJIS-UTF8-H",
            "UniJIS-UTF8-V",
            "UniJIS2004-UTF16-H",
            "UniJIS2004-UTF16-V",
            "UniJIS2004-UTF32-H",
            "UniJIS2004-UTF32-V",
            "UniJIS2004-UTF8-H",
            "UniJIS2004-UTF8-V",
            "UniJISPro-UCS2-HW-V",
            "UniJISPro-UCS2-V",
            "UniJISPro-UTF8-V",
            "UniJISX0213-UTF32-H",
            "UniJISX0213-UTF32-V",
            "UniJISX02132004-UTF32-H",
            "UniJISX02132004-UTF32-V",
            "V",
            "WP-Symbol",
        ),
    },
    "Korea1": {
        "directory": "Adobe-Korea1-2",
        "mapping": "Adobe-Korea1-UCS2",
        "cmaps": (
            "KSC-EUC-H",
            "KSC-EUC-V",
            "KSC-H",
            "KSC-Johab-H",
            "KSC-Johab-V",
            "KSC-V",
            "KSCms-UHC-H",
            "KSCms-UHC-HW-H",
            "KSCms-UHC-HW-V",
            "KSCms-UHC-V",
            "KSCpc-EUC-H",
            "KSCpc-EUC-V",
            "UniKS-UCS2-H",
            "UniKS-UCS2-V",
            "UniKS-UTF16-H",
            "UniKS-UTF16-V",
            "UniKS-UTF32-H",
            "UniKS-UTF32-V",
            "UniKS-UTF8-H",
            "UniKS-UTF8-V",
        ),
    },
}
_HEX = r"[0-9A-Fa-f]+"


def _verify_checkout(path: Path, expected_revision: str) -> None:
    """Require a clean Git checkout at the pinned source revision."""
    try:
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "status",
                "--porcelain",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Cannot verify source checkout {path}") from exc
    if head != expected_revision:
        raise ValueError(
            f"Source checkout {path} is at {head}, expected {expected_revision}"
        )
    if status:
        raise ValueError(f"Source checkout {path} is not clean")


def _git_blob(repo: Path, relative_path: Path) -> bytes:
    """Read canonical source bytes from the pinned Git tree."""
    source = relative_path.as_posix()
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{source}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Cannot read pinned source blob {source}") from exc


def _required_match(pattern: str, text: str, context: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"Missing {context}")
    return match.group(1)


def _blocks(text: str, begin: str, end: str):
    pattern = rf"\b{re.escape(begin)}\b(.*?)\b{re.escape(end)}\b"
    yield from re.findall(pattern, text, flags=re.DOTALL)


def _compress_cid_ranges(mapping: dict[tuple[int, int], int]) -> list[list[int]]:
    out: list[list[int]] = []
    for (length, code), cid in sorted(mapping.items()):
        if out:
            prev = out[-1]
            expected_cid = prev[3] + prev[2] - prev[1] + 1
            if prev[0] == length and code == prev[2] + 1 and cid == expected_cid:
                prev[2] = code
                continue
        out.append([length, code, code, cid])
    return out


def _parse_cmap(data: bytes, source: str, expected_name: str) -> dict[str, Any]:
    text = data.decode("latin-1")
    name = _required_match(r"/CMapName\s+/([^\s]+)\s+def", text, "CMapName")
    if name != expected_name:
        raise ValueError(f"CMap name mismatch in {source}: {name!r}")

    mapping: dict[tuple[int, int], int] = {}
    for block in _blocks(text, "begincidchar", "endcidchar"):
        for code_hex, cid_text in re.findall(rf"<({_HEX})>\s+(\d+)", block):
            if len(code_hex) % 2:
                raise ValueError(f"Odd-length code in {source}")
            mapping[(len(code_hex) // 2, int(code_hex, 16))] = int(cid_text)

    for block in _blocks(text, "begincidrange", "endcidrange"):
        for lo_hex, hi_hex, cid_text in re.findall(
            rf"<({_HEX})>\s+<({_HEX})>\s+(\d+)", block
        ):
            if len(lo_hex) != len(hi_hex) or len(lo_hex) % 2:
                raise ValueError(f"Invalid CID range in {source}")
            lo = int(lo_hex, 16)
            hi = int(hi_hex, 16)
            if hi < lo or hi - lo > 1_000_000:
                raise ValueError(f"Invalid CID range bounds in {source}")
            length = len(lo_hex) // 2
            cid = int(cid_text)
            for offset, code in enumerate(range(lo, hi + 1)):
                mapping[(length, code)] = cid + offset

    codespaces: list[list[int]] = []
    for block in _blocks(text, "begincodespacerange", "endcodespacerange"):
        for lo_hex, hi_hex in re.findall(rf"<({_HEX})>\s+<({_HEX})>", block):
            if len(lo_hex) != len(hi_hex) or len(lo_hex) % 2:
                raise ValueError(f"Invalid codespace range in {source}")
            codespaces.append(
                [len(lo_hex) // 2, int(lo_hex, 16), int(hi_hex, 16)]
            )

    base_match = re.search(r"/([^\s/]+)\s+usecmap\b", text)
    return {
        "base": base_match.group(1) if base_match else None,
        "codespaces": sorted(codespaces),
        "name": name,
        "ordering": _required_match(
            r"/Ordering\s+\(([^)]*)\)\s+def", text, "Ordering"
        ),
        "ranges": _compress_cid_ranges(mapping),
        "registry": _required_match(
            r"/Registry\s+\(([^)]*)\)\s+def", text, "Registry"
        ),
        "sha256": hashlib.sha256(data).hexdigest(),
        "supplement": int(
            _required_match(r"/Supplement\s+(\d+)\s+def", text, "Supplement")
        ),
        "wmode": int(_required_match(r"/WMode\s+(\d+)\s+def", text, "WMode")),
    }


def _decode_utf16_hex(value: str) -> str:
    if len(value) % 2:
        raise ValueError("Odd-length UTF-16 hex string")
    return bytes.fromhex(value).decode("utf-16-be")


def _parse_cid_unicode(data: bytes, source: str) -> tuple[dict[int, str], str]:
    text = data.decode("latin-1")
    mapping: dict[int, str] = {}

    for block in _blocks(text, "beginbfchar", "endbfchar"):
        for src_hex, dst_hex in re.findall(rf"<({_HEX})>\s+<({_HEX})>", block):
            mapping[int(src_hex, 16)] = _decode_utf16_hex(dst_hex)

    for block in _blocks(text, "beginbfrange", "endbfrange"):
        array_ranges = list(
            re.finditer(rf"<({_HEX})>\s+<({_HEX})>\s*\[([^]]*)]", block)
        )
        array_spans = [match.span() for match in array_ranges]
        for match in array_ranges:
            lo = int(match.group(1), 16)
            hi = int(match.group(2), 16)
            values = re.findall(rf"<({_HEX})>", match.group(3))
            for offset, value in enumerate(values[: max(hi - lo + 1, 0)]):
                mapping[lo + offset] = _decode_utf16_hex(value)

        def in_array_span(start: int) -> bool:
            return any(lo <= start < hi for lo, hi in array_spans)

        for match in re.finditer(
            rf"<({_HEX})>\s+<({_HEX})>\s+<({_HEX})>", block
        ):
            if in_array_span(match.start()):
                continue
            lo = int(match.group(1), 16)
            hi = int(match.group(2), 16)
            dst_hex = match.group(3)
            if hi < lo or hi - lo > 1_000_000:
                raise ValueError(f"Invalid Unicode range bounds in {source}")
            width = len(dst_hex) // 2
            first = int(dst_hex, 16)
            for offset, cid in enumerate(range(lo, hi + 1)):
                value = (first + offset).to_bytes(width, "big").hex()
                mapping[cid] = _decode_utf16_hex(value)
    return mapping, hashlib.sha256(data).hexdigest()


def _compress_unicode_map(mapping: dict[int, str]) -> dict[str, list[list[Any]]]:
    ranges: list[list[int]] = []
    values: list[list[Any]] = []
    items = sorted(mapping.items())
    index = 0
    while index < len(items):
        cid, text = items[index]
        if len(text) == 1:
            end = index
            while end + 1 < len(items):
                next_cid, next_text = items[end + 1]
                if (
                    len(next_text) != 1
                    or next_cid != items[end][0] + 1
                    or ord(next_text) != ord(items[end][1]) + 1
                ):
                    break
                end += 1
            if end > index:
                ranges.append([cid, items[end][0], ord(text)])
                index = end + 1
                continue
        values.append([cid, text])
        index += 1
    return {"ranges": ranges, "values": values}


INDEX_NAME = "predefined_cmaps_index.json.zlib"


def _collection_file(ordering: str) -> str:
    return f"predefined_cmaps_{ordering}.json.zlib"


def _serialize(payload: dict[str, Any]) -> tuple[bytes, int]:
    """Return ``(compressed, uncompressed_length)`` deterministically."""
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return zlib.compress(raw, level=9), len(raw)


def build(cmap_repo: Path, mapping_repo: Path) -> dict[str, bytes]:
    """Return ``{filename: bytes}`` for the index and one file per collection.

    The tables are split per collection so the runtime loads only the one a
    document's ``CIDSystemInfo`` names. A single file holding every collection
    would make one CJK document pay for all four.
    """
    _verify_checkout(cmap_repo, CMAP_REVISION)
    _verify_checkout(mapping_repo, MAPPING_REVISION)
    sources = {
        "cmap-resources": {
            "revision": CMAP_REVISION,
            "url": "https://github.com/adobe-type-tools/cmap-resources",
        },
        "mapping-resources-pdf": {
            "revision": MAPPING_REVISION,
            "url": "https://github.com/adobe-type-tools/mapping-resources-pdf",
        },
    }
    outputs: dict[str, bytes] = {}
    index: dict[str, Any] = {"collections": {}, "format": 2, "sources": sources}

    for ordering, config in COLLECTIONS.items():
        cmaps: dict[str, Any] = {}
        for name in config["cmaps"]:
            relative_path = Path(config["directory"]) / "CMap" / name
            parsed = _parse_cmap(
                _git_blob(cmap_repo, relative_path),
                relative_path.as_posix(),
                name,
            )
            if parsed["ordering"] != ordering:
                raise ValueError(f"Unexpected collection for {name}")
            cmaps[name] = parsed

        # A collection file must be self-contained: every usecmap base has to
        # live beside the CMap that names it.
        for name, parsed in cmaps.items():
            base = parsed.get("base")
            if base is not None and base not in cmaps:
                raise ValueError(
                    f"{name} uses {base}, which is outside collection {ordering}"
                )

        mapping_path = Path("pdf2unicode") / config["mapping"]
        cid_to_text, digest = _parse_cid_unicode(
            _git_blob(mapping_repo, mapping_path),
            mapping_path.as_posix(),
        )
        compressed, output_bytes = _serialize(
            {
                "cmaps": cmaps,
                "collection": {
                    **_compress_unicode_map(cid_to_text),
                    "registry": "Adobe",
                    "sha256": digest,
                },
                "format": 2,
                "ordering": ordering,
            }
        )
        filename = _collection_file(ordering)
        outputs[filename] = compressed
        index["collections"][ordering] = {
            "cmaps": sorted(cmaps),
            "compressed_bytes": len(compressed),
            "file": filename,
            "output_bytes": output_bytes,
            "sha256": hashlib.sha256(compressed).hexdigest(),
        }

    outputs[INDEX_NAME] = _serialize(index)[0]
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmap-repo", type=Path, required=True)
    parser.add_argument("--mapping-repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    outputs = build(args.cmap_repo, args.mapping_repo)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in sorted(outputs.items()):
        (args.output_dir / filename).write_bytes(data)
        print(
            f"{filename}: {len(data)} bytes "
            f"sha256={hashlib.sha256(data).hexdigest()}"
        )


if __name__ == "__main__":
    main()
