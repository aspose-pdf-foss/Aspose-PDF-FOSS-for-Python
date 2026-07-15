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

COLLECTIONS = {
    "CNS1": {
        "directory": "Adobe-CNS1-7",
        "mapping": "Adobe-CNS1-UCS2",
        "cmaps": (
            "UniCNS-UTF16-H",
            "UniCNS-UTF16-V",
            "ETen-B5-H",
            "ETen-B5-V",
        ),
    },
    "GB1": {
        "directory": "Adobe-GB1-6",
        "mapping": "Adobe-GB1-UCS2",
        "cmaps": (
            "UniGB-UTF16-H",
            "UniGB-UTF16-V",
            "GBK-EUC-H",
            "GBK-EUC-V",
        ),
    },
    "Japan1": {
        "directory": "Adobe-Japan1-7",
        "mapping": "Adobe-Japan1-UCS2",
        "cmaps": (
            "UniJIS-UTF16-H",
            "UniJIS-UTF16-V",
            "90ms-RKSJ-H",
            "90ms-RKSJ-V",
        ),
    },
    "Korea1": {
        "directory": "Adobe-Korea1-2",
        "mapping": "Adobe-Korea1-UCS2",
        "cmaps": (
            "UniKS-UTF16-H",
            "UniKS-UTF16-V",
            "KSCms-UHC-H",
            "KSCms-UHC-V",
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


def build(cmap_repo: Path, mapping_repo: Path) -> bytes:
    _verify_checkout(cmap_repo, CMAP_REVISION)
    _verify_checkout(mapping_repo, MAPPING_REVISION)
    payload: dict[str, Any] = {
        "cmaps": {},
        "collections": {},
        "format": 1,
        "sources": {
            "cmap-resources": {
                "revision": CMAP_REVISION,
                "url": "https://github.com/adobe-type-tools/cmap-resources",
            },
            "mapping-resources-pdf": {
                "revision": MAPPING_REVISION,
                "url": "https://github.com/adobe-type-tools/mapping-resources-pdf",
            },
        },
    }
    for ordering, config in COLLECTIONS.items():
        for name in config["cmaps"]:
            relative_path = Path(config["directory"]) / "CMap" / name
            parsed = _parse_cmap(
                _git_blob(cmap_repo, relative_path),
                relative_path.as_posix(),
                name,
            )
            if parsed["ordering"] != ordering:
                raise ValueError(f"Unexpected collection for {name}")
            payload["cmaps"][name] = parsed

        mapping_path = Path("pdf2unicode") / config["mapping"]
        cid_to_text, digest = _parse_cid_unicode(
            _git_blob(mapping_repo, mapping_path),
            mapping_path.as_posix(),
        )
        payload["collections"][ordering] = {
            **_compress_unicode_map(cid_to_text),
            "registry": "Adobe",
            "sha256": digest,
        }

    raw = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return zlib.compress(raw, level=9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmap-repo", type=Path, required=True)
    parser.add_argument("--mapping-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = build(args.cmap_repo, args.mapping_repo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)


if __name__ == "__main__":
    main()
