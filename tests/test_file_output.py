"""Saving over a file never costs the file that was there.

``Path.write_bytes`` truncates its target before it writes, so saving a
document over the file it came from destroyed the original the moment the
write began: on a volume without room for the new version the save failed with
``ENOSPC`` and left an **empty file** where the only copy had been. Every PDF
save now stages the new bytes beside the target and renames them over it, so a
failure anywhere before that rename leaves the target byte for byte as it was.

The failure is simulated here by making the flush to disk fail -- the point in
the sequence where a full disk or a yanked volume surfaces.
"""

from __future__ import annotations

import errno
import os
import stat
import sys

import pytest

from aspose_pdf import Document
from aspose_pdf.engine import file_output
from aspose_pdf.engine.file_output import write_file_atomically
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.lowcode import FileDataSource

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits and symlinks"
)
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root is not refused by permission bits",
)


def _saved(path, text: str = "v1"):
    document = Document()
    document.pages.add().add_text(text, x=40, y=700)
    document.save(str(path))
    return path


def _disk_full(monkeypatch):
    def full(_descriptor):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(file_output.os, "fsync", full)


def _leftovers(directory) -> list[str]:
    return [name for name in os.listdir(directory) if name.endswith(".tmp")]


# --- a failed save leaves the original alone ------------------------------


@pytest.mark.parametrize("incremental", [False, True], ids=["full", "incremental"])
def test_a_save_that_fails_leaves_the_file_it_was_replacing(
    tmp_path, monkeypatch, incremental
):
    path = _saved(tmp_path / "only-copy.pdf")
    original = path.read_bytes()
    document = Document(str(path))
    document.pages[0].add_text("an edit", x=40, y=600)

    _disk_full(monkeypatch)
    with pytest.raises(OSError) as excinfo:
        document.save(str(path), overwrite=True, incremental=incremental)

    assert excinfo.value.errno == errno.ENOSPC
    assert path.read_bytes() == original
    assert _leftovers(tmp_path) == []


def test_a_write_that_fails_part_way_leaves_the_original(tmp_path, monkeypatch):
    path = tmp_path / "target.bin"
    path.write_bytes(b"the original")

    real_fdopen = os.fdopen

    def half_written(descriptor, *args, **kwargs):
        handle = real_fdopen(descriptor, *args, **kwargs)
        real_write = handle.write

        def write(data):
            real_write(data[: len(data) // 2])
            raise OSError(errno.EIO, "I/O error")

        handle.write = write
        return handle

    monkeypatch.setattr(file_output.os, "fdopen", half_written)
    with pytest.raises(OSError):
        write_file_atomically(path, b"a much longer replacement")

    assert path.read_bytes() == b"the original"
    assert _leftovers(tmp_path) == []


def test_the_low_code_output_is_written_the_same_way(tmp_path, monkeypatch):
    # An Optimizer run "in place" writes its output over its input.
    path = tmp_path / "in-place.pdf"
    path.write_bytes(b"the input")
    _disk_full(monkeypatch)
    with pytest.raises(AsposePdfException):
        FileDataSource(path).write_bytes(b"the optimised output")
    assert path.read_bytes() == b"the input"


# --- a save that succeeds is still a save -----------------------------------


def test_a_save_over_the_source_replaces_it(tmp_path):
    path = _saved(tmp_path / "doc.pdf")
    document = Document(str(path))
    document.pages[0].add_text("v2", x=40, y=600)
    document.save(str(path), overwrite=True)

    assert "v2" in Document(str(path)).pages[0].extract_text()
    assert _leftovers(tmp_path) == []


def test_a_new_file_is_written_where_none_was(tmp_path):
    write_file_atomically(tmp_path / "fresh.bin", b"data")
    assert (tmp_path / "fresh.bin").read_bytes() == b"data"


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_the_file_keeps_its_permissions(tmp_path, mode):
    # A staged file would otherwise arrive as 0600, and a document saved for
    # others to read would quietly stop being readable by them.
    path = tmp_path / "shared.bin"
    path.write_bytes(b"old")
    os.chmod(path, mode)
    write_file_atomically(path, b"new")
    assert stat.S_IMODE(os.stat(path).st_mode) == mode


@posix_only
def test_a_new_file_gets_the_mode_open_would_give_it(tmp_path):
    umask = os.umask(0o022)
    os.umask(umask)
    write_file_atomically(tmp_path / "new.bin", b"data")
    assert stat.S_IMODE(os.stat(tmp_path / "new.bin").st_mode) == 0o666 & ~umask


@posix_only
def test_a_symlink_is_written_through_not_replaced(tmp_path):
    real = tmp_path / "real.bin"
    real.write_bytes(b"old")
    link = tmp_path / "link.bin"
    link.symlink_to(real)

    write_file_atomically(link, b"new")

    assert link.is_symlink()
    assert real.read_bytes() == b"new"


@posix_only
@not_root
def test_a_read_only_file_is_refused_and_left_alone(tmp_path):
    path = tmp_path / "read-only.bin"
    path.write_bytes(b"keep me")
    os.chmod(path, 0o444)
    try:
        with pytest.raises(PermissionError):
            write_file_atomically(path, b"replacement")
        assert path.read_bytes() == b"keep me"
    finally:
        os.chmod(path, 0o644)


@posix_only
@not_root
def test_a_directory_that_takes_no_new_file_falls_back_to_writing_in_place(tmp_path):
    # Nowhere to stage a copy beside the target; the write still happens, the
    # way it always did.
    directory = tmp_path / "locked"
    directory.mkdir()
    path = directory / "doc.bin"
    path.write_bytes(b"old")
    os.chmod(directory, 0o555)
    try:
        write_file_atomically(path, b"new")
        assert path.read_bytes() == b"new"
    finally:
        os.chmod(directory, 0o755)


def test_the_engine_s_cos_save_is_written_the_same_way(tmp_path, monkeypatch):
    path = _saved(tmp_path / "cos.pdf")
    original = path.read_bytes()
    engine = Document(str(path))._engine_pdf
    _disk_full(monkeypatch)
    with pytest.raises(OSError):
        engine.save_cos(path)
    assert path.read_bytes() == original
