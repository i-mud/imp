#!/usr/bin/env python3
"""Release packaging gate: build a distinct fixture A from a temporary source copy, then run the
existing A/B server acceptance against it and the real release archive B.

Run with the prepared repo interpreter:
    services/relay/.venv/bin/python scripts/test-server-release.py ARCHIVE_B
A lives only in a temporary directory and is never published; the repository is not modified.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "services/relay"
FIXTURE_VERSION = "0.0.0+dev.a"
COPIED = ("package.json", "scripts/build-server-bundle.sh", "scripts/check-server-abi.py")
COPIED_TREES = ("services/relay", "integrations/common", "integrations/tinyfugue", "deploy")
SKIP = shutil.ignore_patterns(
    ".git", ".venv", "node_modules", "dist", "build", "target", "__pycache__", "*.egg-info",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
)  # fmt: skip


def die(message):
    raise SystemExit(f"error: {message}")


def stamp(path, pattern, replacement):
    text = path.read_text()
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        die(f"cannot stamp fixture source: {path}")
    path.write_text(text)


def member_bytes(archive, name):
    with tarfile.open(archive) as tar:
        names = [
            m.name
            for m in tar.getmembers()
            if m.name.endswith(f"/{name}") and m.name.count("/") == name.count("/") + 1
        ]
        if len(names) != 1:
            die(f"{archive.name}: expected exactly one {name}")
        return tar.extractfile(names[0]).read()


def main():
    if len(sys.argv) != 2:
        die(f"usage: {sys.argv[0]} ARCHIVE_B")
    archive_b = Path(sys.argv[1]).resolve()
    if not archive_b.is_file():
        die(f"archive not found: {archive_b}")

    pin = (RELAY / ".python-version").read_text().strip()
    prepared = Path(sys.prefix).resolve() == (RELAY / ".venv").resolve()
    if not prepared or f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}" != pin:
        die(f"run with the locked repo interpreter (CPython {pin}): {RELAY}/.venv/bin/python")

    version_b = member_bytes(archive_b, "VERSION").decode().strip()
    if version_b == FIXTURE_VERSION:
        die("release version collides with the fixture version")

    with tempfile.TemporaryDirectory(prefix="imp-release-gate-") as tmp:
        source = Path(tmp) / "source"
        for name in COPIED:
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, source / name)
        for name in COPIED_TREES:
            shutil.copytree(ROOT / name, source / name, symlinks=True, ignore=SKIP)

        package = source / "package.json"
        data = json.loads(package.read_text())
        data["version"] = FIXTURE_VERSION
        package.write_text(json.dumps(data, indent=2) + "\n")
        for project in ("services/relay", "integrations/common", "integrations/tinyfugue"):
            stamp(
                source / project / "pyproject.toml", r'^version = "[^"]+"', f'version = "{FIXTURE_VERSION}"'
            )
        # A harmless docstring change gives A distinct native embedded bytecode.
        stamp(
            source / "services/relay/server_entry.py",
            r'^"""Frozen Linux',
            '"""Release-gate fixture A. Frozen Linux',
        )

        dist_a = Path(tmp) / "dist"
        subprocess.run(
            ["bash", str(source / "scripts/build-server-bundle.sh")],
            check=True,
            env={**os.environ, "IMP_SERVER_DIST": str(dist_a)},
        )
        archives = sorted(dist_a.glob("imp-server-*-linux-x86_64.tar.gz"))
        if len(archives) != 1:
            die(f"expected one fixture archive, found {len(archives)}")
        archive_a = archives[0]

        if member_bytes(archive_a, "VERSION").decode().strip() != FIXTURE_VERSION:
            die("fixture A did not take the fixture version")
        native = [
            hashlib.sha256(member_bytes(a, "bin/imp-server")).hexdigest() for a in (archive_a, archive_b)
        ]
        if native[0] == native[1]:
            die("fixture A and release B have the same native executable")

        subprocess.run(
            ["bash", str(ROOT / "scripts/test-server-bundle.sh"), str(archive_a), str(archive_b)], check=True
        )


if __name__ == "__main__":
    main()
