# Fuzzing

The harness covers four untrusted-input boundaries: the COS tokenizer, full
COS parser, content-stream parser, and stream-filter chain. Every target uses
tight `PdfLoadLimits` so malformed inputs cannot request unbounded parser or
codec work.

Install the optional runner and start one target with its seed corpus:

```bash
python -m pip install -e '.[fuzz]'
python fuzz/run.py cos fuzz/corpus/cos -max_len=1048576
python fuzz/run.py tokenizer fuzz/corpus/tokenizer -max_len=1048576
python fuzz/run.py content fuzz/corpus/content -max_len=1048576
python fuzz/run.py filters fuzz/corpus/filters -max_len=1048576
```

The corpus is also replayed by `tests/test_fuzz_corpus.py`, without requiring
Atheris. All seed files were created for this repository and are distributed
under the project MIT license.
