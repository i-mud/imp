"""Multicall entry point of the frozen Imp server runtime (bin/imp-server and its aliases)."""

from __future__ import annotations

import os
import string
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import version
from pathlib import Path

ENTRYPOINT = "imp-server"
RUNTIME_DIR = "imp-server-runtime"
ROLES = (
    "relay",
    "gateway",
    "feed",
    "feed-watch",
    "action-consumer",
    "direct-wss",
    "direct-edge",
    "public-verify",
)
ALIASES = tuple(f"imp-{role}" for role in ROLES)
_PACKAGES = ("imp-relay", "imp-adapter", "imp-tinyfugue")
_WEBSOCKETS_MAJOR = "16"  # matches imp-relay's websockets>=16.0,<17


def bundle_check(root: Path) -> None:
    """Require the tree to contain exactly the regular files SHA256SUMS lists, with no symlinks."""
    expected: set[str] = set()
    count = 0
    for raw in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            raise SystemExit("invalid SHA256SUMS entry")
        digest, name = parts
        name = name.removeprefix("*")
        relative_path = Path(name)
        if (
            len(digest) != 64
            or any(char not in string.hexdigits for char in digest)
            or not name
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or name == "SHA256SUMS"
        ):
            raise SystemExit(f"invalid SHA256SUMS entry: {raw!r}")
        expected.add(relative_path.as_posix())
        count += 1
    if count != len(expected):
        raise SystemExit("SHA256SUMS contains duplicate paths")

    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SystemExit(f"bundle contains an unexpected symlink: {relative}")
        if path.is_file():
            if relative != "SHA256SUMS":
                actual.add(relative)
        elif not path.is_dir():
            raise SystemExit(f"bundle contains an unsupported path type: {relative}")

    if actual != expected:
        raise SystemExit(
            "bundle contents do not match SHA256SUMS: "
            f"missing={sorted(expected - actual)!r} extra={sorted(actual - expected)!r}"
        )


def runtime_check(expected: str, bin_dir: Path) -> None:
    """Validate embedded package metadata and the fixed release entrypoints; executes no bundled code."""
    for package in _PACKAGES:
        actual = version(package)
        if actual != expected:
            raise SystemExit(f"{package}: expected version {expected}, installed {actual}")
    websockets = version("websockets")
    if websockets.split(".")[0] != _WEBSOCKETS_MAJOR:
        raise SystemExit(f"websockets: expected major {_WEBSOCKETS_MAJOR}, installed {websockets}")

    for name in (ENTRYPOINT, *ALIASES):
        path = bin_dir / name
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise SystemExit(f"Imp release is missing executable entrypoint: {path}")
    if (bin_dir / RUNTIME_DIR).is_symlink() or not (bin_dir / RUNTIME_DIR).is_dir():
        raise SystemExit(f"Imp release is missing its runtime directory: {bin_dir / RUNTIME_DIR}")


def main(roles: Mapping[str, Callable[[], object]], argv: Sequence[str] | None = None) -> None:
    """Run the role named by the alias basename, or by the first argument of imp-server."""
    args = list(sys.argv if argv is None else argv)
    name = os.path.basename(args[0]) if args else ""
    rest = args[1:]
    if name in ALIASES:
        role = name.removeprefix("imp-")
    elif name == ENTRYPOINT and rest:
        role, *rest = rest
    else:
        raise SystemExit(f"usage: {ENTRYPOINT} ROLE [ARGS...] | bundle-check ROOT | runtime-check VERSION")

    if name == ENTRYPOINT and role in ("bundle-check", "runtime-check"):
        if len(rest) != 1:
            raise SystemExit(f"usage: {ENTRYPOINT} {role} {'ROOT' if role == 'bundle-check' else 'VERSION'}")
        if role == "bundle-check":
            bundle_check(Path(rest[0]))
        else:
            runtime_check(rest[0], Path(os.path.realpath(sys.executable)).parent)
        return
    if role not in ROLES or role not in roles:
        raise SystemExit(f"unknown imp-server role: {role}")

    sys.argv = [f"imp-{role}", *rest]
    raise SystemExit(roles[role]())
