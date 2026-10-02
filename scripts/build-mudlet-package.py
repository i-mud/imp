#!/usr/bin/env python3
"""Build the Imp Mudlet package and its shared native helper."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUDLET = ROOT / "integrations" / "mudlet"
LUA_SOURCE = MUDLET / "lua" / "imp.lua"

OUTPUT = ROOT / "dist" / "mudlet"
RAW = OUTPUT / "raw"
WORK = OUTPUT / "work"
SPEC = OUTPUT / "spec"
PROJECT = OUTPUT / "project"
DESKTOP_RESOURCE = ROOT / "apps" / "desktop" / "src-tauri" / "resources" / "mudlet"

HELPER_NAME = "imp-mudlet-helper"
RUNTIME_NAME = "imp-mudlet-runtime"
PACKAGE_NAME = "Imp.mpackage"
PACKAGE_OUTPUT = OUTPUT / PACKAGE_NAME

MUDDLER_IMAGE = os.environ.get(
    "MUDDLER_IMAGE",
    "demonnic/muddler:1.1.0",
)


def normalized_machine() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)


def platform_slug() -> str:
    system = platform.system().lower()
    if system == "darwin":
        system = "macos"
    return f"{system}-{normalized_machine()}"


def project_version() -> str:
    with (MUDLET / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def reset(path: Path) -> None:
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise SystemExit(f"cannot reset build directory {path}: {exc}") from exc

    path.mkdir(parents=True, exist_ok=True)


def freeze_helper() -> tuple[Path, Path]:
    for path in (RAW, WORK, SPEC):
        reset(path)

    subprocess.run(
        [
            "uv",
            "run",
            "--python",
            "3.12",
            "pyinstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--contents-directory",
            RUNTIME_NAME,
            "--name",
            HELPER_NAME,
            "--distpath",
            str(RAW),
            "--workpath",
            str(WORK),
            "--specpath",
            str(SPEC),
            str(MUDLET / "src" / "imp_mudlet" / "bridge.py"),
        ],
        cwd=MUDLET,
        check=True,
    )

    extension = ".exe" if os.name == "nt" else ""
    bundle = RAW / HELPER_NAME
    executable = bundle / f"{HELPER_NAME}{extension}"
    runtime = bundle / RUNTIME_NAME

    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not produce {executable}")

    if not runtime.is_dir():
        raise SystemExit(f"PyInstaller did not produce {runtime}")

    return executable, runtime


def stage_muddler_project() -> None:
    reset(PROJECT)

    scripts = PROJECT / "src" / "scripts" / "Imp"
    scripts.mkdir(parents=True)

    (PROJECT / "mfile").write_text(
        json.dumps(
            {
                "package": "Imp",
                "version": project_version(),
                "author": "i-mud",
                "title": "Imp - Interactive MUD Peripheral",
                "description": (
                    "Mudlet integration for Imp, a cross-platform MUD "
                    "companion for live state, alerts, and trusted actions."
                ),
                "outputFile": True,
            },
            indent=2,
        )
        + "\n"
    )

    (scripts / "scripts.json").write_text(
        json.dumps(
            [
                {
                    "name": "Imp",
                }
            ],
            indent=2,
        )
        + "\n"
    )

    lua = LUA_SOURCE.read_text()
    if not lua.endswith("\n"):
        lua += "\n"

    # Imp.start() performs its own deferred focus reconciliation after the
    # package script has loaded.
    lua += """
