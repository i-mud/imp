#!/usr/bin/env python3
"""Developer-only ABI gate: every unique ELF in a server bundle tree must run on the frozen baseline.

Baseline: x86_64 little-endian ELF64, loader /lib64/ld-linux-x86-64.so.2, GLIBC <= 2.36,
GLIBCXX <= 3.4.30, CXXABI <= 1.3.13, GCC <= 12.0.0, ZLIB <= 1.2.13, and DT_NEEDED limited to ordinary glibc
libraries, libz.so.1, baseline libstdc++/libgcc, or libraries shipped in the tree.
Prints credential-free JSON records on stdout; violations go to stderr with exit status 1.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LOADER = "/lib64/ld-linux-x86-64.so.2"
CEILINGS = {
    "GLIBC": (2, 36),
    "GLIBCXX": (3, 4, 30),
    "CXXABI": (1, 3, 13),
    "GCC": (12, 0, 0),
    "ZLIB": (1, 2, 13),
}
SYSTEM_LIBS = {
    "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1", "libutil.so.1",
    "libresolv.so.2", "libanl.so.1", "ld-linux-x86-64.so.2", "libz.so.1",
    "libstdc++.so.6", "libgcc_s.so.1",
}  # fmt: skip
TAG = re.compile(r"^(GLIBC|GLIBCXX|CXXABI|GCC|ZLIB)_(\d+(?:\.\d+)*)$")


def readelf(*args):
    result = subprocess.run(
        ["readelf", "-W", *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return result.stdout


def field(text, pattern):
    match = re.search(pattern, text)
    return match[1].strip() if match else None


def parse(header, program, dynamic, versions):
    """Build a record from readelf -h, -l, -d and -V output; only version *needs* count."""
    needs, current, in_needs = {}, None, False
    for line in versions.splitlines():
        if line.startswith("Version "):
            in_needs = line.startswith("Version needs section")
        elif in_needs and (match := re.search(r"\bFile: (\S+)", line)):
            current = needs.setdefault(match[1], [])
        elif in_needs and current is not None and (match := re.search(r"\bName: (\S+)", line)):
            current.append(match[1])
    return {
        "class": field(header, r"Class:\s+(.*)"),
        "data": field(header, r"Data:\s+(.*)"),
        "machine": field(header, r"Machine:\s+(.*)"),
        "interp": field(program, r"Requesting program interpreter: ([^\]]+)\]"),
        "soname": field(dynamic, r"\(SONAME\)\s+Library soname: \[([^\]]+)\]"),
        "needed": re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", dynamic),
        "version_needs": needs,
    }


def describe(path):
    return parse(readelf("-h", path), readelf("-l", path), readelf("-d", path), readelf("-V", path))


def violations(record, shipped):
    found = []
    if record["class"] != "ELF64" or "little endian" not in (record["data"] or ""):
        found.append(f"not little-endian ELF64: {record['class']}, {record['data']}")
    if record["machine"] != "Advanced Micro Devices X86-64":
        found.append(f"unsupported architecture: {record['machine']}")
    if record["interp"] not in (None, LOADER):
        found.append(f"unsupported loader: {record['interp']}")
    for lib in record["needed"]:
        if lib not in SYSTEM_LIBS and lib not in shipped:
            found.append(f"unsupported library: {lib}")
    for lib, tags in record["version_needs"].items():
        if lib not in SYSTEM_LIBS:
            if lib not in shipped:
                found.append(f"version needs from unsupported library: {lib}")
            continue
        for tag in tags:
            match = TAG.match(tag)
            if match is None:
                found.append(f"unsupported version requirement: {lib} {tag}")
            elif tuple(map(int, match[2].split("."))) > CEILINGS[match[1]]:
                found.append(f"version above baseline: {lib} {tag}")
    return found


def scan(tree):
    """Return (records, violations) for every unique-inode ELF below tree."""
    inodes = {}
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as f:
            if f.read(4) != b"\x7fELF":
                continue
        stat = path.stat()
        inodes.setdefault((stat.st_dev, stat.st_ino), []).append(path)

    records = [
        {"paths": [p.relative_to(tree).as_posix() for p in paths], **describe(paths[0])}
        for paths in inodes.values()
    ]
    shipped = {Path(p).name for r in records for p in r["paths"]} | {
        r["soname"] for r in records if r["soname"]
    }
    errors = []
    for record, paths in zip(records, inodes.values(), strict=True):
        record["sha256"] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
        errors += [f"{record['paths'][0]}: {v}" for v in violations(record, shipped)]
    if not records:
        errors.append("no ELF files found")
    return records, errors


def main(argv):
    if len(argv) != 2:
        raise SystemExit(f"usage: {argv[0]} BUNDLE_TREE")
    banner = readelf("-v").splitlines()[0]
    if not banner.startswith("GNU readelf"):
        raise SystemExit(f"GNU readelf is required, found: {banner}")
    records, errors = scan(Path(argv[1]))
    print(json.dumps({"readelf": banner, "elf": records}, indent=2, sort_keys=True))
    for error in errors:
        print(f"abi: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
