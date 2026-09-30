#!/usr/bin/env python3
"""Build the existing Imp relay as a Tauri-compatible sidecar binary."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "services" / "relay"
OUTPUT = ROOT / "dist" / "desktop-node"
RAW = OUTPUT / "raw"
WORK = OUTPUT / "work"
SPEC = OUTPUT / "spec"
TAURI_BINARIES = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"


def command_output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    host_triple = command_output("rustc", "--print", "host-tuple")
    target_triple = os.environ.get("TAURI_TARGET_TRIPLE", host_triple)

    if target_triple != host_triple:
        raise SystemExit(
            "PyInstaller must build on the target platform: "
            f"host={host_triple}, target={target_triple}"
        )

    for path in (RAW, WORK, SPEC):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "uv",
            "run",
            "pyinstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "imp-node",
            "--distpath",
            str(RAW),
            "--workpath",
            str(WORK),
            "--specpath",
            str(SPEC),
            str(RELAY / "sidecar_entry.py"),
        ],
        cwd=RELAY,
        check=True,
    )

    extension = ".exe" if os.name == "nt" else ""
    source = RAW / f"imp-node{extension}"

    if not source.is_file():
        raise SystemExit(f"PyInstaller did not produce {source}")

    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    destination = TAURI_BINARIES / f"imp-node-{target_triple}{extension}"
    shutil.copy2(source, destination)

    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)

    print(destination)


if __name__ == "__main__":
    main()
