# Bundled Adobe CMap data

The tables are generated from these official BSD-3-Clause Adobe repositories:

- `adobe-type-tools/cmap-resources`, revision
  `f5cf3bca7fdfeaceb77aa82847e974f2306c20b4`;
- `adobe-type-tools/mapping-resources-pdf`, revision
  `2dd5e53fb74a01718b9dfd448a0d1cce6fff2aa5`.

## Layout

`predefined_cmaps_index.json.zlib` is a small index naming every bundled CMap,
the file that holds it, and that file's SHA-256 and decompressed size. The
tables themselves are split one file per character collection, so a document —
which names exactly one collection through its `CIDSystemInfo` — never
decompresses the other three. Only the index digest is pinned in code
(`predefined_cmaps._INDEX_SHA256`); it in turn pins each collection file.

| Collection | CMaps | File | SHA-256 |
| --- | --- | --- | --- |
| `CNS1` | 36 | `predefined_cmaps_CNS1.json.zlib` | `7d6bf44b1b69c0726a999a646791f739964cf1e536c926563d38bb8119d899b3` |
| `GB1` | 26 | `predefined_cmaps_GB1.json.zlib` | `f6a8ea9a0c00abaf8f161ae1d01d865236980dcf92ad062d6cf72eacb29d9c1d` |
| `Japan1` | 59 | `predefined_cmaps_Japan1.json.zlib` | `686ea874df0bffd022412a064017a07deb205f7c794b58b9eb5beae01bca5dda` |
| `Korea1` | 20 | `predefined_cmaps_Korea1.json.zlib` | `a88dd086d04e271ad4c20d257c45c61430484622af5065ba3aca363267fc4663` |

Index SHA-256:
`6d8564c57ad67b0e0e9929e40591aa8f2e437198f92150dbad4bb47361fef5c2`

Every encoding CMap each collection defines is bundled, in both horizontal and
vertical form, along with that collection's Adobe CID-to-Unicode table. The
`Adobe-<Ordering>-<N>` CMaps are deliberately excluded: their codes already are
CIDs rather than an encoding.

## Regenerating

```shell
python scripts/build_cmap_data.py \
  --cmap-repo /path/to/cmap-resources \
  --mapping-repo /path/to/mapping-resources-pdf \
  --output-dir src/aspose_pdf/engine/data/cmaps
```

The generator rejects a checkout unless it is clean and its `HEAD` is exactly
the pinned revision above. It reads committed Git blobs rather than worktree
files, so platform-specific line-ending conversion cannot change the generated
bytes, and it refuses to emit a collection whose `usecmap` bases are not all in
the same file. The runtime uses an exact allowlist and never accesses the
network or constructs a filesystem path from a PDF CMap name.

See `LICENSE-ADOBE-CMAP.txt` and `LICENSE-ADOBE-MAPPING.txt` for the notices
that apply to the two upstream data sets.