-- Installed package startup.
Imp.start()
"""

    (scripts / "Imp.lua").write_text(lua)


def build_package() -> Path:
    command = [
        "docker",
        "run",
        "--rm",
    ]

    if os.name != "nt":
        command.extend(
            [
                "--user",
                f"{os.getuid()}:{os.getgid()}",
            ]
        )

    command.extend(
        [
            "-v",
            f"{PROJECT.resolve()}:/workspace",
            "-w",
            "/workspace",
            MUDDLER_IMAGE,
        ]
    )

    subprocess.run(command, check=True)

    package = PROJECT / "build" / PACKAGE_NAME
    if not package.is_file():
        raise SystemExit(f"Muddler did not produce {package}")

    return package


def assemble_distribution(
    package: Path,
    executable: Path,
    runtime: Path,
) -> Path:
    destination = OUTPUT / platform_slug()
    reset(destination)

    shutil.copy2(package, destination / PACKAGE_NAME)
    shutil.copy2(executable, destination / executable.name)
    shutil.copytree(runtime, destination / RUNTIME_NAME)

    return destination


def stage_desktop_resource(
    executable: Path,
    runtime: Path,
    *,
    require_package: bool = False,
) -> Path:
    package = DESKTOP_RESOURCE / PACKAGE_NAME
    package_bytes = package.read_bytes() if package.is_file() else None

    if require_package and package_bytes is None:
        raise SystemExit(
            f"desktop Mudlet resource is missing {PACKAGE_NAME}"
        )

    if package.is_file():
        verify_package(package)

    reset(DESKTOP_RESOURCE)
    (DESKTOP_RESOURCE / ".gitkeep").touch()

    if package_bytes is not None:
        (DESKTOP_RESOURCE / PACKAGE_NAME).write_bytes(package_bytes)

    shutil.copy2(executable, DESKTOP_RESOURCE / executable.name)
    shutil.copytree(runtime, DESKTOP_RESOURCE / RUNTIME_NAME)

    return DESKTOP_RESOURCE


def verify_package(package: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())

    if "Imp.xml" not in names:
        raise SystemExit("package is missing Imp.xml")

    if "config.lua" not in names:
        raise SystemExit("package is missing config.lua")

    # The helper intentionally does not live inside the package. One native
    # helper is shared by every Mudlet profile using Imp.
    if any(name.startswith(HELPER_NAME) for name in names):
        raise SystemExit("native helper must not be embedded in the package")

    if any(name.startswith(RUNTIME_NAME) for name in names):
        raise SystemExit("native helper runtime must not be embedded in the package")


def verify_distribution(
    distribution: Path,
    executable_name: str,
) -> None:
    package = distribution / PACKAGE_NAME
    executable = distribution / executable_name
    runtime = distribution / RUNTIME_NAME

    if not package.is_file():
        raise SystemExit(f"distribution is missing {package}")

    if not executable.is_file():
        raise SystemExit(f"distribution is missing {executable}")

    if not runtime.is_dir() or not any(runtime.iterdir()):
        raise SystemExit(f"distribution is missing populated {runtime}")


def main() -> None:
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--desktop-resource",
        action="store_true",
        help="build and stage the helper bundled with the desktop app",
    )
    mode.add_argument(
        "--package-only",
        action="store_true",
        help="build only the platform-neutral Mudlet package",
    )

    parser.add_argument(
        "--require-package",
        action="store_true",
        help="fail desktop-resource staging unless Imp.mpackage is present",
    )

    args = parser.parse_args()

    if args.require_package and not args.desktop_resource:
        parser.error("--require-package requires --desktop-resource")

    OUTPUT.mkdir(parents=True, exist_ok=True)

    if args.package_only:
        stage_muddler_project()
        package = build_package()
        verify_package(package)
        shutil.copy2(package, PACKAGE_OUTPUT)
        print(PACKAGE_OUTPUT)
        return

    executable, runtime = freeze_helper()

    if args.desktop_resource:
        destination = stage_desktop_resource(
            executable,
            runtime,
            require_package=args.require_package,
        )
        print(destination)
        return

    stage_muddler_project()

    package = build_package()
    verify_package(package)

    distribution = assemble_distribution(package, executable, runtime)
    verify_distribution(distribution, executable.name)

    print(distribution)


if __name__ == "__main__":
    main()
