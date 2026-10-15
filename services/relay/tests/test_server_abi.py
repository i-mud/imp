from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-server-abi.py"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_server_abi", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


abi = load_checker()

HEADER = """ELF Header:
  Class:                             ELF64
  Data:                              2's complement, little endian
  Machine:                           {machine}
"""
PROGRAM = "      [Requesting program interpreter: {interp}]\n"
DYNAMIC = " 0x0000000000000001 (NEEDED)             Shared library: [{lib}]\n"
VERSIONS = """Version symbols section '.gnu.version' contains 3 entries:
 Addr: 0x0000000000000a00  Offset: 0x000a00  Link: 6 (.dynsym)
  000:   0 (*local*)       2 (GLIBC_2.99)

Version definition section '.gnu.version_d' contains 1 entry:
 Addr: 0x0000000000000a20  Offset: 0x000a20  Link: 7 (.dynstr)
  000000: Rev: 1  Flags: none  Index: 1  Cnt: 1  Name: GLIBC_2.99

Version needs section '.gnu.version_r' contains 1 entry:
 Addr: 0x0000000000000a40  Offset: 0x000a40  Link: 7 (.dynstr)
  000000: Version: 1  File: {lib}  Cnt: 2
  0x0010:   Name: GLIBC_2.2.5  Flags: none  Version: 3
  0x0020:   Name: {tag}  Flags: none  Version: 2
"""
X86_64 = "Advanced Micro Devices X86-64"


def record(
    machine: str = X86_64, interp: str = abi.LOADER, lib: str = "libc.so.6", tag: str = "GLIBC_2.36"
) -> dict[str, Any]:
    parsed: dict[str, Any] = abi.parse(
        HEADER.format(machine=machine),
        PROGRAM.format(interp=interp),
        DYNAMIC.format(lib=lib),
        VERSIONS.format(lib=lib, tag=tag),
    )
    return parsed


def test_baseline_ceiling_is_inclusive() -> None:
    assert abi.violations(record(), set()) == []
    assert abi.violations(record(tag="GLIBC_2.37"), set()) != []


def test_only_version_needs_count_not_exports() -> None:
    assert record()["version_needs"] == {"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.36"]}
    assert abi.violations(record(), set()) == []


@pytest.mark.parametrize(
    ("lib", "tag", "ok"),
    [
        ("libstdc++.so.6", "GLIBCXX_3.4.30", True),
        ("libstdc++.so.6", "GLIBCXX_3.4.31", False),
        ("libstdc++.so.6", "CXXABI_1.3.13", True),
        ("libstdc++.so.6", "CXXABI_1.3.14", False),
        ("libgcc_s.so.1", "GCC_12.0.0", True),
        ("libgcc_s.so.1", "GCC_13.0.0", False),
        ("libz.so.1", "ZLIB_1.2.13", True),
        ("libz.so.1", "ZLIB_1.3", False),
        ("libc.so.6", "GLIBC_PRIVATE", False),
    ],
)
def test_toolchain_version_ceilings(lib: str, tag: str, ok: bool) -> None:
    assert (abi.violations(record(lib=lib, tag=tag), set()) == []) is ok


def test_architecture_and_loader_are_enforced() -> None:
    assert abi.violations(record(machine="AArch64"), set()) != []
    assert abi.violations(record(interp="/lib/ld-musl-x86_64.so.1"), set()) != []
    assert abi.violations(abi.parse(HEADER.format(machine=X86_64), "", "", ""), set()) == []


def test_unknown_library_is_rejected_unless_shipped() -> None:
    unknown = record(lib="libssl.so.3", tag="OPENSSL_3.0.0")
    assert abi.violations(unknown, set()) != []
    assert abi.violations(unknown, {"libssl.so.3"}) == []


needs_readelf = pytest.mark.skipif(
    platform.machine() != "x86_64" or shutil.which("readelf") is None, reason="needs x86_64 GNU readelf"
)


@needs_readelf
def test_real_readelf_output_parses_and_scan_dedupes_inodes(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    shutil.copy(Path(sys.executable).resolve(), tree / "one")
    (tree / "two").hardlink_to(tree / "one")
    (tree / "text").write_text("not elf")
    (tree / "link").symlink_to("one")

    found = abi.describe(str(tree / "one"))
    assert found["machine"] == X86_64
    assert found["needed"]

    records, _ = abi.scan(tree)
    assert [r["paths"] for r in records] == [["one", "two"]]


@needs_readelf
@pytest.mark.parametrize(
    ("offset_of", "old", "new", "message"),
    [
        (None, b"\x3e\x00", b"\xb7\x00", "unsupported architecture"),  # e_machine: x86-64 -> AArch64
        (b"/lib64/ld-linux-x86-64.so.2\0", b"x86-64", b"x86-65", "unsupported loader"),
        (b"\0GLIBC_2.2.5\0", b"2.2.5", b"9.9.9", "version above baseline: libc.so.6 GLIBC_9.9.9"),
    ],
)
def test_cli_rejects_mutated_real_elf(
    tmp_path: Path, offset_of: bytes | None, old: bytes, new: bytes, message: str
) -> None:
    data = bytearray(Path(sys.executable).resolve().read_bytes())
    assert len(old) == len(new)
    if offset_of is None:
        assert data[18:20] == old
        data[18:20] = new
    else:
        assert offset_of in data
        # Static CPython can also carry these strings outside the dynamic version table.
        data = data.replace(offset_of, offset_of.replace(old, new))
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "python").write_bytes(data)

    result = subprocess.run([sys.executable, str(SCRIPT), str(tree)], capture_output=True, text=True)

    assert result.returncode == 1
    assert message in result.stderr
