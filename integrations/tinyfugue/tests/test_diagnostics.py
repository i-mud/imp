from __future__ import annotations

import stat
from pathlib import Path

from tinyscry_tf.diagnostics import DiagnosticCapture


def test_open_creates_a_private_directory_and_file(tmp_path: Path) -> None:
    directory = tmp_path / "diagnostics"
    capture = DiagnosticCapture(directory)

    capture.open()
    capture.write("1700000000 Char.Vitals {}")
    capture.close()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    active = directory / "gmcp.raw"
    assert stat.S_IMODE(active.stat().st_mode) == 0o600
    assert active.read_text(encoding="utf-8") == "1700000000 Char.Vitals {}\n"


def test_rotation_bounds_total_files_and_size(tmp_path: Path) -> None:
    directory = tmp_path / "diagnostics"
    capture = DiagnosticCapture(directory, max_file_bytes=100, max_files=3)
    capture.open()

    for index in range(80):
        capture.write(f"line-{index:04d}")
    capture.close()

    files = sorted(directory.iterdir())
    assert len(files) <= 3
    rotated = [f for f in files if f.name != "gmcp.raw"]
    assert len(rotated) <= 2
    for path in files:
        assert path.stat().st_size <= 100 + len("line-0079\n")


def test_rotated_files_preserve_private_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "diagnostics"
    capture = DiagnosticCapture(directory, max_file_bytes=20, max_files=2)
    capture.open()

    for index in range(10):
        capture.write(f"payload-{index}")
    capture.close()

    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_oldest_rotated_file_is_pruned_beyond_the_configured_count(tmp_path: Path) -> None:
    directory = tmp_path / "diagnostics"
    capture = DiagnosticCapture(directory, max_file_bytes=10, max_files=1)
    capture.open()

    for index in range(20):
        capture.write(f"x{index}")
    capture.close()

    names = {path.name for path in directory.iterdir()}
    assert names == {"gmcp.raw"}
